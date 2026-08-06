"""The naming pass: give puzzles an AI name, one call each.

Lives in the API package rather than in ``scripts/`` because two callers need
it and the API layer cannot import a script:

* ``scripts.ai_name_puzzles`` — the operator CLI, for backfills and re-runs.
* ``diagnosis.job`` — the background job already chained after puzzle
  generation, so puzzles created from here on are named without anyone
  remembering to run anything.

The pass is resumable and idempotent: a puzzle already carrying
``title_source='ai'`` is skipped, so the second caller to reach a puzzle does
nothing and costs nothing. ``title_source='user'`` is never touched at all.

Degrades rather than fails. Disabled, unkeyed, over budget, refused, or
rejected all end with the puzzle getting its deterministic position-derived
name. Nothing here raises into a caller.
"""

import logging
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.api.models import Game, Puzzle, PuzzleDiagnosis, PuzzleStats
from services.api.puzzles import ai_naming
from services.api.puzzles.identity import assign_primary_motif
from services.api.puzzles.position_names import (
    PositionFacts,
    compose_position_name,
    disambiguate,
)
from services.api.storage.ai_audit_repository import AIAuditRepository, AuditWrite
from services.api.usernames import canonical_username

logger = logging.getLogger(__name__)

# How many previously-used names to show the model. Enough to steer it off a
# phrasing it just used, short enough not to dominate the cached prefix.
AVOID_WINDOW = 25

# What one background job run will name. Bounded because the job also does
# diagnosis and must not turn into an unbounded model-call loop; the job is
# re-queued while work remains, so the corpus still converges.
NAMING_BATCH_MAX = 25


def _evidence_value(evidence, item_id: str) -> str | None:
    """Pull one fact out of a stored ``evidence_json`` list."""
    for item in evidence or []:
        if isinstance(item, dict) and item.get("id") == item_id:
            return item.get("value")
    return None


def _move_time_seconds(evidence) -> float | None:
    raw = _evidence_value(evidence, "clock.move_time")
    if raw is None:
        return None
    try:
        return float(str(raw).split()[0])
    except (ValueError, IndexError):
        return None


def _user_won(game, username: str) -> bool | None:
    """True when the user won this game, None when it cannot be determined."""
    if game is None:
        return None
    user = canonical_username(username)
    if canonical_username(game.white_username) == user:
        return game.white_result == "win"
    if canonical_username(game.black_username) == user:
        return game.black_result == "win"
    return None


def _answer_square(puzzle) -> str | None:
    """The square the winning move lands on, e.g. ``"f7"``.

    Used only to reject a name that gives it away. An unparseable move yields
    None, which just means the gate has one fewer thing to check.
    """
    uci = puzzle.best_move_uci or ""
    if len(uci) < 4:
        return None
    square = uci[2:4]
    if square[0] in "abcdefgh" and square[1] in "12345678":
        return square
    return None


def build_facts(
    puzzle, diagnosis, game, motif: str | None = None
) -> ai_naming.NameFacts:
    """Assemble what the model may see. No handle goes in here — by design.

    ``NameFacts`` has no field for a username or an opponent, so this function
    could not leak one even if it tried. ``user_won`` is a bool derived from
    the game locally; the handles it was derived from stay here.
    """
    evidence = diagnosis.evidence_json if diagnosis else None
    return ai_naming.NameFacts(
        fen=puzzle.fen or "",
        played_move_san=ai_naming.san_or_uci(puzzle.fen, puzzle.played_move_uci or ""),
        best_move_san=ai_naming.san_or_uci(puzzle.fen, puzzle.best_move_uci or ""),
        primary_motif=motif,
        move_number=(puzzle.ply or 0) // 2 + 1,
        phase=diagnosis.phase if diagnosis else None,
        opening_name=diagnosis.opening_name if diagnosis else None,
        move_time_seconds=_move_time_seconds(evidence),
        user_won=_user_won(game, puzzle.username),
        # Gate input: the square the winning move lands on, so a name that
        # arrives at it is rejected. Not withheld from the model — the move
        # itself is in the prompt — just parsed into the form the check needs.
        answer_square=_answer_square(puzzle),
    )


