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
    """Due first, then new — and never a puzzle scheduled for the future.

    /puzzles/due used to top the response up to `n` with not-yet-due puzzles,
    which made the UI's "N puzzles due" a lie and corrupted the intervals (an
    early review re-anchors next_due_at on today). Future puzzles are now
    excluded, so a short session is short.
    """
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

    # p1 (due) + p2 (new) are trainable; p3 (due tomorrow) is not.
    assert data["due_count"] == 2
    assert data["returned_count"] == 2

    puzzles = data["puzzles"]
    # Order should be p1 (due), then p2 (new). p3 is withheld until it is due.
    assert [p["id"] for p in puzzles] == ["p1", "p2"]

    # Check merge
    assert puzzles[0]["attempts"] == 1
    assert puzzles[1]["attempts"] == 0  # New puzzle defaults
    assert puzzles[1]["ease_factor"] == 2.0


def test_due_puzzles_never_serves_a_future_puzzle_to_fill_the_session(
    client, db_session, seed_puzzles
):
    """A user with 1 due puzzle gets a 1-puzzle session, not a padded 3.

    Regression: `get_adaptive_puzzles` sorts future puzzles last but never
    dropped them, so `sorted_pids[:n]` happily returned puzzles due weeks out.
    Passing one of those inflated its interval; failing one reset a
    well-learned puzzle to interval 1.
    """
    now = datetime.now(timezone.utc)
    for pid, offset in (("p1", -timedelta(days=1)), ("p2", timedelta(days=30))):
        db_session.add(
            PuzzleStats(
                puzzle_id=pid,
                username="testuser",
                attempts=3,
                pass_count=3,
                last_result="pass",
                interval_days=30,
                ease_factor=2.5,
                next_due_at=now + offset,
            )
        )
    # p3 stays new (no stats) and so remains trainable.
    db_session.add(
        PuzzleStats(
            puzzle_id="p3",
            username="testuser",
            attempts=1,
            pass_count=1,
            last_result="pass",
            interval_days=30,
            ease_factor=2.5,
            next_due_at=now + timedelta(days=30),
        )
    )
    db_session.commit()

    data = client.get("/puzzles/due?username=testuser&n=5").json()
    assert [p["id"] for p in data["puzzles"]] == ["p1"]
    assert data["due_count"] == 1

    # ...and the status endpoint agrees, so the hero card can't promise 3.
    assert client.get("/users/testuser/status").json()["due_count"] == 1


def test_status_due_count_includes_freshly_generated_puzzles(
    client, db_session, seed_puzzles
):
    """Never-reviewed puzzles are trainable even when older ones are not.

    Regression: due_count only counted scheduled-and-arrived puzzles, with an
    all-or-nothing "if there are no stats rows at all, everything is due"
    fallback. A returning user who generated new puzzles while their existing
    ones were scheduled ahead saw due_count == 0 and a disabled "Start Session"
    button sitting on top of a pile of untouched puzzles.
    """
    now = datetime.now(timezone.utc)
    db_session.add(
        PuzzleStats(
            puzzle_id="p1",
            username="testuser",
            attempts=1,
            pass_count=1,
            last_result="pass",
            interval_days=30,
            ease_factor=2.5,
            next_due_at=now + timedelta(days=30),
        )
    )
    db_session.commit()

    # p2 and p3 have never been reviewed.
    assert client.get("/users/testuser/status").json()["due_count"] == 2
    data = client.get("/puzzles/due?username=testuser&n=5").json()
    assert sorted(p["id"] for p in data["puzzles"]) == ["p2", "p3"]


def test_due_puzzles_returns_empty_when_nothing_is_trainable(
    client, db_session, seed_puzzles
):
    """All caught up is an empty 200, not a padded session."""
    now = datetime.now(timezone.utc)
    for pid in ("p1", "p2", "p3"):
        db_session.add(
            PuzzleStats(
                puzzle_id=pid,
                username="testuser",
                attempts=1,
                pass_count=1,
                last_result="pass",
                interval_days=30,
                ease_factor=2.5,
                next_due_at=now + timedelta(days=30),
            )
        )
    db_session.commit()

    response = client.get("/puzzles/due?username=testuser&n=5")
    assert response.status_code == 200
    data = response.json()
    assert data["puzzles"] == []
    assert data["due_count"] == 0
    assert client.get("/users/testuser/status").json()["due_count"] == 0


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
