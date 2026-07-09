"""
Integration test for review endpoint with session tracking.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import Base, get_db
from services.api.main import app
from services.api.models import Game, TrainingSession
from services.api.models import Puzzle as PuzzleModel


@pytest.fixture
def test_db():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(test_db):
    """Create a test client with the test database."""

    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _create_puzzle(db, puzzle_id: str, username: str, source_game_id: str, ply: int):
    """Helper: create a Game + Puzzle in the DB."""
    existing_game = db.get(Game, source_game_id)
    if not existing_game:
        db.add(
            Game(
                game_id=source_game_id,
                url=f"https://chess.com/game/{source_game_id}",
                username=username,
                white_username=username,
                black_username="opponent",
                white_result="win",
                black_result="lose",
                time_control="600",
                end_time=1704067200,
                rated=True,
            )
        )
    db.add(
        PuzzleModel(
            id=puzzle_id,
            username=username,
            source_game_id=source_game_id,
            ply=ply,
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def test_review_endpoint_increments_session_counters(client, test_db):
    """
    Integration test: POST /puzzles/{id}/review increments session counters.
    """
    # 1. Create a training session
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="testuser",
        requested_n=5,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
    )
    test_db.add(session)
    test_db.commit()

    # 2. Create a puzzle for the user
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-1", 10)

    # 3. Submit a passing review
    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={
            "username": "testuser",
            "result": "pass",
            "time_spent_ms": 5000,
            "session_id": session_id,
        },
    )
    assert response.status_code == 200

    # 4. Verify counters were incremented
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    updated_session = test_db.scalars(stmt).first()
    assert updated_session.pass_count == 1
    assert updated_session.fail_count == 0
    assert updated_session.total_time_ms == 5000
    assert updated_session.current_streak == 1
    assert updated_session.best_streak == 1

    # 5. Submit a failing review to reset streak
    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={
            "username": "testuser",
            "result": "fail",
            "time_spent_ms": 2000,
            "session_id": session_id,
        },
    )
    assert response.status_code == 200

    test_db.refresh(updated_session)
    assert updated_session.pass_count == 1
    assert updated_session.fail_count == 1
    assert updated_session.total_time_ms == 7000
    assert updated_session.current_streak == 0
    assert updated_session.best_streak == 1


def test_review_endpoint_session_not_found(client, test_db):
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-1", 10)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={
            "username": "testuser",
            "result": "pass",
            "session_id": "missing-session",
        },
    )

    assert response.status_code == 404
    assert "session not found" in response.json()["detail"].lower()


def test_review_endpoint_session_wrong_user(client, test_db):
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="owner",
        requested_n=5,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
    )
    test_db.add(session)
    test_db.commit()

    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-2", 12)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={"username": "testuser", "result": "pass", "session_id": session_id},
    )

    assert response.status_code == 403
    assert "different user" in response.json()["detail"].lower()


def test_review_endpoint_session_completed(client, test_db):
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="testuser",
        requested_n=5,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
        completed_at=datetime.now(timezone.utc),
    )
    test_db.add(session)
    test_db.commit()

    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-3", 14)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={"username": "testuser", "result": "pass", "session_id": session_id},
    )

    assert response.status_code == 400
    assert "already completed" in response.json()["detail"].lower()
