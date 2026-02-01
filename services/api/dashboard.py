"""
Dashboard analytics module.

Provides comprehensive dashboard data including:
- User activity summary
- Recent form tracking
- Training schedule information
- Improvement trends over time
"""

from datetime import datetime, timezone, timedelta
from typing import Literal
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, func, case
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query

from services.api.db import get_db
from services.api.models import TrainingSession, PuzzleStats, PuzzleReview
from services.api.storage import PuzzleRepository
from services.api.storage.spaced_repetition import get_due_puzzle_count, get_next_due_date


router = APIRouter(prefix="/users", tags=["dashboard"])


# Response Models
class RecentFormData(BaseModel):
    """Recent performance data for last 20 puzzles."""
    last_20_results: list[Literal["pass", "fail"]]
    accuracy: float
    trend: Literal["up", "down", "steady"]


class ScheduleData(BaseModel):
    """Training schedule information."""
    due_now: int
    due_in_4h: int
    next_review_at: datetime | None


class DashboardSummaryResponse(BaseModel):
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
    """Trend data for a single motif."""
    motif: str
    start_accuracy: float
    end_accuracy: float
    change: float
    trend: Literal["up", "down", "steady"]
    data_points: list[TrendDataPoint]


class TrendsResponse(BaseModel):
    """Motif performance trends over time."""
    window_days: int
    motif_trends: list[MotifTrend]


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
            trend="steady"
        )

    # Extract results
    results = [r.result for r in reversed(reviews)]  # Oldest first

    # Calculate accuracy
    pass_count = sum(1 for r in results if r == "pass")
    accuracy = pass_count / len(results) if results else 0.0

    # Calculate trend (compare first half vs second half)
    if len(results) >= 4:
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
    else:
        trend = "steady"

    return RecentFormData(
        last_20_results=results,
        accuracy=accuracy,
        trend=trend
    )


def calculate_training_streak(db: Session, username: str) -> int:
    """
    Calculate consecutive days with completed training sessions.

    Args:
        db: Database session
        username: Username to query

    Returns:
        Number of consecutive days with training
    """
    # Get all unique completed session dates, ordered descending
    stmt = (
        select(func.date(TrainingSession.completed_at))
        .where(
            TrainingSession.username == username,
            TrainingSession.completed_at.isnot(None)
        )
        .distinct()
        .order_by(desc(func.date(TrainingSession.completed_at)))
    )

    session_dates_iter = db.scalars(stmt)

    try:
        latest_session_date = next(session_dates_iter)
    except StopIteration:
        return 0

    today = datetime.now(timezone.utc).date()

    # A current streak must include today or yesterday.
    if latest_session_date not in [today, today - timedelta(days=1)]:
        return 0

    streak = 1
    expected_date = latest_session_date - timedelta(days=1)

    for session_date in session_dates_iter:
        if session_date == expected_date:
            streak += 1
            expected_date -= timedelta(days=1)
        else:
            # Gap in dates, streak is broken.
            break

    return streak


@router.get("/{username}/dashboard", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    username: str,
    db: Session = Depends(get_db)
):
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
            TrainingSession.completed_at.isnot(None)
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
    total_sessions_count = db.scalar(
        select(func.count(TrainingSession.id))
        .where(
            TrainingSession.username == username,
            TrainingSession.completed_at.isnot(None)
        )
    ) or 0

    # Calculate streak
    streak = calculate_training_streak(db, username)

    # Get recent form
    recent_form = calculate_recent_form(db, username)

    # Get schedule info
    due_now = get_due_puzzle_count(db, username)
    next_review = get_next_due_date(db, username)

    # Calculate due in 4 hours
    four_hours_from_now = datetime.now(timezone.utc) + timedelta(hours=4)
    due_in_4h_count = db.scalar(
        select(func.count(PuzzleStats.puzzle_id))
        .where(
            PuzzleStats.username == username,
            PuzzleStats.next_due_at.isnot(None),
            PuzzleStats.next_due_at <= four_hours_from_now
        )
    ) or 0

    # Subtract already due puzzles
    due_in_4h_count = max(0, due_in_4h_count - due_now)

    schedule = ScheduleData(
        due_now=due_now,
        due_in_4h=due_in_4h_count,
        next_review_at=next_review
    )

    return DashboardSummaryResponse(
        username=username,
        last_session_at=last_session_at,
        days_since_last_session=days_since_last,
        total_sessions=total_sessions_count,
        training_streak_days=streak,
        recent_form=recent_form,
        schedule=schedule,
        needs_warmup=needs_warmup
    )


@router.get("/{username}/trends", response_model=TrendsResponse)
async def get_motif_trends(
    username: str,
    window: int = Query(30, ge=7, le=90, description="Number of days to analyze"),
    db: Session = Depends(get_db)
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

    # Get all reviews within the window, grouped by motif and week
    # Join reviews with stats to get motif information
    stmt = (
        select(
            PuzzleStats.primary_motif,
            func.date(PuzzleReview.reviewed_at).label('week'),
            func.count(PuzzleReview.id).label('total'),
            func.sum(case((PuzzleReview.result == 'pass', 1), else_=0)).label('passed')
        )
        .join(PuzzleStats, PuzzleReview.puzzle_id == PuzzleStats.puzzle_id)
        .where(
            PuzzleReview.username == username,
            PuzzleReview.reviewed_at >= cutoff_date,
            PuzzleStats.primary_motif.isnot(None)
        )
        .group_by(PuzzleStats.primary_motif, func.date(PuzzleReview.reviewed_at))
        .order_by(PuzzleStats.primary_motif, func.date(PuzzleReview.reviewed_at))
    )

    results = db.execute(stmt).all()

    # Organize by motif
    motif_data: dict[str, list[tuple[datetime, float]]] = {}

    for row in results:
        motif = row.primary_motif
        week = row.week
        total = row.total or 0
        passed = row.passed or 0

        if total == 0:
            continue

        accuracy = passed / total

        if motif not in motif_data:
            motif_data[motif] = []

        motif_data[motif].append((week, accuracy))

    # Build trend objects
    trends = []

    for motif, data_points in motif_data.items():
        if len(data_points) < 2:
            # Not enough data for trend
            continue

        # Sort by date
        data_points.sort(key=lambda x: x[0])

        # Get start and end accuracy
        start_accuracy = data_points[0][1]
        end_accuracy = data_points[-1][1]
        change = end_accuracy - start_accuracy

        # Determine trend
        if change > 0.05:
            trend = "up"
        elif change < -0.05:
            trend = "down"
        else:
            trend = "steady"

        # Format data points
        formatted_points = [
            TrendDataPoint(
                date=date.strftime('%Y-%m-%d'),
                accuracy=round(accuracy, 3)
            )
            for date, accuracy in data_points
        ]

        trends.append(MotifTrend(
            motif=motif,
            start_accuracy=round(start_accuracy, 3),
            end_accuracy=round(end_accuracy, 3),
            change=round(change, 3),
            trend=trend,
            data_points=formatted_points
        ))

    # Sort by change (worst to best)
    trends.sort(key=lambda t: t.change)

    return TrendsResponse(
        window_days=window,
        motif_trends=trends
    )
