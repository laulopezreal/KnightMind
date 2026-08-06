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
    # The move they played — the mistake, not the solution. The winning move
    # is deliberately absent: the name sits beside a board the user is about
    # to solve, and a prompt that does not contain the answer cannot leak it.
    played_move_san: str
    move_number: int | None = None
    phase: str | None = None
    opening_name: str | None = None
    move_time_seconds: float | None = None
    user_won: bool | None = None

    # NEVER RENDERED. The winning move's destination square, carried only so
    # the gate can reject a name that arrives at it anyway — the model can see
    # the FEN and could still work the tactic out. ``build_user_prompt`` must
    # not touch this; there is a test asserting it never reaches the prompt.
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
    tokens = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }

    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        # Checked before reading content: a refused response has none.
        return NameOutcome(
            REJECTED, reason="refusal", model_version=model_version, **tokens
        )
    if stop == "max_tokens":
        # Truncated JSON is not partially usable, and a truncated *name* would
        # be worse than none — it reads as a bug, not a joke.
        return NameOutcome(
            REJECTED, reason="truncated", model_version=model_version, **tokens
        )

    raw = _first_text(response)
    if not raw:
        return NameOutcome(
            REJECTED, reason="empty_response", model_version=model_version, **tokens
        )

    try:
        parsed = PuzzleName.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        return NameOutcome(
            REJECTED,
            reason=f"schema:{exc.__class__.__name__}",
            raw_response=raw,
            model_version=model_version,
            **tokens,
        )

    violation = _validate(parsed, facts)
    if violation:
        logger.info("AI name rejected: %s", violation)
        return NameOutcome(
            REJECTED,
            reason=violation,
            raw_response=raw,
            model_version=model_version,
            **tokens,
        )

    return NameOutcome(
        ACCEPTED,
        name=parsed.name,
        raw_response=raw,
        model_version=model_version,
        **tokens,
    )


def _first_text(response) -> str | None:
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            text = (getattr(block, "text", "") or "").strip()
            if text:
                return text
    return None


# Words that name the tactic. Any of these is most of the answer handed over
# before the user has looked at the board.
_SPOILER_WORDS = frozenset(
    {
        "fork",
        "forks",
        "forked",
        "pin",
        "pins",
        "pinned",
        "skewer",
        "skewered",
        "mate",
        "mates",
        "mating",
        "checkmate",
        "hanging",
        "hangs",
        "hung",
        "loose",
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

    # The failure the first trial run actually produced: 20 of 20 names were a
    # move and its point joined by a comma ("Check on h5, d7 Was the Fork").
    # Every one passed the rest of this gate, which is why 100% acceptance was
    # not the good news it looked like. A title has one clause.
    if "," in name:
        return "name_has_two_clauses"

    # The name is shown beside a board the user is about to solve. The prompt
    # is not told the winning move, but the model can see the FEN and work it
    # out, so refusing the answer is enforced here rather than trusted.
    words = {w.strip(".!?'\"").lower() for w in name.split()}

    if facts.answer_square and facts.answer_square.lower() in words:
        return f"name_reveals_answer_square:{facts.answer_square}"

    spoilers = words & _SPOILER_WORDS
    if spoilers:
        return f"name_reveals_tactic:{','.join(sorted(spoilers))}"

    return None
