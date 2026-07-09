"""
Training session endpoints.

Handles session lifecycle: start, complete, and recent sessions query.
"""

import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from pydantic import BaseModel

from services.api.db import get_db
from services.api.models import TrainingSession, RatingSnapshot
from services.ingest import get_player_stats

logger = logging.getLogger(__name__)

# Time controls to auto-snapshot on session completion (best-effort)
AUTO_SNAPSHOT_TIME_CONTROLS = ["rapid", "blitz", "bullet"]

router = APIRouter(prefix="/sessions", tags=["sessions"])


# Request/Response Models
class StartSessionRequest(BaseModel):
    username: str
    n: int
    session_type: str = "standard"  # "standard", "timed", "accuracy_goal"
    target_accuracy: float | None = None  # Target accuracy percentage (0.0-100.0)
    target_time_minutes: int | None = None  # Target session time in minutes
    session_data: dict | None = (
        None  # Flexible storage for session-specific data (e.g., warmup flag)
    )


class StartSessionResponse(BaseModel):
    session_id: str
    requested_n: int
    session_type: str | None = None
    target_accuracy: float | None = None
    target_time_minutes: int | None = None


class CompleteSessionRequest(BaseModel):
    username: str


class SessionSummary(BaseModel):
    session_id: str
    requested_n: int
    pass_count: int
    fail_count: int
    total_time_ms: int
    created_at: datetime
    completed_at: datetime | None
    # Enhanced session fields
    session_type: str | None = None
    target_accuracy: float | None = None
    target_time_minutes: int | None = None
    current_streak: int = 0
    best_streak: int = 0
    hints_used: int = 0


class UseHintRequest(BaseModel):
    username: str


@router.post("/start", response_model=StartSessionResponse)
async def start_session(request: StartSessionRequest, db: Session = Depends(get_db)):
    """
    Start a new training session.

    Creates a session record and returns the session_id for tracking reviews.
    """
    session_id = str(uuid.uuid4())

    session = TrainingSession(
        id=session_id,
        username=request.username,
        requested_n=request.n,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
        session_type=request.session_type,
        target_accuracy=request.target_accuracy,
        target_time_minutes=request.target_time_minutes,
        current_streak=0,
        best_streak=0,
        hints_used=0,
        session_data=request.session_data or {},
    )

    db.add(session)
    db.commit()

    return StartSessionResponse(
        session_id=session_id,
        requested_n=request.n,
        session_type=request.session_type,
        target_accuracy=request.target_accuracy,
        target_time_minutes=request.target_time_minutes,
    )


@router.get("/recent", response_model=list[SessionSummary])
async def get_recent_sessions(
    username: str, limit: int = 10, db: Session = Depends(get_db)
):
    """
    Get recent training sessions for a user.

    Returns sessions ordered by created_at descending.
    Limit: default 10, max 50.
    """
    # Enforce max limit
    limit = min(limit, 50)

    stmt = (
        select(TrainingSession)
        .where(TrainingSession.username == username)
        .order_by(desc(TrainingSession.created_at))
        .limit(limit)
    )

    sessions = db.scalars(stmt).all()

    return [
        SessionSummary(
            session_id=s.id,
            requested_n=s.requested_n,
            pass_count=s.pass_count,
            fail_count=s.fail_count,
            total_time_ms=s.total_time_ms,
            created_at=s.created_at,
            completed_at=s.completed_at,
            session_type=s.session_type,
            target_accuracy=s.target_accuracy,
            target_time_minutes=s.target_time_minutes,
            current_streak=s.current_streak,
            best_streak=s.best_streak,
            hints_used=s.hints_used,
        )
        for s in sessions
    ]


@router.get("/{session_id}", response_model=SessionSummary)
async def get_session(session_id: str, db: Session = Depends(get_db)):
    """
    Get session details by ID.

    Used for validating sessions on page load.
    """
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    session = db.scalars(stmt).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionSummary(
        session_id=session.id,
        requested_n=session.requested_n,
        pass_count=session.pass_count,
        fail_count=session.fail_count,
        total_time_ms=session.total_time_ms,
        created_at=session.created_at,
        completed_at=session.completed_at,
        session_type=session.session_type,
        target_accuracy=session.target_accuracy,
        target_time_minutes=session.target_time_minutes,
        current_streak=session.current_streak,
        best_streak=session.best_streak,
        hints_used=session.hints_used,
    )


