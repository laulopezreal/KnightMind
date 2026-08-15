"""The model call for puzzle naming, and the gate on what comes back.

Holds no database access: facts in, outcome out, so every rejection path is
testable without a session. Budget accounting and audit persistence belong to
the caller (see ``scripts/ai_name_puzzles.py``), exactly as they do for
diagnosis in ``diagnosis/job.py``.

Never raises. Disabled, unkeyed, refused, malformed, unreachable — every one
returns an outcome the caller degrades from by falling back to
``position_names.compose_position_name``. A puzzle always ends up with a name.
"""

import json
import logging
from dataclasses import dataclass

import chess
from pydantic import ValidationError

from services.api.ai import config
from services.api.puzzles.naming_prompts import SYSTEM_PROMPT, build_user_prompt
from services.api.puzzles.naming_schema import (
    MAX_NAME_CHARS,
    RESPONSE_SCHEMA,
    PuzzleName,
)

logger = logging.getLogger(__name__)

# Outcome statuses, matching ai/client.py so the audit log reads the same way
# for both kinds of call.
ACCEPTED = "accepted"
REJECTED = "rejected"  # the model answered, but the answer failed the gate
SKIPPED = "skipped"  # no call attempted (flag off, no key, no usable facts)
ERROR = "error"  # the call itself failed (network, auth, overload)

# What this module writes into the audit log's discriminator column.
CALL_TYPE = "naming"


@dataclass(frozen=True)
class NameFacts:
    """The facts naming is allowed to see.

    There is deliberately no field for a username or an opponent handle. The
    prompt is rendered only from this object, so the "no identity reaches the
    model" property is enforced by the type rather than by remembering.
    """

    fen: str
    # The mistake.
    played_move_san: str
    # The solution. Deliberately given to the model — withholding it made the
    # names measurably worse without making them safer, because the model just
    # latched onto whichever fact was left. See naming_prompts' docstring for
    # the three measured attempts. ``_validate`` is what keeps it out of the
    # name, which is why the gate's rules are tested one by one.
    best_move_san: str | None = None
    primary_motif: str | None = None
    move_number: int | None = None
    phase: str | None = None
    opening_name: str | None = None
    move_time_seconds: float | None = None
    user_won: bool | None = None

    # Gate input: the square the winning move lands on, so a name that arrives
    # at it is rejected. Not a secret — ``best_move_san`` already names it —
    # just the parsed form the check needs.
    answer_square: str | None = None


@dataclass(frozen=True)
class NameOutcome:
    status: str
    reason: str | None = None
    name: str | None = None
    raw_response: str | None = None
    model_version: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def usable(self) -> bool:
        return self.status == ACCEPTED and bool(self.name)


def san_or_uci(fen: str, uci: str) -> str:
    """Render a UCI move in SAN, falling back to the raw UCI.

    SAN is what a player reads, and the model names better from ``Nxf7`` than
    from ``g5f7``. A move that will not parse against the FEN is passed through
    rather than raising — one malformed row must not fail a whole backfill.
    """
    try:
        board = chess.Board(fen)
        return board.san(chess.Move.from_uci(uci))
    except (ValueError, AssertionError, TypeError):
        return uci


def name_puzzle(facts: NameFacts, avoid: list[str] | None = None) -> NameOutcome:
    """Ask the model for one puzzle's name. Never raises."""
    if not config.naming_is_enabled():
        return NameOutcome(SKIPPED, reason="disabled")

    key = config.api_key()
    if not key:
        # Not an error: the service runs fine without a key and names
        # deterministically instead.
        return NameOutcome(SKIPPED, reason="no_api_key")

    if not facts.fen or not facts.played_move_san:
        # Nothing concrete to name from. Asking anyway invites the model to
        # invent a position, which is the one thing the design forbids.
        return NameOutcome(SKIPPED, reason="no_position")

    try:
        response = _call(key, facts, avoid)
    except Exception as exc:  # noqa: BLE001 - all provider failures degrade alike
        logger.warning("AI naming call failed: %s", exc.__class__.__name__)
        return NameOutcome(ERROR, reason=f"{exc.__class__.__name__}")

    return _interpret(response, facts)


def _call(key: str, facts: NameFacts, avoid: list[str] | None):
    # Imported from the diagnosis client so there is one SDK client pool and
    # one place that knows how to build it.
    from services.api.ai.client import _client

    return _client(key).beta.messages.create(
        model=config.NAMING_MODEL,
        max_tokens=config.NAMING_MAX_TOKENS,
        # Same server-side fallback as diagnosis: a false-positive policy
        # decline should cost us the joke, not the name.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": config.NAMING_EFFORT,
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        },
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                # Byte-identical on every call, so it caches across a backfill.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": build_user_prompt(facts, avoid)}],
    )


