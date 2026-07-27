"""
Spaced repetition storage module.
Handles database operations for puzzle reviews and statistics.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from services.api.models import Puzzle, PuzzleResult, PuzzleReview, PuzzleStats
from services.api.puzzles.identity import assign_primary_motif, generate_puzzle_title
from services.api.storage.puzzle_repository import PuzzleRepository

# Datetime convention (single documented rule for all "due" comparisons):
# every datetime is persisted as naive-UTC (values come from
# ``datetime.now(timezone.utc)`` and land in naive ``DateTime`` columns).
#   * In SQL, compare ``next_due_at`` against a NAIVE-UTC ``now`` so both sides
#     match the stored representation. Passing an aware ``now`` is correct on
#     SQLite (tzinfo is stripped symmetrically) but silently wrong on Postgres
#     when the session ``TimeZone`` is not UTC, because a naive column is then
#     reinterpreted in the session zone — shifting the due boundary by the UTC
#     offset. Use ``_utcnow_naive()`` for every SQL comparison.
#   * In Python, coerce a naive value read back from the DB to aware-UTC before
#     comparing it against an aware ``now`` (see ``get_adaptive_puzzles``).


def _utcnow_naive() -> datetime:
    """Return the current UTC time as a naive datetime (tzinfo stripped).

    Used as the bound for SQL comparisons against naive-UTC ``DateTime``
    columns so the comparison is backend-independent (see module note).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def calculate_next_interval(
    current_interval: int | None, ease_factor: float, result: PuzzleResult | str
) -> tuple[int, float]:
    """
    Calculate the next interval and ease factor based on simple SR rules.

    Rules:
    - On FAIL: interval=1, ease=max(1.3, ease-0.2)
    - On PASS:
        - if interval is None (new): interval=1
        - else if interval == 1: interval=3
        - else: interval = round(interval * ease)
        - ease = min(2.8, ease+0.05)
    """
    res = result if isinstance(result, PuzzleResult) else PuzzleResult(result)

    if res == PuzzleResult.FAIL:
        new_interval = 1
        new_ease = max(1.3, ease_factor - 0.2)
    else:  # PASS
        if current_interval is None:
            new_interval = 1
        elif current_interval == 1:
            new_interval = 3
        else:
            new_interval = round(current_interval * ease_factor)

        new_ease = min(2.8, ease_factor + 0.05)

    return new_interval, new_ease


def get_puzzle_stats(
    db: Session, puzzle_id: str, username: str, *, for_update: bool = False
) -> PuzzleStats | None:
    """Get statistics for a specific puzzle and user.

    ``for_update`` takes a row lock (Postgres ``SELECT ... FOR UPDATE``) so a
    caller doing a read-modify-write on the counters is race-safe against
    concurrent reviews of the same puzzle. The lock is a no-op on SQLite (which
    doesn't support ``FOR UPDATE`` and serializes writers anyway).
    """
    stmt = select(PuzzleStats).where(
        PuzzleStats.puzzle_id == puzzle_id, PuzzleStats.username == username
    )
    if for_update and db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    return db.scalars(stmt).first()


def get_all_puzzle_stats(db: Session, username: str) -> dict[str, PuzzleStats]:
    """Get statistics for all puzzles belonging to a user."""
    stmt = select(PuzzleStats).where(PuzzleStats.username == username)
    return {stats.puzzle_id: stats for stats in db.scalars(stmt).all()}


def insert_puzzle_review(
    db: Session,
    puzzle_id: str,
    username: str,
    result: PuzzleResult | str,
    time_spent_ms: int | None = None,
    reviewed_at: datetime | None = None,
    session_id: str | None = None,
    client_review_id: str | None = None,
    attempted_move: str | None = None,
    client_result: PuzzleResult | str | None = None,
    verified: bool = False,
    source: str | None = None,
) -> PuzzleReview:
    """
    Record a puzzle review in the database.

    Args:
        db: Database session
        puzzle_id: ID of the puzzle
        username: Username of the reviewer
        result: 'pass' or 'fail' (or PuzzleResult enum)
        time_spent_ms: Time spent on the puzzle in milliseconds
        reviewed_at: Timestamp of the review (defaults to current time)
        session_id: Optional training session ID
        client_review_id: Optional client-supplied idempotency key. When set, a
            unique index over (puzzle_id, username, session_id, client_review_id)
            prevents a retried/double-submitted review from being recorded twice.
        attempted_move: The UCI move the user played (None for no-move flows).
        client_result: The raw pass/fail the client claimed, preserved even when
            the server overrides ``result`` after verifying the move.
        verified: True only when the server checked the attempted move against
            the puzzle's accepted-solution set.
        source: How the outcome was decided ("server_verified" or
            "client_reported"); None for legacy rows.

    Returns:
        The created PuzzleReview object

    Note:
        This function does NOT commit. It flushes so autogenerated IDs and
        constraint violations surface immediately; the caller owns the
        transaction boundary and must commit (unit-of-work pattern).
    """
    if reviewed_at is None:
        reviewed_at = datetime.now(timezone.utc)

    # Ensure result is a string if Enum is passed
    result_val = result.value if isinstance(result, PuzzleResult) else result
    client_result_val = (
        client_result.value
        if isinstance(client_result, PuzzleResult)
        else client_result
    )

    review = PuzzleReview(
        puzzle_id=puzzle_id,
        username=username,
        reviewed_at=reviewed_at,
        result=result_val,
        time_spent_ms=time_spent_ms,
        session_id=session_id,
        client_review_id=client_review_id,
        attempted_move=attempted_move,
        client_result=client_result_val,
        verified=verified,
        source=source,
    )
    db.add(review)
    db.flush()
    return review


