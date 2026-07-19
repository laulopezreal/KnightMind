"""
Dashboard analytics module.

Provides comprehensive dashboard data including:
- User activity summary
- Recent form tracking
- Training schedule information
- Improvement trends over time
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session

from services.api.analytics_confidence import (
    MIN_REVIEWS_FOR_FORM_TREND,
    MIN_REVIEWS_FOR_MOTIF_TREND,
)
from services.api.day_boundary import day_expr, day_key, utc_today
from services.api.db import get_db
from services.api.models import PuzzleReview, PuzzleStats, TrainingSession
from services.api.storage.spaced_repetition import (
    get_due_puzzle_count,
    get_next_due_date,
)

router = APIRouter(prefix="/users", tags=["dashboard"])


# Response Models
class RecentFormData(BaseModel):
    """Recent performance data for last 20 puzzles.

    Descriptive only: `accuracy` and `trend` summarize observed results, they
    are not a forecast. `trend` stays "steady" until at least
    MIN_REVIEWS_FOR_FORM_TREND reviews exist; `insufficient_data` says whether
    the sample was too small to read a direction.
    """

    last_20_results: list[Literal["pass", "fail"]]
    accuracy: float
    trend: Literal["up", "down", "steady"]
    sample_size: int
    insufficient_data: bool


class ScheduleData(BaseModel):
    """Training schedule information."""

    due_now: int
    due_in_4h: int
    next_review_at: datetime | None


class DashboardSummary(BaseModel):
    """Complete dashboard summary."""

    username: str
    last_session_at: datetime | None
    days_since_last_session: int
    total_sessions: int
    training_streak_days: int
    recent_form: RecentFormData
    schedule: ScheduleData
    needs_warmup: bool


class TrendDataPoint(BaseModel):
    """Single data point in a trend."""

    date: str  # ISO date string
    accuracy: float


class MotifTrend(BaseModel):
    """Trend data for a single motif.

    Descriptive only. `trend` stays "steady" (and `insufficient_data` is True)
    until the motif has at least MIN_REVIEWS_FOR_MOTIF_TREND reviews in the
    window, so a two-attempt swing is never rendered as a direction.
    """

    motif: str
    start_accuracy: float
    end_accuracy: float
    change: float
    trend: Literal["up", "down", "steady"]
    total_reviews: int
    insufficient_data: bool
    data_points: list[TrendDataPoint]


class TrendsResponse(BaseModel):
    """Motif performance trends over time."""

    window_days: int
    motif_trends: list[MotifTrend]


class TrickyPuzzle(BaseModel):
    """A puzzle the user has struggled with."""

    puzzle_id: str
    title: str
    fail_count: int
    last_attempted_at: datetime


class TrickyPuzzlesResponse(BaseModel):
    """Response containing tricky puzzles."""

    puzzles: list[TrickyPuzzle]
    total_count: int


def calculate_recent_form(db: Session, username: str) -> RecentFormData:
    """
    Calculate recent form from last 20 puzzle reviews.

    Args:
        db: Database session
        username: Username to query

    Returns:
        Recent form data with results, accuracy, and trend
    """
    # Get last 20 reviews
    stmt = (
        select(PuzzleReview)
        .where(PuzzleReview.username == username)
        .order_by(desc(PuzzleReview.reviewed_at))
        .limit(20)
    )

    reviews = db.scalars(stmt).all()

    if not reviews:
        return RecentFormData(
            last_20_results=[],
            accuracy=0.0,
            trend="steady",
            sample_size=0,
            insufficient_data=True,
        )

    # Extract results
    results = [r.result for r in reversed(reviews)]  # Oldest first

    # Calculate accuracy
    pass_count = sum(1 for r in results if r == "pass")
    accuracy = pass_count / len(results) if results else 0.0

    # Trend is directional only with enough reviews; below the threshold the
    # honest signal is "steady" + insufficient_data (a 2/2 split is not a trend).
    insufficient_data = len(results) < MIN_REVIEWS_FOR_FORM_TREND
    if insufficient_data:
        trend = "steady"
    else:
        mid = len(results) // 2
        first_half = results[:mid]
        second_half = results[mid:]

        first_acc = sum(1 for r in first_half if r == "pass") / len(first_half)
        second_acc = sum(1 for r in second_half if r == "pass") / len(second_half)

        if second_acc > first_acc + 0.1:
            trend = "up"
        elif second_acc < first_acc - 0.1:
            trend = "down"
        else:
            trend = "steady"

    return RecentFormData(
        last_20_results=results,
        accuracy=accuracy,
        trend=trend,
        sample_size=len(results),
        insufficient_data=insufficient_data,
    )


def calculate_training_streak(db: Session, username: str) -> int:
    """
    Calculate consecutive days with completed training sessions.

    Args:
        db: Database session
        username: Username to query

    Returns:
        Number of consecutive days with training

    Day boundary is UTC (see services.api.day_boundary). ``func.date`` returns a
    string on SQLite but a ``date`` on Postgres; both are normalized to
    "YYYY-MM-DD" strings via ``day_key`` so the comparison is backend-agnostic.
    Previously the raw SQLite string was compared to Python ``date`` objects,
    which never matched — collapsing every SQLite streak to 0.
    """
    # Get all unique completed session (UTC) dates, ordered descending
    stmt = (
        select(day_expr(TrainingSession.completed_at))
        .where(
            TrainingSession.username == username,
            TrainingSession.completed_at.isnot(None),
        )
        .distinct()
        .order_by(desc(day_expr(TrainingSession.completed_at)))
    )

    session_days = [day_key(d) for d in db.scalars(stmt) if d is not None]

    if not session_days:
        return 0

    latest_day = session_days[0]
    today = utc_today()
    valid_latest = {day_key(today), day_key(today - timedelta(days=1))}

    # A current streak must include today or yesterday (UTC).
    if latest_day not in valid_latest:
        return 0

    # Walk backwards one UTC day at a time until a gap appears.
    streak = 1
    latest_date = datetime.strptime(latest_day, "%Y-%m-%d").date()
    expected_day = day_key(latest_date - timedelta(days=1))

    for session_day in session_days[1:]:
        if session_day == expected_day:
            streak += 1
            expected_date = datetime.strptime(
                session_day, "%Y-%m-%d"
            ).date() - timedelta(days=1)
            expected_day = day_key(expected_date)
        else:
            # Gap in dates, streak is broken.
            break

    return streak


@router.get("/{username}/dashboard", response_model=DashboardSummary)
async def get_dashboard_summary(username: str, db: Session = Depends(get_db)):
    """
    Get comprehensive dashboard summary for a user.

    Returns:
        - Recent activity stats
        - Training schedule
        - Recent form (last 20 puzzles)
        - Streak information
    """
    # Get last session
    stmt = (
        select(TrainingSession)
        .where(
            TrainingSession.username == username,
            TrainingSession.completed_at.isnot(None),
        )
        .order_by(desc(TrainingSession.completed_at))
    )

    last_session = db.scalars(stmt).first()

    last_session_at = last_session.completed_at if last_session else None
    days_since_last = 0
    needs_warmup = False

    if last_session_at:
        last_session_at = last_session_at.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - last_session_at
        days_since_last = delta.days
        needs_warmup = days_since_last > 7

    # Count total completed sessions
    total_sessions_count = (
        db.scalar(
            select(func.count(TrainingSession.id)).where(
                TrainingSession.username == username,
                TrainingSession.completed_at.isnot(None),
            )
        )
        or 0
    )

    # Calculate streak
    streak = calculate_training_streak(db, username)

    # Get recent form
    recent_form = calculate_recent_form(db, username)

    # Get schedule info
    due_now = get_due_puzzle_count(db, username)
    next_review = get_next_due_date(db, username)

    # Calculate due in 4 hours
    four_hours_from_now = datetime.now(timezone.utc) + timedelta(hours=4)
    due_in_4h_count = (
        db.scalar(
            select(func.count(PuzzleStats.puzzle_id)).where(
                PuzzleStats.username == username,
                PuzzleStats.next_due_at.isnot(None),
                PuzzleStats.next_due_at <= four_hours_from_now,
            )
        )
        or 0
    )

    # Subtract already due puzzles
    due_in_4h_count = max(0, due_in_4h_count - due_now)

    schedule = ScheduleData(
        due_now=due_now, due_in_4h=due_in_4h_count, next_review_at=next_review
    )

    return DashboardSummary(
        username=username,
        last_session_at=last_session_at,
        days_since_last_session=days_since_last,
        total_sessions=total_sessions_count,
        training_streak_days=streak,
        recent_form=recent_form,
        schedule=schedule,
        needs_warmup=needs_warmup,
    )


@router.get("/{username}/trends", response_model=TrendsResponse)
async def get_motif_trends(
    username: str,
    window: int = Query(30, ge=7, le=90, description="Number of days to analyze"),
    db: Session = Depends(get_db),
):
    """
    Get motif performance trends over time.

    Args:
        username: Username to query
        window: Number of days to analyze (default 30, max 90)

    Returns:
        Trend data for each motif showing improvement/decline
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=window)

    # Get all reviews within the window, grouped by motif and UTC day.
    # Join reviews with stats to get motif information.
    stmt = (
        select(
            PuzzleStats.primary_motif,
            day_expr(PuzzleReview.reviewed_at).label("day"),
            func.count(PuzzleReview.id).label("total"),
            func.sum(case((PuzzleReview.result == "pass", 1), else_=0)).label("passed"),
        )
        .join(PuzzleStats, PuzzleReview.puzzle_id == PuzzleStats.puzzle_id)
        .where(
            PuzzleReview.username == username,
            PuzzleReview.reviewed_at >= cutoff_date,
            PuzzleStats.primary_motif.isnot(None),
        )
        .group_by(PuzzleStats.primary_motif, day_expr(PuzzleReview.reviewed_at))
        .order_by(PuzzleStats.primary_motif, day_expr(PuzzleReview.reviewed_at))
    )

    results = db.execute(stmt).all()

    # Organize by motif. day_key normalizes SQLite(str)/Postgres(date) buckets.
    motif_data: dict[str, list[tuple[str, float]]] = {}
    motif_review_totals: dict[str, int] = {}

    for row in results:
        motif = row.primary_motif
        day = day_key(row.day)
        total = row.total or 0
        passed = row.passed or 0

        if total == 0:
            continue

        accuracy = passed / total

        motif_data.setdefault(motif, []).append((day, accuracy))
        motif_review_totals[motif] = motif_review_totals.get(motif, 0) + total

    # Build trend objects
    trends = []

    for motif, data_points in motif_data.items():
        if len(data_points) < 2:
            # Need at least two day buckets to describe any movement.
            continue

        # Sort by day (string dates sort chronologically as "YYYY-MM-DD").
        data_points.sort(key=lambda x: x[0])

        total_reviews = motif_review_totals[motif]

        # Get start and end accuracy
        start_accuracy = data_points[0][1]
        end_accuracy = data_points[-1][1]
        change = end_accuracy - start_accuracy

        # Direction is descriptive and only reported once the sample is big
        # enough; otherwise a two-attempt swing would read as a "trend".
        insufficient_data = total_reviews < MIN_REVIEWS_FOR_MOTIF_TREND
        if insufficient_data:
            trend = "steady"
        elif change > 0.05:
            trend = "up"
        elif change < -0.05:
            trend = "down"
        else:
            trend = "steady"

        formatted_points = [
            TrendDataPoint(date=day, accuracy=round(accuracy, 3))
            for day, accuracy in data_points
        ]

        trends.append(
            MotifTrend(
                motif=motif,
                start_accuracy=round(start_accuracy, 3),
                end_accuracy=round(end_accuracy, 3),
                change=round(change, 3),
                trend=trend,
                total_reviews=total_reviews,
                insufficient_data=insufficient_data,
                data_points=formatted_points,
            )
        )

    # Sort by change (worst to best)
    trends.sort(key=lambda t: t.change)

    return TrendsResponse(window_days=window, motif_trends=trends)