def name_puzzles(
    db: Session,
    username: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    limit: int | None = None,
) -> dict:
    """Name every in-scope puzzle, model-first with a deterministic fallback."""
    from services.api.ai import config

    # Read once. Every puzzle in a run agrees about whether naming is on, and
    # the disabled path never reaches the client at all.
    enabled = config.naming_is_enabled()

    stmt = (
        select(Puzzle, PuzzleStats, PuzzleDiagnosis, Game)
        .outerjoin(
            PuzzleStats,
            # Both halves of the ownership key — see #360.
            (PuzzleStats.puzzle_id == Puzzle.id)
            & (PuzzleStats.username == Puzzle.username),
        )
        .outerjoin(
            PuzzleDiagnosis,
            (PuzzleDiagnosis.puzzle_id == Puzzle.id)
            & (PuzzleDiagnosis.username == Puzzle.username),
        )
        .outerjoin(
            Game,
            (Game.game_id == Puzzle.source_game_id)
            & (Game.username == Puzzle.username),
        )
        .order_by(Puzzle.created_at)
    )
    if username:
        stmt = stmt.where(Puzzle.username == canonical_username(username))

    rows = db.execute(stmt).all()

    audit = AIAuditRepository(db)
    # One budget per user, read on first sight and decremented locally
    # thereafter: re-reading per puzzle would cost a query for a number only
    # this loop moves. Keyed by user rather than read once, because without
    # --username the rows span users and a single budget would be charged
    # against whichever handle happened to be asked for — leaving the per-user
    # cap unenforced for everyone else.
    budgets: dict[str, object] = {}

    def budget_for(handle: str):
        key = canonical_username(handle)
        if key not in budgets:
            budgets[key] = audit.budget_last_24h(key, call_type=ai_naming.CALL_TYPE)
        return budgets[key]

    outcomes: Counter = Counter()
    used: set[str] = set()
    recent: list[str] = []
    samples: list[tuple[str, str]] = []
    named = 0

    for puzzle, stats, diagnosis, game in rows:
        if limit is not None and named >= limit:
            break

        source = stats.title_source if stats else None
        # A name the user typed is never touched, with or without --force.
        if source == "user":
            outcomes["kept_user_title"] += 1
            if stats and stats.title:
                used.add(stats.title)
            continue
        # An existing AI name is the cache. --force is how you invalidate it
        # after changing the prompt.
        if source == "ai" and not force:
            outcomes["already_named"] += 1
            if stats and stats.title:
                used.add(stats.title)
            continue

        motif = (stats.primary_motif if stats else None) or assign_primary_motif(puzzle)
        fallback = compose_position_name(
            PositionFacts(
                fen=puzzle.fen or "",
                best_move_uci=puzzle.best_move_uci or "",
                primary_motif=motif,
                move_number=(puzzle.ply or 0) // 2 + 1,
            )
        )

        if dry_run:
            # No model call: a dry run must be free and must not consume
            # budget. It shows the deterministic floor, which is the worst
            # case for any puzzle.
            name, title_source = fallback, "position"
            outcomes["dry_run"] += 1
        elif not enabled:
            # Short-circuited here rather than left to name_puzzle's own
            # disabled check, so a switched-off feature writes NOTHING — not
            # even a skip row. An audit table full of skips hides the real
            # ones; the diagnosis kill switch has the same property.
            name, title_source = fallback, "position"
            outcomes["disabled"] += 1
        elif budget_for(puzzle.username).exhausted:
            name, title_source = fallback, "position"
            outcomes["budget_exhausted"] += 1
            audit.record(
                AuditWrite(
                    username=puzzle.username,
                    call_type=ai_naming.CALL_TYPE,
                    puzzle_id=puzzle.id,
                    status=ai_naming.SKIPPED,
                    reason="budget_exhausted",
                )
            )
        else:
            facts = build_facts(puzzle, diagnosis, game, motif=motif)
            outcome = ai_naming.name_puzzle(facts, avoid=recent[-AVOID_WINDOW:])

            if outcome.status in (ai_naming.ACCEPTED, ai_naming.REJECTED):
                key = canonical_username(puzzle.username)
                budgets[key] = budgets[key].spend(1)
            audit.record(
                AuditWrite(
                    username=puzzle.username,
                    call_type=ai_naming.CALL_TYPE,
                    puzzle_id=puzzle.id,
                    status=outcome.status,
                    reason=outcome.reason,
                    model_version=outcome.model_version,
                    response_json=outcome.raw_response,
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                )
            )
            outcomes[outcome.status] += 1
            if outcome.usable:
                name, title_source = outcome.name, "ai"
            else:
                name, title_source = fallback, "position"

        # The model is asked not to repeat itself but cannot be trusted to
        # succeed at it, so uniqueness is enforced here regardless of source.
        name = disambiguate(name, used, (puzzle.ply or 0) // 2 + 1)
        used.add(name)
        recent.append(name)
        named += 1
        if len(samples) < 40:
            samples.append((title_source, name))

        if dry_run:
            continue

        if stats is None:
            # 88 puzzles in the live corpus have no stats row at all. Without
            # this they would be permanently unnameable.
            db.add(
                PuzzleStats(
                    puzzle_id=puzzle.id,
                    username=puzzle.username,
                    primary_motif=motif,
                    title=name,
                    title_source=title_source,
                    attempts=0,
                    pass_count=0,
                    fail_count=0,
                    ease_factor=2.0,
                )
            )
            outcomes["stats_row_created"] += 1
        else:
            stats.title = name
            stats.title_source = title_source

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return {
        "total": len(rows),
        "named": named,
        "outcomes": outcomes,
        "distinct": len(used),
        "samples": samples,
        # The tightest remaining allowance across the users actually touched;
        # None when nothing was ever charged (a dry run, or an empty scope).
        "budget_remaining": (
            min(b.remaining for b in budgets.values()) if budgets else None
        ),
    }


def pending_count(db: Session, username: str) -> int:
    """Puzzles for this user that do not yet carry an AI name.

    The worker's re-queue predicate reads this. Without it, a run that
    diagnoses 200 puzzles and names only ``NAMING_BATCH_MAX`` of them would not
    be re-queued — diagnosis has no work left — and the rest would sit with
    their deterministic names forever.

    Returns 0 when naming is disabled, so a deployment that never turns naming
    on cannot queue work that will never be done.
    """
    from services.api.ai import config

    if not config.naming_is_enabled():
        return 0

    return (
        db.scalar(
            select(func.count())
            .select_from(Puzzle)
            .outerjoin(
                PuzzleStats,
                (PuzzleStats.puzzle_id == Puzzle.id)
                & (PuzzleStats.username == Puzzle.username),
            )
            .where(
                Puzzle.username == canonical_username(username),
                # NULL covers puzzles with no stats row at all.
                (PuzzleStats.title_source.is_(None))
                | (PuzzleStats.title_source.notin_(("ai", "user"))),
            )
        )
        or 0
    )
