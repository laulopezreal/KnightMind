"""
Puzzle naming.

Why this exists
---------------
``puzzles.identity.generate_puzzle_title`` maps a motif to a title through a
seven-entry dict, so a corpus of any size has at most seven names — and because
``assign_primary_motif`` falls back to ``"blunder"`` whenever it recognises
nothing, most of them are "The Missed Win". See
``docs/puzzle-naming-and-metadata-plan.md`` §1.

This module replaces that with names composed from the puzzle's own facts. Two
rules shape everything here:

**The joke has to be true.** A name is not a gag attached to a puzzle; it is the
most absurd *real* fact about that puzzle, stated plainly. So the generator is a
salience picker over facts (:data:`_RULES`), not a joke list. A joke list would
rebuild the seven-name ceiling with better punchlines.

**Never joke about the tactic.** "The Fork Fiasco" is funnier than "The Fork"
and leaks exactly as much. Every fact used below comes from provenance or
damage — move number, opening, clock, castling, result — never from the motif or
the solution. Naming the tactic is what :func:`compose_insight_phrase` is for,
and that one is only ever rendered after the puzzle is resolved.

Uniqueness
----------
``puzzles`` has ``uq_puzzles_username_source_game_id_ply``, and
``generator._is_user_move`` means puzzles are only cut from the user's *own*
moves — so within one game the ply parity is fixed and no two puzzles can share
a move number. Every template below therefore carries the move number, which
makes the whole name space unique per game without any corpus query. That is a
deliberate trade: it costs some punchiness ("Bongcloud Attack Incident, move 12"
rather than "Bongcloud Incident") and buys the property the old titles lacked.

Purity
------
No DB access and no clock reads. Callers assemble :class:`PuzzleFacts`; this
module turns facts into strings and nothing else. That is what lets the whole
thing be table-tested and lets ``scripts/name_puzzles.py`` report a distribution
over the real corpus before any of it ships.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

# Opening *families* whose names are funny on their own. Chess does the work
# here; we only have to not get in the way.
#
# Verified against services/api/openings/eco.tsv — these are all real values of
# ``GameContext.opening_family`` (which is opening_name.split(":")[0]). Two
# obvious candidates are deliberately absent: "Fried Liver" and "Monkey's Bum"
# are sub-lines, not families (they live under "Italian Game" and "Spanish
# Game"), so listing them would add entries that can never match.
ABSURD_OPENINGS = frozenset(
    {
        "Amar Opening",
        "Anderssen's Opening",
        "Barnes Defense",
        "Barnes Opening",
        "Bongcloud Attack",
        "Borg Defense",
        "Clemenz Opening",
        "Creepy Crawly Formation",
        "Elephant Gambit",
        "English Orangutan",
        "Grob Opening",
        "Hippopotamus Defense",
        "Latvian Gambit",
        "Latvian Gambit Accepted",
        "Polish Defense",
        "Polish Opening",
        "Sodium Attack",
        "Ware Defense",
        "Ware Opening",
    }
)

# Fallback vocabulary, seeded on the puzzle id.
#
# Tone rule (plan §3.5): punch at the position, not the player. These are names
# for the user's own blunders, read at the moment they have just failed the same
# puzzle for the fourth time. Every entry names *the moment* — a Wobble, a
# Daydream — and none rates the person. "Idiotic", "Clueless" and friends are
# out, and test_naming enforces that no entry contains a second-person pronoun.
EPITHETS = (
    "Blink",
    "Brainfog",
    "Bungle",
    "Clanger",
    "Daydream",
    "Detour",
    "Doze",
    "Drift",
    "Faceplant",
    "Flub",
    "Fumble",
    "Glitch",
    "Gremlin",
    "Hiccup",
    "Lapse",
    "Mirage",
    "Mishap",
    "Misfire",
    "Muddle",
    "Nap",
    "Oversight",
    "Pratfall",
    "Scramble",
    "Shrug",
    "Sigh",
    "Skid",
    "Slip",
    "Snag",
    "Stumble",
    "Swerve",
    "Tangle",
    "Tripwire",
    "Tumble",
    "Wander",
    "Waver",
    "Wobble",
    "Yawn",
)

_NUMBER_WORDS = (
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
)

# Hard cap, applied in the generator rather than by CSS. A Library row already
# carries status, difficulty, cause and colour; a name that arrives pre-elided
# is not a name, so an over-long candidate loses to the next rule instead of
# being truncated. See plan F8.
NAME_MAX_CHARS = 38


@dataclass(frozen=True)
class PuzzleFacts:
    """Everything naming is allowed to know about a puzzle.

    Deliberately *not* the ORM row. Only ``puzzle_id`` and ``ply`` are required;
    every other field is optional because every one of them is genuinely absent
    on some real row — a game imported without its PGN has no clock or castling
    data, and an undiagnosed puzzle has no opening or phase. The salience ladder
    is built so that missing facts cost variety, never correctness.

    Note what is *not* here: motif, cause, best move, solution line. Naming
    cannot leak the answer because naming cannot see it.
    """

    puzzle_id: str
    ply: int

    # From ``games``.
    opponent: str | None = None
    user_won: bool | None = None
    time_control_class: str | None = None  # bullet | blitz | rapid | None

    # From the PGN replay (``diagnosis.pgn_context``). Absent when the game was
    # imported without ``pgn_blob`` or the PGN carries no clock tags.
    move_time_seconds: float | None = None
    user_castled: bool | None = None

    # From ``puzzle_diagnoses``. Absent until the diagnosis job reaches the row.
    opening_family: str | None = None
    opening_name: str | None = None
    phase: str | None = None

    # Manual puzzles are named by the user; see ``compose_name``.
    is_manual: bool = False


def move_number(ply: int) -> int:
    """Chess move number for a 1-based mainline ply.

    ``ply`` is 1-based: ``generator`` starts at 0 and increments *before* use,
    and ``pgn_context.collect`` documents it as "1-based mainline index". So
    plies 1 and 2 are both move 1.
    """
    return (ply + 1) // 2


def _seed(puzzle_id: str) -> int:
    """Stable integer seed for a puzzle id.

    ``zlib.crc32`` rather than ``hash()`` on purpose. Python salts ``str``
    hashing per process (``PYTHONHASHSEED``), so ``hash()`` returns a different
    value in every API worker and after every restart — the same puzzle would be
    renamed on each deploy. crc32 is stable across processes, releases and
    platforms. See plan F1; ``test_naming`` checks this across a real subprocess
    boundary, because an in-process assertion passes against the broken version.
    """
    return zlib.crc32(puzzle_id.encode("utf-8"))


def _epithet(puzzle_id: str) -> str:
    return EPITHETS[_seed(puzzle_id) % len(EPITHETS)]


def _number_word(n: int) -> str:
    return _NUMBER_WORDS[n] if 0 <= n < len(_NUMBER_WORDS) else str(n)


# --- Salience rules -------------------------------------------------------
#
# Each rule returns a name or None. The highest salience that fires wins; ties
# break on the puzzle id seed so the choice is deterministic. Rules are ordered
# by how absurd the fact they key on actually is, not by how easy it is to
# compute.


def _rule_absurd_opening(f: PuzzleFacts) -> str | None:
    if f.opening_family in ABSURD_OPENINGS:
        return f"{f.opening_family} Incident, move {move_number(f.ply)}"
    return None


def _rule_snap_decision(f: PuzzleFacts) -> str | None:
    """Blundered almost instantly — in a game that gave time to think.

    Excluded for bullet (where three seconds is the whole game, so it is not a
    punchline) and for correspondence, where ``classify_time_control`` returns
    None and a move time can be measured in days.
    """
    if f.move_time_seconds is None or f.time_control_class in (None, "bullet"):
        return None
    if f.move_time_seconds <= 3:
        secs = _number_word(int(f.move_time_seconds))
        return f"{secs} Seconds of Thought, move {move_number(f.ply)}"
    return None


def _rule_long_think(f: PuzzleFacts) -> str | None:
    """Spent real time on it and blundered anyway. The funnier direction."""
    if f.move_time_seconds is None or f.time_control_class is None:
        return None
    if f.move_time_seconds >= 120:
        mins = _number_word(int(f.move_time_seconds // 60))
        return f"{mins} Minutes For This, move {move_number(f.ply)}"
    return None


def _rule_late_move(f: PuzzleFacts) -> str | None:
    n = move_number(f.ply)
    return f"Move {n} Meltdown" if n >= 50 else None


def _rule_early_move(f: PuzzleFacts) -> str | None:
    n = move_number(f.ply)
    return f"Already? Move {n}" if n <= 8 else None


def _rule_won_anyway(f: PuzzleFacts) -> str | None:
    """A blunder in a game that was still won. Earned comedy, no sting."""
    return f"Won Anyway, move {move_number(f.ply)}" if f.user_won else None


def _rule_uncastled(f: PuzzleFacts) -> str | None:
    n = move_number(f.ply)
    if f.user_castled is False and n >= 20:
        return f"King Still At Home, move {n}"
    return None


def _rule_bullet(f: PuzzleFacts) -> str | None:
    if f.time_control_class == "bullet":
        return f"Bullet, Obviously, move {move_number(f.ply)}"
    return None


def _rule_endgame(f: PuzzleFacts) -> str | None:
    if f.phase == "endgame":
        return f"Endgame {_epithet(f.puzzle_id)}, move {move_number(f.ply)}"
    return None


def _rule_floor(f: PuzzleFacts) -> str:
    """Always fires. Carries the move number, so it is unique per game.

    This is the rule that decides whether the feature works. If most of a corpus
    lands here the Library reads "The Move 24 Shrug / The Move 31 Wobble" — the
    original complaint wearing a costume. ``scripts/name_puzzles.py --dry-run``
    reports exactly that share; see plan F2.
    """
    return f"The Move {move_number(f.ply)} {_epithet(f.puzzle_id)}"


# (salience, name, rule). Salience is the comic weight of the *fact*, so a rare
# fact outranks a common one even when both fire.
_RULES: tuple[tuple[int, str, object], ...] = (
    (5, "absurd_opening", _rule_absurd_opening),
    (5, "snap_decision", _rule_snap_decision),
    (4, "long_think", _rule_long_think),
    (4, "late_move", _rule_late_move),
    (4, "early_move", _rule_early_move),
    (4, "won_anyway", _rule_won_anyway),
    (3, "uncastled", _rule_uncastled),
    (3, "bullet", _rule_bullet),
    (2, "endgame", _rule_endgame),
)


def select_rule(facts: PuzzleFacts) -> tuple[str, str]:
    """Return ``(rule_name, name)`` for the winning salience rule.

    Exposed separately from :func:`compose_name` so the dry-run CLI can report
    which rung each puzzle landed on without re-deriving it.
    """
    best: tuple[int, int, str, str] | None = None
    seed = _seed(facts.puzzle_id)

    for salience, rule_name, rule in _RULES:
        candidate = rule(facts)  # type: ignore[operator]
        if candidate is None or len(candidate) > NAME_MAX_CHARS:
            # Over-long candidates lose to the next rule rather than being
            # elided — see NAME_MAX_CHARS.
            continue
        # Tie-break on the seed mixed with the rule name, so two equally
        # salient rules resolve deterministically but not always the same way
        # round across the corpus.
        tie = zlib.crc32(rule_name.encode("utf-8")) ^ seed
        key = (salience, tie, rule_name, candidate)
        if best is None or key[:2] > best[:2]:
            best = key

    if best is None:
        return "floor", _rule_floor(facts)
    return best[2], best[3]


def compose_name(
    facts: PuzzleFacts,
    register: str = "playful",
    user_title: str | None = None,
) -> str:
    """Compose a puzzle's display name.

    Args:
        facts: What naming is allowed to know.
        register: ``"playful"`` (default) or ``"plain"``. Same facts, same
            extraction pass, two output registers — not two naming systems. The
            plain register also serves users for whom the English wordplay does
            not land.
        user_title: A name the user chose. Always wins; ``PuzzleStats.title``
            means "the name the user picked, NULL otherwise" and a generated
            name must never overwrite one.

    Returns:
        A non-empty name. There is no failure mode that yields "Puzzle": the
        floor rule fires on ``ply`` alone, which is NOT NULL.
    """
    if user_title:
        return user_title
    if facts.is_manual:
        # No source game to be funny about, and manual puzzles are titled at
        # creation. Falling through would name it after a ply that means
        # nothing for a hand-entered position.
        return f"Saved position {facts.puzzle_id[:8]}"
    if register == "plain":
        return _compose_plain(facts)
    return select_rule(facts)[1]


def _compose_plain(facts: PuzzleFacts) -> str:
    """The dry register: the same facts, no wordplay.

    First template whose facts are present wins. Every rung is a real name, so
    missing opening data degrades the name rather than emptying it.
    """
    n = move_number(facts.ply)
    if facts.opening_name:
        return f"{facts.opening_name} · move {n}"
    if facts.opening_family:
        return f"{facts.opening_family} · move {n}"
    if facts.phase and facts.opponent:
        return f"{facts.phase.title()} vs {facts.opponent} · move {n}"
    if facts.opponent:
        return f"Move {n} vs {facts.opponent}"
    return f"Move {n}"


# Thresholds at which a puzzle has earned a nickname. fail_count is the best
# comic material in the schema because it is about persistence, not stupidity.
# Applied as a suffix to a name that never changes: the root stays the identity,
# the badge is won. Highest matching threshold wins.
_NICKNAMES: tuple[tuple[int, str], ...] = (
    (6, "arch-nemesis"),
    (3, "nemesis"),
)


def earned_suffix(fail_count: int) -> str:
    """Return ``" (nemesis)"``-style decoration, or ``""``.

    Kept separate from :func:`compose_name` so the stored, searchable name never
    contains it — a name that changes as you fail is a broken identity.
    """
    for threshold, label in _NICKNAMES:
        if fail_count >= threshold:
            return f" ({label})"
    return ""


# --- The other name -------------------------------------------------------

# Motif -> what the solver missed, as a sentence fragment.
#
# Yes, this is a small map keyed on motif, which is what ``MOTIF_TITLES`` was.
# The difference is what it is *for*. MOTIF_TITLES' sin was being used as
# identity, where seven values across a whole corpus is a defect. As a
# post-resolution description of the tactic it is correct that there are only a
# handful of values — there really are only a handful of tactics. Identity is
# ``compose_name``'s job; this is the teaching line.
_INSIGHT_PHRASES = {
    "back_rank": "Missed a back-rank mate",
    "hanging_queen": "Missed a hanging queen",
    "hanging_piece": "Missed a loose piece",
    "fork": "Missed a fork",
    "pin": "Missed a pin",
    "mate_threat": "Missed a forced mate",
}


def compose_insight_phrase(motif: str | None, cause: str | None) -> str | None:
    """What the solver missed. **Post-resolution surfaces only.**

    This is the half of the design that is allowed to name the tactic, which is
    exactly why it must never render while ``status == 'solving'``. See plan §4.

    Deadpan on purpose: it is the teaching line, read straight after a failure,
    and the puzzle's *name* already carried the joke.

    Returns None when there is neither a usable motif nor a cause — "we could
    not work out what happened" is an honest state that ``DiagnosisResponse``
    already models as ``unclear``, and inventing "The Missed Win" to fill the
    gap is the original bug.
    """
    from services.api.diagnosis.clusters import (
        humanise_cause,
        humanise_motif,
        usable_motif,
    )

    real_motif = usable_motif(motif)
    if real_motif:
        key = real_motif.strip().lower()
        return _INSIGHT_PHRASES.get(key) or f"Missed a {humanise_motif(real_motif)}"
    if cause:
        return humanise_cause(cause)
    return None
