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
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.api.models import (
    DiagnosisAuditLog,
    Game,
    Puzzle,
    PuzzleDiagnosis,
    PuzzleStats,
)
from services.api.puzzles import ai_naming
from services.api.puzzles.identity import assign_primary_motif
from services.api.puzzles.position_names import (
    PositionFacts,
    answer_square_of,
    compose_position_name,
    disambiguate,
)
from services.api.puzzles.title_registry import taken_titles
from services.api.storage.ai_audit_repository import (
    AIAuditRepository,
    AuditWrite,
    Budget,
)
from services.api.usernames import canonical_username

logger = logging.getLogger(__name__)

# How many previously-used names to show the model. Enough to steer it off a
# phrasing it just used, short enough not to dominate the cached prefix.
AVOID_WINDOW = 25

# What one background job run will name. Bounded because the job also does
# diagnosis and must not turn into an unbounded model-call loop; the job is
# re-queued while work remains, so the corpus still converges.
NAMING_BATCH_MAX = 25

# How many failed calls in a row mean the provider is down rather than one call
# being unlucky. A single failure at the tail of an otherwise fine run is not
# worth pausing for; three with no answer between them is not bad luck.
ERROR_STREAK_LIMIT = 3

# How long a tripped breaker holds the re-queue chain off. Matched to the
# worker's stuck-job lease (worker.py) so the two timeouts that decide "is this
# thing alive?" agree, and short enough that a blip costs one paused chain
# rather than an afternoon of unnamed puzzles.
ERROR_STREAK_COOLDOWN = timedelta(minutes=15)


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
        answer_square=answer_square_of(puzzle.best_move_uci),
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
    budgets: dict[str, Budget] = {}

    def budget_for(handle: str) -> Budget:
        key = canonical_username(handle)
        if key not in budgets:
            budgets[key] = audit.budget_last_24h(key, call_type=ai_naming.CALL_TYPE)
        return budgets[key]

    outcomes: Counter = Counter()

    # The names already spoken for, per user. Two things changed here and both
    # matter.
    #
    # Seeded from the DATABASE, not from this run's rows. A pass is routinely
    # partial — ``--limit`` bounds it, the background job takes 25 at a time,
    # ``--username`` scopes it — so the names it can see are a fraction of the
    # names the user holds. A set built from the run alone happily hands out a
    # name some untouched puzzle already carries, which is how duplicates
    # survived across runs in the first place.
    #
    # Keyed by user, because uniqueness is per user. One shared set would rename
    # a puzzle because a DIFFERENT tenant reached the same fork square — a real
    # possibility on a pass with no ``--username``.
    used: dict[str, set[str]] = {}

    def titles_for(handle: str) -> set[str]:
        key = canonical_username(handle)
        if key not in used:
            used[key] = taken_titles(db, key)
        return used[key]

    # Distinct (user, title) pairs this pass saw, kept separately now that
    # ``used`` is pre-loaded with names the pass never touched. It is reported
    # as ``distinct`` and printed by scripts/ai_name_puzzles.py.
    seen: set[tuple[str, str]] = set()
    recent: list[str] = []
    samples: list[tuple[str, str]] = []
    named = 0

    for puzzle, stats, diagnosis, game in rows:
        if limit is not None and named >= limit:
            break

        source = stats.title_source if stats else None
        owner = canonical_username(puzzle.username)
        # A name the user typed is never touched, with or without --force.
        if source == "user":
            outcomes["kept_user_title"] += 1
            # No need to reserve it: a kept title is already in the database,
            # so titles_for() has it the moment anyone asks.
            if stats and stats.title:
                seen.add((owner, stats.title))
            continue
        # An existing AI name is the cache. --force is how you invalidate it
        # after changing the prompt.
        if source == "ai" and not force:
            outcomes["already_named"] += 1
            if stats and stats.title:
                seen.add((owner, stats.title))
            continue

        motif = (stats.primary_motif if stats else None) or assign_primary_motif(puzzle)
        fallback = compose_position_name(
            PositionFacts(
                fen=puzzle.fen or "",
                played_move_uci=puzzle.played_move_uci or "",
                answer_square=answer_square_of(puzzle.best_move_uci),
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
            # Enough rows to open the breaker, then stop asking.
            #
            # Writing one per remaining puzzle is pure noise — the answer will
            # not change within this run, and a large corpus turns one capped
            # pass into hundreds of identical audit rows. But stopping at the
            # FIRST one would leave the streak below ERROR_STREAK_LIMIT, so the
            # breaker never opens and the worker re-queues immediately: cheaper
            # rows, same loop. Recording exactly the threshold is what makes the
            # next run defer instead.
            if outcomes["budget_exhausted"] >= ERROR_STREAK_LIMIT:
                logger.info(
                    "Naming budget exhausted for %s; stopping this pass",
                    puzzle.username,
                )
                break
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
            if outcome.usable and outcome.name:
                name, title_source = outcome.name, "ai"
            else:
                name, title_source = fallback, "position"

        # The model is asked not to repeat itself but cannot be trusted to
        # succeed at it, so uniqueness is enforced here regardless of source —
        # and now against the whole library, not just this run.
        titles = titles_for(puzzle.username)
        current = stats.title if stats is not None else None
        # A row may always keep its own name, so its current title is excluded
        # from what it collides against — otherwise every re-run would rename
        # each puzzle away from itself ("The f7 Knight Fork" -> "..., move 12"
        # -> "... (2)", forever).
        #
        # But the title it vacates is NOT handed to anybody else in this pass:
        # ``titles`` keeps it. Reusing it here would mean two UPDATEs in one
        # transaction swapping a name between rows, and Postgres checks a unique
        # index per statement — whichever UPDATE the unit of work happened to
        # emit first would fail against the row that had not moved yet.
        # SQLAlchemy orders UPDATEs by primary key, so that is a coin flip, and
        # a coin flip that aborts a naming run. The freed name is simply free
        # from the NEXT pass on.
        against = titles - {current} if current else titles
        name = disambiguate(name, against, (puzzle.ply or 0) // 2 + 1)
        titles.add(name)
        seen.add((owner, name))
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
        try:
            db.commit()
        except IntegrityError:
            # Someone else took one of these names between our read and our
            # commit — a puzzle saved by an import running alongside the job.
            # This module promises not to raise into its callers (the operator
            # CLI and a background job), and the work is recoverable: the
            # puzzles stay pending, so the job re-queues and the next pass reads
            # the name that beat us. Losing one batch beats failing the job.
            db.rollback()
            outcomes["title_conflict"] += 1
            logger.warning("naming pass rolled back: a title was taken concurrently")

    return {
        "total": len(rows),
        "named": named,
        "outcomes": outcomes,
        "distinct": len(seen),
        "samples": samples,
        # The tightest remaining allowance across the users actually touched;
        # None when nothing was ever charged (a dry run, or an empty scope).
        "budget_remaining": (
            min(b.remaining for b in budgets.values()) if budgets else None
        ),
    }


def pending_count(db: Session, username: str) -> int:
    """Puzzles for this user that still need — and could still get — an AI name.

    The worker's re-queue predicate reads this, so it MUST reach zero. The
    diagnosis job keeps that property by recording a row even for puzzles it
    cannot analyse; without one, "every future run would re-attempt this same
    un-analysable puzzle forever".

    Naming has the same trap and needs the same answer. A name the gate rejects
    leaves the puzzle on its deterministic title, which by itself still looks
    like pending work — so the job would be re-queued, reject it again, and
    loop until the budget ran out, then loop for free after that. The audit log
    is the record of the attempt: a puzzle the model has already answered for
    (accepted or rejected) is not counted again. Errors and budget skips are
    NOT attempts and stay eligible, so an outage or an exhausted day retries
    later rather than permanently giving up.

    Audit rows are purged after ``AUDIT_RETENTION_DAYS``, which means a
    rejected puzzle becomes eligible again roughly monthly. That is a feature:
    it is one cheap retry after a prompt change, not a loop.

    Returns 0 when naming is disabled, so a deployment that never turns naming
    on cannot queue work that will never be done.
    """
    from services.api.ai import config

    if not config.naming_is_enabled():
        return 0

    answered = (
        select(DiagnosisAuditLog.puzzle_id)
        .where(
            DiagnosisAuditLog.call_type == ai_naming.CALL_TYPE,
            DiagnosisAuditLog.username == canonical_username(username),
            DiagnosisAuditLog.status.in_(("accepted", "rejected")),
            DiagnosisAuditLog.puzzle_id.is_not(None),
        )
        .scalar_subquery()
    )

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
                Puzzle.id.notin_(answered),
            )
        )
        or 0
    )


