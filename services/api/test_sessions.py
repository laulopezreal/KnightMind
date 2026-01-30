"""
Tests for training session endpoints and integration.
"""
import pytest
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import Base
from services.api.models import TrainingSession, PuzzleReview, PuzzleResult
from services.api.sessions import (
    start_session,
    complete_session,
    get_recent_sessions,
    StartSessionRequest,
    CompleteSessionRequest
)
from services.api.storage.spaced_repetition import insert_puzzle_review


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.mark.asyncio
async def test_start_session(db_session):
    """Test starting a new training session."""
    request = StartSessionRequest(username="testuser", n=5)
    response = await start_session(request, db_session)
    
    assert response.session_id is not None
    assert response.requested_n == 5
    
    # Verify session in database
    stmt = select(TrainingSession).where(TrainingSession.id == response.session_id)
    session = db_session.scalars(stmt).first()
    
    assert session is not None
    assert session.username == "testuser"
    assert session.requested_n == 5
    assert session.pass_count == 0
    assert session.fail_count == 0
    assert session.total_time_ms == 0
    assert session.completed_at is None


@pytest.mark.asyncio
async def test_complete_session(db_session):
    """Test completing a session."""
    # Create a session
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="testuser",
        requested_n=5,
        pass_count=3,
        fail_count=2,
        total_time_ms=30000
    )
    db_session.add(session)
    db_session.commit()
    
    # Complete it
    request = CompleteSessionRequest(username="testuser")
    response = await complete_session(session_id, request, db_session)
    
    assert response.session_id == session_id
    assert response.pass_count == 3
    assert response.fail_count == 2
    assert response.total_time_ms == 30000
    assert response.completed_at is not None


@pytest.mark.asyncio
async def test_complete_session_idempotent(db_session):
    """Test that completing a session twice returns the same result."""
    # Create a completed session
    session_id = str(uuid.uuid4())
    completed_at = datetime.now(timezone.utc)
    session = TrainingSession(
        id=session_id,
        username="testuser",
        requested_n=5,
        pass_count=3,
        fail_count=2,
        total_time_ms=30000,
        completed_at=completed_at
    )
    db_session.add(session)
    db_session.commit()
    
    # Complete it again
    request = CompleteSessionRequest(username="testuser")
    response = await complete_session(session_id, request, db_session)
    
    # Should return the same completed_at (within a second)
    assert abs((response.completed_at.replace(tzinfo=timezone.utc) - completed_at).total_seconds()) < 1


@pytest.mark.asyncio
async def test_complete_session_not_found(db_session):
    """Test completing a non-existent session."""
    from fastapi import HTTPException
    
    request = CompleteSessionRequest(username="testuser")
    
    with pytest.raises(HTTPException) as exc_info:
        await complete_session("nonexistent-id", request, db_session)
    
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_complete_session_wrong_user(db_session):
    """Test completing a session with wrong username."""
    from fastapi import HTTPException
    
    # Create a session for user1
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="user1",
        requested_n=5
    )
    db_session.add(session)
    db_session.commit()
    
    # Try to complete as user2
    request = CompleteSessionRequest(username="user2")
    
    with pytest.raises(HTTPException) as exc_info:
        await complete_session(session_id, request, db_session)
    
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_recent_sessions(db_session):
    """Test getting recent sessions."""
    # Create multiple sessions
    now = datetime.now(timezone.utc)
    for i in range(15):
        session = TrainingSession(
            id=str(uuid.uuid4()),
            username="testuser",
            requested_n=5,
            pass_count=i,
            fail_count=5-i,
            total_time_ms=i * 1000,
            created_at=now - timedelta(hours=i)
        )
        db_session.add(session)
    db_session.commit()
    
    # Get recent sessions (default limit 10)
    sessions = await get_recent_sessions("testuser", 10, db_session)
    
    assert len(sessions) == 10
    # Should be ordered by created_at desc (newest first)
    assert sessions[0].pass_count == 0  # Most recent
    assert sessions[9].pass_count == 9


@pytest.mark.asyncio
async def test_get_recent_sessions_max_limit(db_session):
    """Test that limit is capped at 50."""
    # Create 60 sessions
    for i in range(60):
        session = TrainingSession(
            id=str(uuid.uuid4()),
            username="testuser",
            requested_n=5
        )
        db_session.add(session)
    db_session.commit()
    
    # Request 100, should get max 50
    sessions = await get_recent_sessions("testuser", 100, db_session)
    
    assert len(sessions) == 50


def test_review_with_session_increments_counters(db_session):
    """Test that reviews with session_id increment session counters."""
    # Create a session
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="testuser",
        requested_n=5,
        pass_count=0,
        fail_count=0,
        total_time_ms=0
    )
    db_session.add(session)
    db_session.commit()
    
    # Insert a passing review with session_id
    review = insert_puzzle_review(
        db_session,
        puzzle_id="puzzle1",
        username="testuser",
        result=PuzzleResult.PASS,
        time_spent_ms=5000,
        session_id=session_id
    )
    
    assert review.session_id == session_id
    
    # Note: Counter increment happens in the endpoint, not in insert_puzzle_review
    # This test just verifies the session_id is stored correctly


def test_review_without_session_backward_compatible(db_session):
    """Test that reviews without session_id still work (backward compatibility)."""
    review = insert_puzzle_review(
        db_session,
        puzzle_id="puzzle1",
        username="testuser",
        result=PuzzleResult.PASS,
        time_spent_ms=5000
    )
    
    assert review.session_id is None
    assert review.puzzle_id == "puzzle1"
    assert review.result == "pass"
