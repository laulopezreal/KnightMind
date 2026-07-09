from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import get_db
from services.api.main import app
from services.api.models import (
    Base,
    Game,
    PuzzleStats,
)
from services.api.models import (
    Puzzle as PuzzleModel,
)

# Setup test DB
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def init_db():
    # Force registration of models by importing them

    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(db_session):

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    del app.dependency_overrides[get_db]


@pytest.fixture
def seed_puzzles(db_session):
    """Insert test puzzles directly into the DB."""
    # Create a parent game
    db_session.add(
        Game(
            game_id="g1",
            url="https://chess.com/game/g1",
            username="testuser",
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
    )
    db_session.flush()

    for i, pid in enumerate(["p1", "p2", "p3"]):
        db_session.add(
            PuzzleModel(
                id=pid,
                username="testuser",
                source_game_id="g1",
                ply=i + 1,
                fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                side_to_move="white",
                played_move_uci="e2e3",
                best_move_uci="e2e4",
                eval_before=0.5,
                eval_after=-0.5,
                swing=1.0,
                created_at=datetime.now(timezone.utc),
            )
        )
    db_session.commit()


def test_due_puzzles_priority_and_merge(client, db_session, seed_puzzles):
    # Setup stats: p1 is due, p2 is new, p3 is future
    now = datetime.now(timezone.utc)

    # p1: Due (yesterday)
    s1 = PuzzleStats(
        puzzle_id="p1",
        username="testuser",
        attempts=1,
        pass_count=1,
        last_result="pass",
        interval_days=1,
        ease_factor=2.0,
        next_due_at=now - timedelta(days=1),
    )
    # p3: Future (tomorrow)
    s3 = PuzzleStats(
        puzzle_id="p3",
        username="testuser",
        attempts=1,
        pass_count=1,
        last_result="pass",
        interval_days=1,
        ease_factor=2.0,
        next_due_at=now + timedelta(days=1),
    )
    db_session.add(s1)
    db_session.add(s3)
    db_session.commit()

    # Request 3 puzzles
    response = client.get("/puzzles/due?username=testuser&n=3")
    assert response.status_code == 200
    data = response.json()

    assert data["due_count"] == 1
    assert data["returned_count"] == 3

    puzzles = data["puzzles"]
    # Order should be p1 (due), p2 (new), p3 (future)
    assert puzzles[0]["id"] == "p1"
    assert puzzles[1]["id"] == "p2"
    assert puzzles[2]["id"] == "p3"

    # Check merge
    assert puzzles[0]["attempts"] == 1
    assert puzzles[1]["attempts"] == 0  # New puzzle defaults
    assert puzzles[1]["ease_factor"] == 2.0


def test_review_endpoint(client, db_session, seed_puzzles):
    response = client.post(
        "/puzzles/p1/review",
        json={"username": "testuser", "result": "pass", "time_spent_ms": 3000},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["interval_days"] == 1  # First review pass = 1
    assert data["ease_factor"] == pytest.approx(2.05)
    assert data["stats"]["attempts"] == 1

    # Second review pass
    response = client.post(
        "/puzzles/p1/review",
        json={"username": "testuser", "result": "pass", "time_spent_ms": 2000},
    )
    data = response.json()
    assert data["interval_days"] == 3  # pass after 1 = 3
    assert data["ease_factor"] == pytest.approx(2.1)
    assert data["stats"]["attempts"] == 2


def test_due_puzzles_no_puzzles_returns_404(client):
    response = client.get("/puzzles/due?username=missinguser&n=2")
    assert response.status_code == 404
    assert "no puzzles found" in response.json()["detail"].lower()
