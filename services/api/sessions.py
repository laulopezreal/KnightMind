"""
Training session endpoints.

Handles session lifecycle: start, complete, and recent sessions query.
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from pydantic import BaseModel

from services.api.db import get_db
from services.api.models import TrainingSession

router = APIRouter(prefix="/sessions", tags=["sessions"])


# Request/Response Models
class StartSessionRequest(BaseModel):
    username: str
    n: int


class StartSessionResponse(BaseModel):
    session_id: str
    requested_n: int


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


@router.post("/start", response_model=StartSessionResponse)
async def start_session(
    request: StartSessionRequest,
    db: Session = Depends(get_db)
):
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
        total_time_ms=0
    )
    
    db.add(session)
    db.commit()
    
    return StartSessionResponse(
        session_id=session_id,
        requested_n=request.n
    )


@router.post("/{session_id}/complete", response_model=SessionSummary)
async def complete_session(
    session_id: str,
    request: CompleteSessionRequest,
    db: Session = Depends(get_db)
):
    """
    Mark a session as complete.
    
    Idempotent - returns existing summary if already completed.
    """
    # Fetch session
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    session = db.scalars(stmt).first()
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.username != request.username:
        raise HTTPException(status_code=403, detail="Session belongs to different user")
    
    # If not already completed, set completed_at
    if session.completed_at is None:
        session.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(session)
    
    return SessionSummary(
        session_id=session.id,
        requested_n=session.requested_n,
        pass_count=session.pass_count,
        fail_count=session.fail_count,
        total_time_ms=session.total_time_ms,
        created_at=session.created_at,
        completed_at=session.completed_at
    )


@router.get("/recent", response_model=list[SessionSummary])
async def get_recent_sessions(
    username: str,
    limit: int = 10,
    db: Session = Depends(get_db)
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
            completed_at=s.completed_at
        )
        for s in sessions
    ]
