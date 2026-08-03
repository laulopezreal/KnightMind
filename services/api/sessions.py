"""
Training session endpoints.

Handles session lifecycle: start, complete, and recent sessions query.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from services.api.db import get_db
from services.api.identity import assert_owns_username, require_account
from services.api.models import Account, TrainingSession
from services.api.ratings_auto import auto_snapshot
from services.api.usernames import Username

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


# Request/Response Models
class StartSessionRequest(BaseModel):
    username: Username
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
    username: Username


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
    # Surfaced from session_data so a resumed session is ordered the same way
    # it was served. Reading the focus back off the URL instead made the order
    # depend on how the user navigated back — see the resume path in
    # usePuzzleSession.
    focus_cause: str | None = None
    focus_opening: str | None = None
    focus_opening_scope: str | None = None
    # Same reasoning, and the stakes are higher: motif *filters* the queue
    # rather than merely biasing it, so losing it on resume changes the set of
    # puzzles, not just their order.
    motif: str | None = None


class UseHintRequest(BaseModel):
    username: Username


@router.post("/start", response_model=StartSessionResponse)
async def start_session(
    request: StartSessionRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Start a new training session.

    Creates a session record and returns the session_id for tracking reviews.
    """
    assert_owns_username(account, request.username, db)
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
    username: Username,
    limit: int = 10,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Get recent training sessions for a user.

    Returns sessions ordered by created_at descending.
    Limit: default 10, max 50.
    """
    assert_owns_username(account, username, db)
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
async def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Get session details by ID.

    Used for validating sessions on page load.
    """
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    session = db.scalars(stmt).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Object-level ownership: 404 (not 403) so foreign session ids aren't confirmed.
    assert_owns_username(account, session.username, db, status_code=404)

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
        focus_cause=(session.session_data or {}).get("focus_cause"),
        focus_opening=(session.session_data or {}).get("focus_opening"),
        focus_opening_scope=(session.session_data or {}).get("focus_opening_scope"),
        motif=(session.session_data or {}).get("motif"),
    )


@router.post("/{session_id}/complete", response_model=SessionSummary)
async def complete_session(
    session_id: str,
    request: CompleteSessionRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
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

    # Real object-ownership against the authenticated account (404 hides
    # foreign session ids). The self-referential username check below is only
    # meaningful once request.username is trusted, which it now is under auth.
    assert_owns_username(account, session.username, db, status_code=404)

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
    # auto_snapshot cannot expire the ORM-managed session object.
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
        focus_cause=(session.session_data or {}).get("focus_cause"),
        focus_opening=(session.session_data or {}).get("focus_opening"),
        focus_opening_scope=(session.session_data or {}).get("focus_opening_scope"),
        motif=(session.session_data or {}).get("motif"),
    )

    # Best-effort auto-snapshot after response is built
    if should_auto_snapshot:
        await auto_snapshot(request.username, db, session_id=session_id)

    return result


@router.post("/{session_id}/use_hint", response_model=SessionSummary)
async def use_hint(
    session_id: str,
    request: UseHintRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
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

    # Object-ownership against the account (404 hides foreign session ids).
    assert_owns_username(account, session.username, db, status_code=404)

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
        focus_cause=(session.session_data or {}).get("focus_cause"),
        focus_opening=(session.session_data or {}).get("focus_opening"),
        focus_opening_scope=(session.session_data or {}).get("focus_opening_scope"),
        motif=(session.session_data or {}).get("motif"),
    )
