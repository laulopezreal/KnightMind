import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

from services.api.main import app
from services.api.db import get_db
from services.api.models import Base, PuzzleStats, PuzzleReview

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
    from services.api.models import Job, FenEvalCache, PuzzleStats, PuzzleReview
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
    from services.api.db import get_db
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    del app.dependency_overrides[get_db]

@pytest.fixture
def mock_puzzles(tmp_path, monkeypatch):
    """Setup a mock puzzle index and storage data."""
    data_dir = tmp_path / "data"
    puzzle_index_dir = data_dir / "puzzle_index"
    puzzles_dir = data_dir / "puzzles" / "testuser"
    
    puzzle_index_dir.mkdir(parents=True)
    puzzles_dir.mkdir(parents=True)
    
    index = {
        "key1": "p1",
        "key2": "p2",
        "key3": "p3"
    }
    with open(puzzle_index_dir / "testuser.json", "w") as f:
        json.dump(index, f)
        
    for pid in ["p1", "p2", "p3"]:
        puzzle_data = {
            "id": pid,
            "username": "testuser",
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "best_move_uci": "e2e4",
            "side_to_move": "white",
            "source_game_id": "g1",
            "ply": 1,
            "played_move_uci": "e2e3",
            "eval_before": 0.5,
            "eval_after": -0.5,
            "swing": 1.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "used_on": None
        }
        with open(puzzles_dir / f"{pid}.json", "w") as f:
            json.dump(puzzle_data, f)
            
    # Point storage to tmp_path
    monkeypatch.setattr("services.api.storage.puzzles.Path", lambda x: Path(tmp_path / x) if x == "data" else Path(x))
    # This might be tricky if get_puzzle_storage is already initialized. 
    # Let's try a direct monkeypatch of the storage instance.
    from services.api.storage.puzzles import PuzzleStorage, _default_puzzle_storage
    mock_storage = PuzzleStorage(base_path=data_dir)
    monkeypatch.setattr("services.api.storage.puzzles._default_puzzle_storage", mock_storage)
    
    return data_dir

def test_due_puzzles_priority_and_merge(client, db_session, mock_puzzles):
    # Setup stats: p1 is due, p2 is new, p3 is future
    now = datetime.now(timezone.utc)
    
    # p1: Due (yesterday)
    s1 = PuzzleStats(
        puzzle_id="p1", username="testuser", attempts=1, pass_count=1,
        last_result="pass", interval_days=1, ease_factor=2.0,
        next_due_at=now - timedelta(days=1)
    )
    # p3: Future (tomorrow)
    s3 = PuzzleStats(
        puzzle_id="p3", username="testuser", attempts=1, pass_count=1,
        last_result="pass", interval_days=1, ease_factor=2.0,
        next_due_at=now + timedelta(days=1)
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
    assert puzzles[1]["attempts"] == 0 # New puzzle defaults
    assert puzzles[1]["ease_factor"] == 2.0

def test_review_endpoint(client, db_session, mock_puzzles):
    response = client.post("/puzzles/p1/review", json={
        "username": "testuser",
        "result": "pass",
        "time_spent_ms": 3000
    })
    assert response.status_code == 200
    data = response.json()
    
    assert data["interval_days"] == 1 # First review pass = 1
    assert data["ease_factor"] == pytest.approx(2.05)
    assert data["stats"]["attempts"] == 1
    
    # Second review pass
    response = client.post("/puzzles/p1/review", json={
        "username": "testuser",
        "result": "pass",
        "time_spent_ms": 2000
    })
    data = response.json()
    assert data["interval_days"] == 3 # pass after 1 = 3
    assert data["ease_factor"] == pytest.approx(2.1)
    assert data["stats"]["attempts"] == 2


def test_due_puzzles_no_puzzles_returns_404(client, mock_puzzles):
    response = client.get("/puzzles/due?username=missinguser&n=2")
    assert response.status_code == 404
    assert "no puzzles found" in response.json()["detail"].lower()
