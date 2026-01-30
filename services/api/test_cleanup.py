"""
Tests for session cleanup job.
"""
import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import Base
from services.api.models import TrainingSession
from services.api.jobs.cleanup_sessions import cleanup_abandoned_sessions


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


def test_cleanup_abandoned_sessions(db_session):
    """Test auto-complete mechanism for abandoned sessions."""
    # 1. Create a recent session (should NOT be cleaned up)
    recent_session = TrainingSession(
        id="recent",
        username="user1",
        requested_n=5,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    db_session.add(recent_session)
    
    # 2. Create an abandoned session (should be cleaned up)
    abandoned_session = TrainingSession(
        id="abandoned",
        username="user1",
        requested_n=5,
        created_at=datetime.now(timezone.utc) - timedelta(hours=25)
    )
    db_session.add(abandoned_session)
    
    # 3. Create an already completed old session (should NOT be changed)
    completed_time = datetime.now(timezone.utc) - timedelta(hours=25)
    completed_session = TrainingSession(
        id="completed",
        username="user1",
        requested_n=5,
        created_at=datetime.now(timezone.utc) - timedelta(hours=26),
        completed_at=completed_time
    )
    db_session.add(completed_session)
    
    db_session.commit()
    
    # Run cleanup with 24h threshold
    count = cleanup_abandoned_sessions(db_session, hours_threshold=24)
    
    assert count == 1
    
    # Verify abandoned session is completed
    db_session.refresh(abandoned_session)
    assert abandoned_session.completed_at is not None
    # Should be completed just now
    assert (datetime.now(timezone.utc) - abandoned_session.completed_at.replace(tzinfo=timezone.utc)).total_seconds() < 10
    
    # Verify recent session is untouched
    db_session.refresh(recent_session)
    assert recent_session.completed_at is None
    
    # Verify completed session is untouched (timestamp didn't change)
    db_session.refresh(completed_session)
    # Handle timezone awareness for SQLite (which returns naive datetimes)
    # The stored time was timezone-aware, but SQLite stripped it.
    # Convert db time to aware for comparison OR strip tz from expected
    actual_completed_at = completed_session.completed_at
    if actual_completed_at.tzinfo is None:
        actual_completed_at = actual_completed_at.replace(tzinfo=timezone.utc)
        
    assert actual_completed_at == completed_time