def update_puzzle_stats(
    db: Session,
    puzzle_id: str,
    username: str,
    result: PuzzleResult | str,
    reviewed_at: datetime | None = None,
) -> PuzzleStats:
    """
    Update aggregate statistics for a puzzle.
    Creates the stats entry if it doesn't exist.

    Args:
        db: Database session
        puzzle_id: ID of the puzzle
        username: Username of the player
        result: 'pass' or 'fail' (or PuzzleResult enum)
        reviewed_at: Timestamp of the review (defaults to current time)

    Returns:
        The updated PuzzleStats object

    Note:
        This function does NOT commit. It flushes so constraint violations
        surface immediately; the caller owns the transaction boundary and
        must commit (unit-of-work pattern).
    """
    if reviewed_at is None:
        reviewed_at = datetime.now(timezone.utc)

    # Lock the stats row so the counter read-modify-write and the scheduling
    # computed from the post-increment counts are race-safe: concurrent reviews
    # of the SAME puzzle serialize here instead of losing an increment. On the
    # first-ever review the row doesn't exist yet, so there is nothing to lock —
    # the INSERT's primary key then serializes the two racers (one raises
    # IntegrityError, which the review endpoint's replay path handles).
    stats = get_puzzle_stats(db, puzzle_id, username, for_update=True)
    res_enum = result if isinstance(result, PuzzleResult) else PuzzleResult(result)

    if not stats:
        puzzle = PuzzleRepository(db).get_puzzle(username, puzzle_id)
        motif = assign_primary_motif(puzzle)
        title = generate_puzzle_title(motif)
        stats = PuzzleStats(
            puzzle_id=puzzle_id,
            username=username,
            attempts=0,
            pass_count=0,
            fail_count=0,
            ease_factor=2.0,
            primary_motif=motif,
            title=title,
        )
        db.add(stats)

    stats.attempts += 1
    if res_enum == PuzzleResult.PASS:
        stats.pass_count += 1
    else:
        stats.fail_count += 1

    # Apply scheduling rules
    new_interval, new_ease = calculate_next_interval(
        stats.interval_days, stats.ease_factor, res_enum
    )

    stats.interval_days = new_interval
    stats.ease_factor = new_ease
    stats.next_due_at = reviewed_at + timedelta(days=new_interval)

    stats.last_reviewed_at = reviewed_at
    stats.last_result = res_enum.value

    db.flush()
    return stats


def get_adaptive_puzzles(
    db: Session,
    username: str,
    puzzle_ids: list[str],
    n: int = 5,
    session_type: str = "standard",
    target_accuracy: float = None,
) -> tuple[list[str], dict[str, PuzzleStats]]:
    """
    Get puzzles for the user from the candidate list, ordered by adaptive priority.

    Priority factors:
    1. Due: next_due_at <= now
    2. New: next_due_at IS NULL
    3. Future: ordered by next_due_at ASC
    4. Adaptive: Based on user performance and session type

    For accuracy_goal sessions:
    - Prioritize puzzles with lower pass rates if user is below target
    - Prioritize puzzles with higher pass rates if user is above target
    """
    now = datetime.now(timezone.utc)

    # Query stats for the given puzzle IDs
    stmt = select(PuzzleStats).where(
        PuzzleStats.username == username, PuzzleStats.puzzle_id.in_(puzzle_ids)
    )
    all_stats = {s.puzzle_id: s for s in db.scalars(stmt).all()}

    # Sort candidate IDs with adaptive logic
    def sort_key(pid: str):
        stats = all_stats.get(pid)

        # Base priority (same as before)
        if not stats or stats.next_due_at is None:
            # New puzzle: Priority 2 (high, but after due)
            base_priority = 1
            time_factor = now
        else:
            # Ensure stats.next_due_at is aware for comparison
            next_due = stats.next_due_at
            if next_due.tzinfo is None:
                next_due = next_due.replace(tzinfo=timezone.utc)

            if next_due <= now:
                # Due puzzle: Priority 1 (highest)
                base_priority = 0
                time_factor = next_due
            else:
                # Future puzzle: Priority 3 (lowest)
                base_priority = 2
                time_factor = next_due

        # Adaptive factors
        adaptive_score = 0.0

        # For accuracy goal sessions, adjust based on target
        if session_type == "accuracy_goal" and target_accuracy is not None and stats:
            # Calculate current accuracy for this puzzle
            if stats.attempts > 0:
                puzzle_accuracy = (stats.pass_count / stats.attempts) * 100
                # If user is below target, prioritize harder puzzles (lower accuracy)
                # If user is above target, prioritize easier puzzles (higher accuracy)
                accuracy_diff = puzzle_accuracy - target_accuracy
                # Normalize to -1 to 1 range
                adaptive_score = accuracy_diff / 100.0

        return (base_priority, time_factor, -adaptive_score)

    sorted_pids = sorted(puzzle_ids, key=sort_key)
    return sorted_pids[:n], all_stats


