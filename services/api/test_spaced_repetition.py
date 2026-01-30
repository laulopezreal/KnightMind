import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
from services.api.models import Base
from services.api.storage.spaced_repetition import (
    insert_puzzle_review,
    update_puzzle_stats,
    get_puzzle_stats
)

# Use in-memory SQLite for tests
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

def test_record_pass_review(db_session):
    puzzle_id = "test-puzzle-1"
    username = "testuser"
    
    # Record a pass review
    review = insert_puzzle_review(db_session, puzzle_id, username, "pass", time_spent_ms=5000)
    stats = update_puzzle_stats(db_session, puzzle_id, username, "pass")
    
    assert review.puzzle_id == puzzle_id
    assert review.username == username
    assert review.result == "pass"
    assert review.time_spent_ms == 5000
    
    assert stats.puzzle_id == puzzle_id
    assert stats.attempts == 1
    assert stats.pass_count == 1
    assert stats.fail_count == 0
    assert stats.last_result == "pass"
    assert stats.last_reviewed_at is not None

def test_record_fail_review(db_session):
    puzzle_id = "test-puzzle-2"
    username = "testuser"
    
    # Record a fail review
    update_puzzle_stats(db_session, puzzle_id, username, "fail")
    stats = get_puzzle_stats(db_session, puzzle_id, username)
    
    assert stats.attempts == 1
    assert stats.pass_count == 0
    assert stats.fail_count == 1
    assert stats.last_result == "fail"

def test_sequential_reviews(db_session):
    puzzle_id = "test-puzzle-3"
    username = "testuser"
    
    # 1. First review: Fail
    update_puzzle_stats(db_session, puzzle_id, username, "fail")
    
    # 2. Second review: Pass
    stats = update_puzzle_stats(db_session, puzzle_id, username, "pass")
    
    assert stats.attempts == 2
    assert stats.pass_count == 1
    assert stats.fail_count == 1
    assert stats.last_result == "pass"

def test_get_puzzle_stats_none(db_session):
    stats = get_puzzle_stats(db_session, "non-existent", "testuser")
    assert stats is None
