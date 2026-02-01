from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest
from services.api.main import app, get_opponent_rating_from_pgn, calculate_expected_score
from fastapi.testclient import TestClient
from services.api.db import get_db
from services.api.models import Base, RatingSnapshot
from services.api.storage.games import GameStorage

client = TestClient(app)

def test_pgn_parsing():
    pgn = '[Event "Live Chess"]\n[White "player1"]\n[Black "player2"]\n[Result "1-0"]\n[WhiteElo "1500"]\n[BlackElo "1400"]\n...'
    
    # Check black elo (user is white)
    assert get_opponent_rating_from_pgn(pgn, user_is_white=True) == 1400
    
    # Check white elo (user is black)
    assert get_opponent_rating_from_pgn(pgn, user_is_white=False) == 1500
    
    # Missing elo
    pgn_missing = '[Event "Live Chess"]\n[White "player1"]'
    assert get_opponent_rating_from_pgn(pgn_missing, user_is_white=True) is None

def test_expected_score_logic():
    # If opp=1400, ref=1400 -> 1 / (1 + 1) = 0.5
    assert calculate_expected_score(1400, 1400) == 0.5
    
    # If opp=1800, ref=1400 -> diff=400 -> 1 / (1 + 10^1) = 1/11 = 0.09
    assert round(calculate_expected_score(1400, 1800), 2) == 0.09
    
    # If opp=1000, ref=1400 -> diff=-400 -> 1 / (1 + 10^-1) = 1/1.1 = 0.909
    assert round(calculate_expected_score(1400, 1000), 3) == 0.909


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client_with_db(db_session):
    def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@patch("services.api.main.get_player_stats")
def test_create_rating_snapshot_success(mock_get_stats, client_with_db, db_session):
    mock_get_stats.return_value = {"chess_rapid": {"last": {"rating": 1500}}}

    response = client_with_db.post("/ratings/snapshot", json={
        "username": "testuser",
        "time_control": "rapid"
    })

    assert response.status_code == 200
    data = response.json()
    assert data["rating"] == 1500

    stmt = select(RatingSnapshot).where(RatingSnapshot.username == "testuser")
    snapshot = db_session.scalars(stmt).first()
    assert snapshot is not None
    assert snapshot.rating == 1500


@patch("services.api.main.get_player_stats")
def test_create_rating_snapshot_missing_rating(mock_get_stats, client_with_db):
    mock_get_stats.return_value = {"chess_rapid": {"last": {}}}

    response = client_with_db.post("/ratings/snapshot", json={
        "username": "testuser",
        "time_control": "rapid"
    })

    assert response.status_code == 502
    assert "could not find rating" in response.json()["detail"].lower()


def test_explain_rating_changes_basic(client_with_db, db_session, tmp_path, monkeypatch):
    storage = GameStorage(base_path=tmp_path)
    monkeypatch.setattr("services.api.main.get_storage", lambda: storage)

    since_time = datetime.now(timezone.utc) - timedelta(days=2)

    snapshot = RatingSnapshot(
        username="testuser",
        source="chesscom",
        time_control="rapid",
        rating=1400,
        recorded_at=since_time + timedelta(hours=1)
    )
    db_session.add(snapshot)
    db_session.commit()

    pgn_win = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]
[Result "1-0"]
[WhiteElo "1400"]
[BlackElo "1600"]

1. e4 e5 2. Nf3 Nc6 1-0"""

    for i in range(2):
        storage.store_game(
            username="testuser",
            url=f"https://chess.com/game/{i}",
            pgn=pgn_win,
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="loss",
            time_control="rapid",
            end_time=int((since_time + timedelta(hours=2 + i)).timestamp()),
            rated=True,
        )

    response = client_with_db.get(
        "/ratings/explain",
        params={
            "username": "testuser",
            "time_control": "rapid",
            "since": since_time.isoformat(),
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["wins"] == 2
    assert data["stats"]["losses"] == 0
    assert data["rating"]["reference_rating"] == 1400
    assert data["rating"]["reference_is_approx"] is False
    assert any("outperformed" in driver.lower() for driver in data["drivers"])