def retry_is_backed_off(db: Session, username: str) -> bool:
    """True while the provider looks down and the chain must stop re-queuing.

    ``pending_count`` answers "does this work still need doing?" and keeps
    saying yes through an outage, on purpose: an error is not an answer and a
    blip must not mark a puzzle permanently unnamed. But the worker re-queues a
    diagnosis job on that number and claims the new job about two seconds later,
    so "yes, still" plus "then go again" is a spin loop — one that an
    unreachable provider can hold open indefinitely, because an error costs no
    budget either. It writes an audit row and a job row per turn, and since the
    worker became its own container (#374) it burns that container whole.

    This is the damping term, and it is deliberately a *separate* question
    asked by the scheduler rather than a fudge inside ``pending_count``. The
    work really is still pending; what has changed is that right now there is no
    point doing it. Folding "we cannot do this at the moment" into "this does
    not need doing" is how a transient outage would come to look like a
    permanent decision.

    Read from the audit ledger rather than a counter in the worker, for the
    reason the daily budget is: it survives a worker restart, it is per user,
    and it cannot drift from what actually happened. The loop writes the rows
    that stop it.

    Not a latch. The streak resets on the first call the model answers, and
    expires on its own after ``ERROR_STREAK_COOLDOWN`` so the next natural
    trigger — a fresh import, a diagnosis run, the operator CLI — gets a clean
    attempt rather than inheriting yesterday's outage.
    """
    from services.api.ai import config

    if not config.naming_is_enabled():
        # The branch every deployment takes today. pending_count is 0 here
        # anyway, so there is nothing to back off from and no reason to pay for
        # the query.
        return False

    streak = AIAuditRepository(db).failing_streak(
        username, ai_naming.CALL_TYPE, within=ERROR_STREAK_COOLDOWN
    )
    if streak < ERROR_STREAK_LIMIT:
        return False

    logger.warning(
        "Naming paused for %s: %d calls failed with no answer between them",
        username,
        streak,
    )
    return True
