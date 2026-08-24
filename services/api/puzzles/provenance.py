"""Provenance: the label that identifies a puzzle without spoiling it.

A puzzle's single ``title`` was asked to do two jobs that pull apart (see
``docs/design-puzzle-identity-and-modes.md`` §2): *identify* it before the
attempt, and *aid recall* after. Good recall means distinctive, and distinctive
means talking about what happened, which is the answer.

Provenance is the identify half. It says where a puzzle came from -- ``12 Mar ·
Sicilian · move 18`` -- and reveals nothing about the tactic, so it is safe to
serve before an attempt. It costs no model call and is derived, never stored.

The base is **date + move number**, deliberately, because those are the only two
facts every puzzle has at insert time. ``puzzles.source_game_id`` is a real FK
so every puzzle has a game, and every game has an ``end_time`` column. The
opening is *added when a diagnosis row exists*, never depended upon: diagnosis
is asynchronous, so a puzzle generated a minute ago has no opening even on a
fully backfilled corpus.

Measured on production 2026-08-15, ``(date, opening, move)`` is distinct for
318/318 puzzles on one tenant and 28/30 on the other; the remaining collisions
fall through to the ``(source_game_id, ply)`` tiebreak, which is unique per user
by existing constraint. This module composes the label; it does not disambiguate
-- see the caller for that.
"""

from datetime import datetime, timezone

# Chess convention: ply 0 and ply 1 are both "move 1" (White then Black).
PLY_PER_MOVE = 2

SEPARATOR = " · "


def _move_number(ply: int | None) -> int:
    """Full-move number from a half-move index, matching the BOARD.

    ``(ply + 1) // 2``, which is what ``diagnosis/evidence.py:435`` computes --
    the only copy in the repo derived from an actual ``chess.Board``, and the
    one whose output the diagnosis panel prints as "Move number".

    The first version used ``ply // 2 + 1``, copied from ``identity.py`` and
    ``spaced_repetition.py``, and was off by one for every EVEN ply. Puzzles
    store ply 1-based (``generator.py`` increments before examining the move),
    so ply 36 is Black's half of move 18; the old formula called it move 19.
    Verified against python-chess: at ply 36 ``board.fullmove_number`` is 18.

    That is roughly half the corpus -- every puzzle from a game the user played
    as Black -- and after rollout step 6 this string IS the puzzle's name, so
    the Library would have said "move 19" directly above a diagnosis card
    saying "Move number 18".
    """
    return max(1, ((ply or 0) + 1) // PLY_PER_MOVE)


def _format_date(end_time: int | None) -> str | None:
    """``12 Mar`` from a Unix timestamp, or None when there is no real date.

    Returns None rather than a formatted epoch for the falsy cases, and the
    zero case is not hypothetical: ``POST /puzzles/manual`` inserts a synthetic
    game with ``end_time=0``, so formatting blindly would label every manual
    puzzle "1 Jan 1970". A missing date drops that component instead, which
    reads as ``move 18`` rather than as a lie about when the game happened.
    """
    if not end_time or end_time <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(end_time, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        # A corrupt timestamp must not take down a puzzle listing. Dropping the
        # component degrades the label; raising would 500 the whole route.
        return None
    # No leading zero on the day: "2 Mar", not "02 Mar". %-d is glibc-specific,
    # so the day is formatted by hand to keep this portable.
    return f"{moment.day} {moment.strftime('%b')}"


def compose_provenance(
    *,
    end_time: int | None,
    ply: int | None,
    opening_name: str | None = None,
) -> str:
    """Build the identify-half label. Never contains the solution.

    Components are joined only when present, so this degrades in one direction:
    ``12 Mar · Sicilian · move 18`` → ``12 Mar · move 18`` → ``move 18``. The
    move number is always available -- ``ply`` is NOT NULL on the puzzle -- so
    the result is never empty, which matters because it is the fallback for a
    puzzle with no nickname and an empty string would render as a blank row.

    ``opening_name`` is passed through as stored rather than shortened here.
    The stored value is already the display form used by the cause chips.
    """
    parts: list[str] = []

    formatted_date = _format_date(end_time)
    if formatted_date:
        parts.append(formatted_date)

    # Strip first, then test: a whitespace-only opening is truthy, so testing
    # the raw value appends an empty component and renders a dangling " · ".
    trimmed_opening = (opening_name or "").strip()
    if trimmed_opening:
        parts.append(trimmed_opening)

    parts.append(f"move {_move_number(ply)}")
    return SEPARATOR.join(parts)


def resolve_display_name(
    *,
    title: str | None,
    end_time: int | None,
    ply: int | None,
    opening_name: str | None = None,
    resolved: bool,
) -> str:
    """What the API serves as a puzzle's name: nickname when it has one.

    THIS is the one function every puzzle-returning route should call. Clients
    then never branch on "is there a title", and never receive a value they are
    trusted not to render. Both a Pydantic model and a bare ``dict`` payload can
    call it, which matters while ``/puzzles/due`` is still typed ``list[dict]``.

    ``resolved`` is REQUIRED, with no default, and that is the fix for how the
    first version of this gate leaked. It defaulted to True, so a route that
    forgot the gate and a route that had decided the puzzle was resolved were
    byte-identical -- and with the rollout flag off, both were also identical
    at runtime, so neither review nor the test suite could tell them apart.
    Three routes shipped ungated that way. A required keyword turns each of
    those into a TypeError at import time instead.

    ``resolved=False`` withholds the nickname and serves provenance instead --
    this is rollout step 3's gate (§4), and routing every surface through one
    function in step 1 is what made it a single change rather than six.
    Callers get the decision from ``resolution.is_resolved``, which returns
    True for everything while the rollout flag is off.

    Provenance is never withheld. It is the identify half and reveals nothing
    about the tactic, which is the entire reason for splitting the label in two.
    """
    if resolved and title and title.strip():
        return title
    return compose_provenance(end_time=end_time, ply=ply, opening_name=opening_name)