async def _auto_snapshot(username: str, session_id: str, db: Session) -> None:
    """Best-effort: record rating snapshots for common time controls on session complete.

    Skips a time control if the latest stored snapshot already has the same rating,
    avoiding duplicate flat entries in the history chart.
    """
    try:
        stats = await get_player_stats(username)
    except Exception as e:
        logger.debug(
            "Auto-snapshot: could not fetch Chess.com stats for %s: %s", username, e
        )
        return

    now = datetime.now(timezone.utc)
    added = 0
    for tc in AUTO_SNAPSHOT_TIME_CONTROLS:
        rating = stats.get(f"chess_{tc}", {}).get("last", {}).get("rating")
        if not rating:
            continue

        # Skip if the most recent snapshot already has the same rating
        latest_stmt = (
            select(RatingSnapshot)
            .where(
                RatingSnapshot.username == username,
                RatingSnapshot.time_control == tc,
            )
            .order_by(RatingSnapshot.recorded_at.desc())
            .limit(1)
        )
        latest = db.scalars(latest_stmt).first()
        if latest and latest.rating == rating:
            continue

        snapshot = RatingSnapshot(
            username=username,
            source="chesscom",
            time_control=tc,
            rating=rating,
            recorded_at=now,
            session_id=session_id,
        )
        db.add(snapshot)
        added += 1

    if added == 0:
        return

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("Auto-snapshot: commit failed for %s: %s", username, e)


@router.post("/{session_id}/complete", response_model=SessionSummary)
async def complete_session(
    session_id: str, request: CompleteSessionRequest, db: Session = Depends(get_db)
):
    """
    Mark a session as complete.

    Idempotent - returns existing summary if already completed.
    Auto-records rating snapshots linked to this session (best-effort).
    """
    # Fetch session
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    session = db.scalars(stmt).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.username != request.username:
        raise HTTPException(status_code=403, detail="Session belongs to different user")

    # If not already completed, set completed_at and auto-snapshot
    should_auto_snapshot = False
    if session.completed_at is None:
        session.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(session)
        should_auto_snapshot = True

    # Build response before auto-snapshot so a rollback inside
    # _auto_snapshot cannot expire the ORM-managed session object.
    result = SessionSummary(
        session_id=session.id,
        requested_n=session.requested_n,
        pass_count=session.pass_count,
        fail_count=session.fail_count,
        total_time_ms=session.total_time_ms,
        created_at=session.created_at,
        completed_at=session.completed_at,
        session_type=session.session_type,
        target_accuracy=session.target_accuracy,
        target_time_minutes=session.target_time_minutes,
        current_streak=session.current_streak,
        best_streak=session.best_streak,
        hints_used=session.hints_used,
    )

    # Best-effort auto-snapshot after response is built
    if should_auto_snapshot:
        await _auto_snapshot(request.username, session_id, db)

    return result


@router.post("/{session_id}/use_hint", response_model=SessionSummary)
async def use_hint(
    session_id: str, request: UseHintRequest, db: Session = Depends(get_db)
):
    """
    Use a hint during a training session.

    Updates the session's hint counter.
    """
    # Fetch session
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    session = db.scalars(stmt).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.username != request.username:
        raise HTTPException(status_code=403, detail="Session belongs to different user")

    if session.completed_at is not None:
        raise HTTPException(status_code=400, detail="Session already completed")

    # Increment hints used
    session.hints_used += 1
    db.commit()
    db.refresh(session)

    return SessionSummary(
        session_id=session.id,
        requested_n=session.requested_n,
        pass_count=session.pass_count,
        fail_count=session.fail_count,
        total_time_ms=session.total_time_ms,
        created_at=session.created_at,
        completed_at=session.completed_at,
        session_type=session.session_type,
        target_accuracy=session.target_accuracy,
        target_time_minutes=session.target_time_minutes,
        current_streak=session.current_streak,
        best_streak=session.best_streak,
        hints_used=session.hints_used,
    )