def get_due_puzzles(
    db: Session, username: str, puzzle_ids: list[str], n: int = 5
) -> tuple[list[str], dict[str, PuzzleStats]]:
    """
    Get puzzles for the user from the candidate list, ordered by SR priority.

    Priority:
    1. Due: next_due_at <= now
    2. New: next_due_at IS NULL
    3. Future: ordered by next_due_at ASC
    """
    return get_adaptive_puzzles(db, username, puzzle_ids, n)


def get_trainable_puzzle_ids(
    db: Session, username: str, puzzle_ids: list[str]
) -> list[str]:
    """Narrow candidate ids to the puzzles the user may train *right now*.

    Trainable = never scheduled (a brand-new puzzle, or one with no stats row
    yet) OR scheduled and the date has arrived. A puzzle scheduled for the
    future is deliberately excluded, for two reasons:

    * Honesty. The UI counts "N puzzles due" from
      :func:`get_trainable_puzzle_count`; a session that quietly tops itself up
      with not-yet-due puzzles makes that number a lie.
    * Correctness. :func:`update_puzzle_stats` re-anchors ``next_due_at`` on the
      review date, so an early PASS inflates the interval (30d → 75d measured
      from today, not from the original due date) while an early FAIL resets a
      well-learned puzzle to interval 1 and drops its ease — punishing the user
      for a puzzle the scheduler itself served too soon.

    Written as an exclusion query rather than a join so that ids with no stats
    row survive naturally, and so the caller's priority order is preserved.
    """
    if not puzzle_ids:
        return []
    now = _utcnow_naive()
    scheduled_later = set(
        db.scalars(
            select(PuzzleStats.puzzle_id).where(
                PuzzleStats.username == username,
                PuzzleStats.puzzle_id.in_(puzzle_ids),
                PuzzleStats.next_due_at.isnot(None),
                PuzzleStats.next_due_at > now,
            )
        ).all()
    )
    return [pid for pid in puzzle_ids if pid not in scheduled_later]


def get_trainable_puzzle_count(db: Session, username: str) -> int:
    """Count the puzzles the user can train right now.

    This is what "N puzzles due" means everywhere in the UI, and it counts
    exactly the set :func:`get_trainable_puzzle_ids` serves — the two must not
    drift, or the hero card promises work the session can't deliver (or hides
    work it can). Unlike :func:`get_due_puzzle_count` this includes puzzles
    that have never been reviewed, which is why freshly generated puzzles are
    trainable the moment they exist.
    """
    now = _utcnow_naive()
    stmt = (
        select(func.count(Puzzle.id))
        .outerjoin(
            PuzzleStats,
            and_(
                PuzzleStats.puzzle_id == Puzzle.id,
                PuzzleStats.username == Puzzle.username,
            ),
        )
        .where(
            Puzzle.username == username,
            or_(
                PuzzleStats.puzzle_id.is_(None),
                PuzzleStats.next_due_at.is_(None),
                PuzzleStats.next_due_at <= now,
            ),
        )
    )
    return db.scalar(stmt) or 0


def get_scheduled_within_count(db: Session, username: str, hours: int) -> int:
    """Count puzzles scheduled to become due inside the next ``hours``.

    Strictly forward-looking (``now < next_due_at <= now + hours``) so it can be
    reported alongside :func:`get_trainable_puzzle_count` without overlapping
    it — the two sets are disjoint by construction.
    """
    now = _utcnow_naive()
    stmt = select(func.count(PuzzleStats.puzzle_id)).where(
        PuzzleStats.username == username,
        PuzzleStats.next_due_at.isnot(None),
        PuzzleStats.next_due_at > now,
        PuzzleStats.next_due_at <= now + timedelta(hours=hours),
    )
    return db.scalar(stmt) or 0


def get_due_puzzle_count(db: Session, username: str) -> int:
    """Get count of puzzles due for review.

    Strict "has a schedule and it has arrived" count. Prefer
    :func:`get_trainable_puzzle_count` for anything user-facing — this one
    excludes never-reviewed puzzles.
    """
    # naive-UTC bound: match the naive-UTC storage of next_due_at (see module note)
    now = _utcnow_naive()
    stmt = select(func.count(PuzzleStats.puzzle_id)).where(
        PuzzleStats.username == username, PuzzleStats.next_due_at <= now
    )
    return db.scalar(stmt) or 0


def get_next_due_date(db: Session, username: str) -> datetime | None:
    """Get the next upcoming due date for a user's puzzles."""
    # naive-UTC bound: match the naive-UTC storage of next_due_at (see module note)
    now = _utcnow_naive()
    stmt = select(func.min(PuzzleStats.next_due_at)).where(
        PuzzleStats.username == username, PuzzleStats.next_due_at > now
    )
    return db.scalar(stmt)