def _interpret(response, facts: NameFacts) -> NameOutcome:
    model_version = getattr(response, "model", config.NAMING_MODEL)
    usage = getattr(response, "usage", None)
    # Passed explicitly at every call site below rather than splatted as
    # **tokens: mypy maps a splatted dict onto parameters positionally and
    # cannot see that these two land on the int fields, so it reports each
    # construction as an arg-type error.
    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(usage, "output_tokens", 0) or 0)

    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        # Checked before reading content: a refused response has none.
        return NameOutcome(
            REJECTED,
            reason="refusal",
            model_version=model_version,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
    if stop == "max_tokens":
        # Truncated JSON is not partially usable, and a truncated *name* would
        # be worse than none — it reads as a bug, not a joke.
        return NameOutcome(
            REJECTED,
            reason="truncated",
            model_version=model_version,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    raw = _first_text(response)
    if not raw:
        return NameOutcome(
            REJECTED,
            reason="empty_response",
            model_version=model_version,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    try:
        parsed = PuzzleName.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        return NameOutcome(
            REJECTED,
            reason=f"schema:{exc.__class__.__name__}",
            raw_response=raw,
            model_version=model_version,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    violation = _validate(parsed, facts)
    if violation:
        logger.info("AI name rejected: %s", violation)
        return NameOutcome(
            REJECTED,
            reason=violation,
            raw_response=raw,
            model_version=model_version,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    return NameOutcome(
        ACCEPTED,
        name=parsed.name,
        raw_response=raw,
        model_version=model_version,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


def _first_text(response) -> str | None:
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            text = (getattr(block, "text", "") or "").strip()
            if text:
                return text
    return None


# Words that state the tactic outright. The model is told the motif and should
# use it to choose an angle; naming it is what turns a title back into a label.
#
# Not a spoiler argument. The solving page renders `primary_motif` as a badge
# beside the title anyway (Puzzles.tsx, desktop header and mobile context bar,
# neither gated on resolution), so this protects nothing the UI does not
# already give away. It is a quality rule: "Queen Retreated From a Fork" says
# less about this position than "Queen Backed Off Too Soon", because the fork
# is the one thing every puzzle of that motif shares.
_TACTIC_WORDS = frozenset(
    {
        "fork",
        "forks",
        "forked",
        "forking",
        "pin",
        "pins",
        "pinned",
        "pinning",
        "skewer",
        "skewers",
        "skewered",
        "mate",
        "mates",
        "mated",
        "mating",
        "checkmate",
        "hanging",
        "hangs",
        "hung",
        "loose",
        "free",
        "check",
        "checks",
        "checked",
    }
)


def _validate(parsed: PuzzleName, facts: NameFacts) -> str | None:
    """The gate. Returns a rejection reason, or None when the name is usable.

    Thin by design. A name has no ground truth to check it against, so this
    covers only what would be visibly broken in the library card. The Pydantic
    model already enforced length, single-line, and contains-letters; what is
    left is the two failure modes that satisfy the schema and are still wrong.
    """
    name = parsed.name

    if len(name) > MAX_NAME_CHARS:
        # The schema bounds this, but a future schema edit should not be able to
        # silently widen the card.
        return "name_too_long"

    # A model that narrates instead of naming produces "1. e4 Nf6 2. d4" — it
    # satisfies every other rule and is not a title.
    if any(token in name for token in ("1.", "2.", "...")):
        return "name_is_move_list"

    # A blanket comma ban used to live here. It was aimed at the first trial
    # run's failure — 20 of 20 names were a move and its point joined by a
    # comma ("Check on h5, d7 Was the Fork") — but it treated punctuation as
    # the disease rather than the symptom.
    #
    # The actual cause was the prompt carrying both moves, which read as two
    # things to name; removing it, capping the length, and showing the failure
    # back to the model is what fixed the shape. The ban then outlived its
    # cause and started producing run-ons instead: "Rook Shuffled Bishop
    # Lived", "Wrong Check Right Queen" — the model still wanted two clauses
    # and simply jammed them together without punctuation.
    #
    # So one comma is allowed and two are not. Two commas is a list, and a list
    # is the summary shape returning by another route.
    if name.count(",") > 1:
        return "name_is_a_list"

    words = {w.strip(".!?'\"").lower() for w in name.split()}

    # The square is the hard spoiler: "Nf7 Was There" hands over the move.
    #
    # Matched as a SUBSTRING of the whole name, not against the tokens above.
    # Tokenising strips only `.!?'"`, so every other way of writing the square
    # walked straight through — measured, all of these were accepted against an
    # answer square of f7:
    #
    #     Nf7 Was There        (the SAN prefix — this docstring's own example)
    #     Knight to f7, Finally  (one comma is legal, and `,` is not stripped)
    #     Knight to f7's Square
    #     Knight (f7) Waited
    #     The e5f7 Moment      (san_or_uci falls back to raw UCI)
    #
    # A substring test has no false positives to trade against: a square is a
    # letter a-h followed by a digit 1-8, and no English word contains a
    # letter-digit pair. Anything spelling "f7" in a chess puzzle's name is the
    # square, whatever punctuation or piece letter is welded to it.
    if facts.answer_square and facts.answer_square.lower() in name.lower():
        return f"name_reveals_answer_square:{facts.answer_square}"

    # Naming the tactic is refused again, but for a different reason than the
    # first time and with the input left alone.
    #
    # The first attempt withheld the tactic from the PROMPT, which starved the
    # model: it fell back on the clock (13 of 40 names became "N Seconds of…"),
    # then on the played move (11 of 19 became "<Piece> <verb> to <square>").
    # Relaxing it produced fluent names that mostly said the tactic outright —
    # 12 of 20 on the first production batch, including "Corner Check Wins the
    # Rook", which states what the move achieves.
    #
    # So the model still receives the tactic and uses it to choose an angle; it
    # just may not state it. That combination measured best of the three
    # variants tried, and it is the one arrangement not yet shipped.
    tactics = words & _TACTIC_WORDS
    if tactics:
        return f"name_states_the_tactic:{','.join(sorted(tactics))}"

    return None
