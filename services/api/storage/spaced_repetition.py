"""
Spaced repetition storage module.
Handles database operations for puzzle reviews and statistics.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from services.api.models import Puzzle, PuzzleResult, PuzzleReview, PuzzleStats
from services.api.puzzles.identity import assign_primary_motif, generate_puzzle_title
from services.api.puzzles.title_registry import unique_title
from services.api.storage.puzzle_repository import PuzzleRepository
from services.api.usernames import canonical_username

# Username convention: every public function here folds its ``username``
# argument once with ``canonical_username`` and works with the folded value
# thereafter. See the "storage-boundary rule" section of
# ``services.api.usernames`` for why a bare ``.lower()`` is not an acceptable
# substitute — the failure mode is specifically severe in this module, because
# an unmatched fold yields empty stats and empty stats mean "never seen".

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
        PuzzleStats.puzzle_id == puzzle_id,
        PuzzleStats.username == canonical_username(username),
    )
    if for_update and db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    return db.scalars(stmt).first()


def get_all_puzzle_stats(db: Session, username: str) -> dict[str, PuzzleStats]:
    """Get statistics for all puzzles belonging to a user."""
    stmt = select(PuzzleStats).where(
        PuzzleStats.username == canonical_username(username)
    )
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
    review_context: str = "standard",
    affects_scheduling: bool = True,
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
        review_context: Server-owned context for telemetry and feedback.
        affects_scheduling: Whether this review may change PuzzleStats.

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
        username=canonical_username(username),
        reviewed_at=reviewed_at,
        result=result_val,
        time_spent_ms=time_spent_ms,
        session_id=session_id,
        client_review_id=client_review_id,
        attempted_move=attempted_move,
        client_result=client_result_val,
        verified=verified,
        source=source,
        review_context=review_context,
        affects_scheduling=affects_scheduling,
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

    # Folded once here, then reused for the read, the PuzzleRepository lookup
    # and the INSERT. Folding per-statement instead would let a future edit
    # change one of the three and fork a user's stats row from their puzzles.
    username = canonical_username(username)

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
        # generate_puzzle_title has seven strings in it, so this row's name
        # collides with an existing one as soon as the user has two puzzles of
        # the same motif with no stats row — and titles are unique per user, so
        # that collision would now fail the review instead of merely repeating a
        # name. Reviewing a puzzle must never fail because of what it is called.
        title = unique_title(
            db,
            username,
            generate_puzzle_title(motif),
            (getattr(puzzle, "ply", 0) or 0) // 2 + 1 if puzzle else None,
        )
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
    target_accuracy: float | None = None,
    focus_puzzle_ids: set[str] | None = None,
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

    ``focus_puzzle_ids`` biases a training-pattern session. It re-orders the
    candidates it is given and nothing else:

    * It sits *after* the due/new/future tier in the sort key, so a focus can
      never promote a not-yet-due puzzle above a due one. The caller has
      already narrowed to the trainable set (``get_trainable_puzzle_ids``);
      this only changes the order within it.
    * A focus puzzle absent from ``puzzle_ids`` stays absent. Focusing on a
      pattern with nothing due yields an ordinary session rather than an empty
      one or an error — the pattern is a preference, not a filter.

    Within one tier, focus puzzles come first in the tier's own order. That can
    serve a focus puzzle ahead of a more-overdue one, which is the point of
    asking for a focused session and costs nothing: every puzzle in the due
    tier is already due, so no interval is re-anchored early.
    """
    now = datetime.now(timezone.utc)
    focus = focus_puzzle_ids or set()

    # Folded once for both reads below (stats here, source games in
    # _source_games). Live/API traffic is unaffected because Username already
    # canonicalises at the request boundary; direct/internal callers with a
    # non-canonical handle are repaired here. The failure mode is silent and
    # severe: an unmatched fold produces an empty all_stats, collapsing every
    # puzzle to the never-seen tier and serving due puzzles as new — which then
    # re-anchors their intervals off the wrong date. This was previously a
    # `.lower()`, which is not the same fold: ' Bob ' lowercased is ' bob ',
    # a key that matches nothing, so the guard looked present and did nothing.
    username = canonical_username(username)

    # Query stats for the given puzzle IDs
    stmt = select(PuzzleStats).where(
        PuzzleStats.username == username,
        PuzzleStats.puzzle_id.in_(puzzle_ids),
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

        # Tier first, focus second: the focus re-orders within a tier and can
        # never cross one. With no focus requested every key is (…, 0, …) and
        # the order is byte-identical to an unfocused session.
        return (base_priority, 0 if pid in focus else 1, time_factor, -adaptive_score)

    sorted_pids = sorted(puzzle_ids, key=sort_key)

    # Counters reset per tier, so within a tier one puzzle per game is
    # *preferred* — but that is a preference, not a bound on the session. The
    # rest are deferred, not dropped, and ``varied[:n]`` reaches straight into
    # them whenever a tier holds fewer distinct games than the session has slots
    # — which the live due tier did when this was written (3 distinct games
    # against a default n=5), so a default session necessarily repeats a game.
    # ``_vary_session``'s own docstring says it: the caps cannot invent variety
    # that is not there.
    #
    # No ordering invariant is stated here, because three attempts to state one
    # were all wrong: "up to two spanning tiers", then "in production that bound
    # is two", then "within the first n of a tier, distinct games come first".
    # The last fails because the two caps COMPOSE. With only the game cap active
    # every deferred puzzle is by definition a repeat, so distinct-first does
    # hold — asserted by
    # TestGameDiversity::test_the_cap_cannot_invent_variety_that_is_not_there.
    # Turn the motif cap on and a distinct-GAME puzzle can be deferred for
    # motif reasons and land behind a game repeat inside the first ``n``:
    # games A, B, A, C all sharing one motif, n=3, serves A twice and never C.
    # Pinned by ``test_the_two_caps_compose_and_neither_orders_alone``.
    #
    # Three of TestGameDiversity's five tests set motif="blunder" to neutralise
    # the motif cap; the other two take the helper default "Fork", where at
    # _GAME_CAP=1 the motif cap never binds. So the class does not assert the
    # composition at the current constants — not that it could not: raise
    # _GAME_CAP to 3 and one of them becomes composition-sensitive.
    #
    # The lesson these three attempts share: a measured count rots (the due tier
    # went 9 -> 12 in a day) and an ordering rule stated over one cap is falsified
    # by the other. Assert structure here, put behaviour in tests.
    #
    # Sharing counters globally
    # would be tighter, but it is also the direction that risks starving a tier:
    # once the due tier consumes its games, every new puzzle from those games
    # would defer. Per-tier is the more permissive and therefore safer default,
    # and tightening it is a behaviour change that deserves its own replay
    # against the live pool rather than riding along here.
    #
    # A focused session is *asked* to be concentrated on one cause or opening,
    # so capping by motif would fight the user's explicit choice. Spreading
    # across GAMES does not: a focus asks for a kind of mistake, never for five
    # positions out of the same game, so that cap stays on in both modes.
    source_games = _source_games(db, username, sorted_pids)
    varied: list[str] = []
    for tier in sorted({_tier_of(pid, all_stats, now) for pid in sorted_pids}):
        tier_pids = [p for p in sorted_pids if _tier_of(p, all_stats, now) == tier]
        varied.extend(
            _vary_session(tier_pids, all_stats, source_games, n, cap_motifs=not focus)
        )

    return varied[:n], all_stats


def _tier_of(pid: str, all_stats: dict[str, PuzzleStats], now: datetime) -> int:
    """Scheduling tier: 0 due, 1 never-seen, 2 scheduled for later.

    Mirrors ``base_priority`` in ``sort_key``. Extracted so the variety pass can
    group by the same boundary the sort established, instead of re-deriving it.
    """
    stats = all_stats.get(pid)
    if stats is None or stats.next_due_at is None:
        return 1
    next_due = stats.next_due_at
    if next_due.tzinfo is None:
        next_due = next_due.replace(tzinfo=timezone.utc)
    return 0 if next_due <= now else 2


def _source_games(db: Session, username: str, puzzle_ids: list[str]) -> dict[str, str]:
    """Map puzzle id -> source game id for the candidates.

    A separate read because the sort works on ids and stats alone, and
    PuzzleStats does not carry the source game. One indexed query on the
    candidate set, not per puzzle.

    Private: ``username`` arrives already folded from ``get_adaptive_puzzles``,
    which is the public boundary. Re-folding here would be harmless but would
    blur where the single fold lives.
    """
    if not puzzle_ids:
        return {}
    rows = db.execute(
        select(Puzzle.id, Puzzle.source_game_id).where(
            Puzzle.username == username, Puzzle.id.in_(puzzle_ids)
        )
    ).all()
    return {pid: game for pid, game in rows}


# At most this share of a session may share one motif. Five forks in a row is a
# worse session than four forks and a pin, even when the fifth fork is the next
# most overdue: the point of a mixed session is that you cannot pattern-match
# your way through it.
_VARIETY_SHARE = 2 / 3

# Motif values that are not motifs. assign_primary_motif returns "blunder"
# when no specific tactic is identified, and it is 65% of puzzle_stats. The
# cap below already exempts a missing motif, for the stated reason that
# capping "unknown" would penalise the puzzles the user has seen least — but no
# row is actually NULL, so without this the exemption never fired and the
# population it was written to protect was the one being capped.
_NON_MOTIFS = frozenset({"blunder"})

# How many puzzles from one game may appear in a session. One, because two
# positions from the same game are not two problems: same opening, same
# opponent, same sitting, often a few moves apart. Measured on the live corpus
# the candidates average 3.19 puzzles per game, so without this a default
# five-puzzle session is drawn from one or two games.
_GAME_CAP = 1


def _vary_session(
    ordered: list[str],
    all_stats: dict[str, PuzzleStats],
    source_games: dict[str, str],
    n: int,
    *,
    cap_motifs: bool,
) -> list[str]:
    """Reorder so one game — or one motif — cannot monopolise a session.

    Deferred puzzles are appended rather than dropped, so this returns the same
    set in a different order — never a shorter list. That says nothing about
    the *session*: the caller slices to ``n``, so reordering does change which
    puzzles get served. That is exactly why this runs per tier. A session with nothing else available stays as concentrated as the
    corpus forces it to be; the caps cannot invent variety that is not there.

    Both constraints are applied in one pass on purpose. Run as two passes they
    fight: whichever runs second re-defers the other's picks, and the winner is
    decided by call order rather than by which grouping matters more.

    Puzzles with no recorded motif are exempt from the motif cap: "unknown" is
    not a motif, and treating it as one would cap the very puzzles the user has
    seen least. A puzzle with no known source game is likewise exempt from the
    game cap rather than being lumped into one pseudo-game.
    """
    if n <= 1 or len(ordered) <= 1:
        return ordered

    motif_cap = max(1, int(n * _VARIETY_SHARE))
    taken: list[str] = []
    deferred: list[str] = []
    motif_counts: dict[str, int] = {}
    game_counts: dict[str, int] = {}

    for pid in ordered:
        # Once the session is full every remaining puzzle is tail padding, so
        # stop deferring and keep the underlying priority order intact.
        if len(taken) >= n:
            taken.append(pid)
            continue

        stats = all_stats.get(pid)
        motif = stats.primary_motif if stats else None
        if motif is not None and motif.strip().lower() in _NON_MOTIFS:
            motif = None
        game = source_games.get(pid)

        over_motif = (
            cap_motifs and motif is not None and motif_counts.get(motif, 0) >= motif_cap
        )
        over_game = game is not None and game_counts.get(game, 0) >= _GAME_CAP
        if over_motif or over_game:
            deferred.append(pid)
            continue

        if motif is not None:
            motif_counts[motif] = motif_counts.get(motif, 0) + 1
        if game is not None:
            game_counts[game] = game_counts.get(game, 0) + 1
        taken.append(pid)

    # Anything held back rejoins immediately after, so the set is unchanged.
    return taken + deferred


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
                PuzzleStats.username == canonical_username(username),
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
            Puzzle.username == canonical_username(username),
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
        PuzzleStats.username == canonical_username(username),
        PuzzleStats.next_due_at.isnot(None),
        PuzzleStats.next_due_at > now,
        PuzzleStats.next_due_at <= now + timedelta(hours=hours),
    )
    return db.scalar(stmt) or 0