@router.get("/{username}/puzzles/tricky", response_model=TrickyPuzzlesResponse)
async def get_tricky_puzzles(
    username: str,
    limit: int = Query(
        5, ge=1, le=20, description="Maximum number of puzzles to return"
    ),
    db: Session = Depends(get_db),
):
    """
    Get puzzles the user has failed multiple times.

    Returns puzzles with 2+ failures, sorted by:
    1. Fail count (descending)
    2. Most recently attempted

    Args:
        username: Username to query
        limit: Maximum number of puzzles to return (default 5, max 20)

    Returns:
        List of tricky puzzles with fail counts and last attempt timestamps
    """
    # Base filter for tricky puzzles (shared by count and result queries)
    base_query = select(PuzzleStats).where(
        PuzzleStats.username == username,
        PuzzleStats.fail_count >= 2,
        PuzzleStats.last_reviewed_at.isnot(None),
    )

    # Count total tricky puzzles
    total_count = (
        db.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    )

    # Get paginated and sorted results
    stats = db.scalars(
        base_query.order_by(
            desc(PuzzleStats.fail_count), desc(PuzzleStats.last_reviewed_at)
        ).limit(limit)
    ).all()

    puzzles = [
        TrickyPuzzle(
            puzzle_id=stat.puzzle_id,
            title=stat.title or "Untitled Puzzle",
            fail_count=stat.fail_count,
            last_attempted_at=stat.last_reviewed_at,
        )
        for stat in stats
    ]

    return TrickyPuzzlesResponse(puzzles=puzzles, total_count=total_count)
