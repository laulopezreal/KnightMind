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
    """Full-move number from a half-move index, matching the rest of the app.

    ``identity.py`` and ``spaced_repetition.py`` both compute this as
    ``ply // 2 + 1``; duplicating the expression a third time is how the three
    drift apart, so callers should come here.
    """
    return (ply or 0) // PLY_PER_MOVE + 1


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
) -> str:
    """What the API serves as a puzzle's name: nickname when it has one.

    THIS is the one function every puzzle-returning route should call. Clients
    then never branch on "is there a title", and never receive a value they are
    trusted not to render. Both a Pydantic model and a bare ``dict`` payload can
    call it, which matters while ``/puzzles/due`` is still typed ``list[dict]``.

    Note what this does NOT do yet: it does not gate. Under §4 of the design a
    nickname is withheld until the puzzle is resolved, and wiring that in is
    rollout step 3. Step 1 only routes every surface through one function, so
    that later change lands in one place instead of six. Today a nickname wins
    wherever one exists, which is everywhere, so nothing visibly changes.
    """
    if title and title.strip():
        return title
    return compose_provenance(end_time=end_time, ply=ply, opening_name=opening_name)