def get_due_puzzle_count(db: Session, username: str) -> int:
    """Get count of puzzles due for review.

    A puzzle is "due" when its ``next_due_at`` has passed OR is NULL. A NULL
    ``next_due_at`` marks a never-reviewed ("New") puzzle, which the scheduler
    (``get_adaptive_puzzles``) already surfaces as trainable; counting it here
    keeps the badge/gate consistent with what a session will actually serve.
    Eager-on-save stats rows start with ``next_due_at = NULL``, so excluding
    NULLs would report 0 due for a fresh user who has generated but not yet
    reviewed any puzzles.

    Prefer :func:`get_trainable_puzzle_count` for user-facing totals, because it
    also includes legacy puzzles with no stats row yet.
    """
    # naive-UTC bound: match the naive-UTC storage of next_due_at (see module note)
    now = _utcnow_naive()
    stmt = select(func.count(PuzzleStats.puzzle_id)).where(
        PuzzleStats.username == canonical_username(username),
        or_(PuzzleStats.next_due_at.is_(None), PuzzleStats.next_due_at <= now),
    )
    return db.scalar(stmt) or 0


def get_next_due_date(db: Session, username: str) -> datetime | None:
    """Get the next upcoming due date for a user's puzzles."""
    # naive-UTC bound: match the naive-UTC storage of next_due_at (see module note)
    now = _utcnow_naive()
    stmt = select(func.min(PuzzleStats.next_due_at)).where(
        PuzzleStats.username == canonical_username(username),
        PuzzleStats.next_due_at > now,
    )
    return db.scalar(stmt)
