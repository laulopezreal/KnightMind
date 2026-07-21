import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import get_db
from services.api.main import (
    app,
    calculate_expected_score,
    get_opponent_rating_from_pgn,
)
from services.api.models import Base, Game, RatingSnapshot
from services.api.time_control import classify_time_control

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


def test_classify_time_control_bullet():
    assert classify_time_control("60") == "bullet"
    assert classify_time_control("30") == "bullet"
    assert classify_time_control("120") == "bullet"
    assert classify_time_control("60+0") == "bullet"
    assert classify_time_control("60+1") == "bullet"  # 60 + 40*1 = 100 < 180


def test_classify_time_control_blitz():
    assert classify_time_control("180") == "blitz"
    assert classify_time_control("300") == "blitz"
    assert classify_time_control("180+0") == "blitz"
    assert classify_time_control("180+2") == "blitz"  # 180 + 40*2 = 260 < 600
    assert classify_time_control("300+0") == "blitz"
    assert classify_time_control("300+5") == "blitz"  # 300 + 40*5 = 500 < 600


def test_classify_time_control_rapid():
    assert classify_time_control("600") == "rapid"
    assert classify_time_control("900") == "rapid"
    assert classify_time_control("1800") == "rapid"
    assert classify_time_control("600+0") == "rapid"
    assert classify_time_control("600+5") == "rapid"  # 600 + 40*5 = 800
    assert classify_time_control("300+10") == "rapid"  # 300 + 40*10 = 700 >= 600


def test_classify_time_control_passthrough():
    """Already-classified strings should pass through unchanged."""
    assert classify_time_control("rapid") == "rapid"
    assert classify_time_control("blitz") == "blitz"
    assert classify_time_control("bullet") == "bullet"
    assert classify_time_control("Rapid") == "rapid"


def test_classify_time_control_edge_cases():
    """Boundary cases."""
    assert classify_time_control("179") == "bullet"  # just under 180
    assert classify_time_control("180") == "blitz"  # exactly 180
    assert classify_time_control("599") == "blitz"  # just under 600
    assert classify_time_control("600") == "rapid"  # exactly 600


def test_classify_time_control_unrecognized():
    """Unrecognized formats should return None instead of silently defaulting."""
    assert classify_time_control("daily") is None
    assert classify_time_control("1/259200") is None
    assert classify_time_control("unknown") is None


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
def client_with_db(db_session, monkeypatch):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    # `with TestClient(app)` runs the app lifespan, whose puzzle-identity
    # backfill uses the module-level SessionLocal directly (dependency
    # overrides don't apply there). Point it at the test engine so startup
    # never touches the real dev-SQLite database (./knightmind.db) — same
    # pattern as test_ops.py's test_db_instance fixture.
    monkeypatch.setattr(
        "services.api.main.SessionLocal", sessionmaker(bind=db_session.get_bind())
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@patch("services.api.main.get_player_stats")
def test_create_rating_snapshot_success(mock_get_stats, client_with_db, db_session):
    mock_get_stats.return_value = {"chess_rapid": {"last": {"rating": 1500}}}

    response = client_with_db.post(
        "/ratings/snapshot", json={"username": "testuser", "time_control": "rapid"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["rating"] == 1500

    stmt = select(RatingSnapshot).where(RatingSnapshot.username == "testuser")
    snapshot = db_session.scalars(stmt).first()
    assert snapshot is not None
    assert snapshot.rating == 1500


@patch("services.api.main.get_player_stats")
def test_create_rating_snapshot_hides_internal_error(mock_get_stats, client_with_db):
    """dim 23: an unexpected exception must not leak its raw text to the caller.

    The catch-all returns a generic 500 detail; the real error is only logged
    server-side. Domain exceptions (UserNotFound/Network) keep their safe 502
    messages — this test only covers the unexpected path.
    """
    secret = "psql://user:hunter2@db.internal/knightmind connection refused"
    mock_get_stats.side_effect = RuntimeError(secret)

    response = client_with_db.post(
        "/ratings/snapshot", json={"username": "testuser", "time_control": "rapid"}
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert secret not in detail
    assert "hunter2" not in detail
    assert detail == "Internal server error"


@patch("services.api.main.get_player_stats")
def test_create_rating_snapshot_missing_rating(mock_get_stats, client_with_db):
    mock_get_stats.return_value = {"chess_rapid": {"last": {}}}

    response = client_with_db.post(
        "/ratings/snapshot", json={"username": "testuser", "time_control": "rapid"}
    )

    assert response.status_code == 502
    assert "could not find rating" in response.json()["detail"].lower()


def test_explain_rating_changes_basic(client_with_db, db_session):
    since_time = datetime.now(timezone.utc) - timedelta(days=2)

    snapshot = RatingSnapshot(
        username="testuser",
        source="chesscom",
        time_control="rapid",
        rating=1400,
        recorded_at=since_time + timedelta(hours=1),
    )
    db_session.add(snapshot)

    pgn_win = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]
[Result "1-0"]
[WhiteElo "1400"]
[BlackElo "1600"]

1. e4 e5 2. Nf3 Nc6 1-0"""

    # Seed enough rated games (>= MIN_GAMES_FOR_RATING_DRIVERS) so directional
    # drivers are emitted rather than suppressed as insufficient data.
    for i in range(5):
        db_session.add(
            Game(
                game_id=f"game-explain-{i}",
                url=f"https://chess.com/game/{i}",
                username="testuser",
                white_username="testuser",
                black_username="opponent",
                white_result="win",
                black_result="loss",
                time_control="600",
                end_time=int((since_time + timedelta(hours=2 + i)).timestamp()),
                rated=True,
                pgn_blob=pgn_win,
            )
        )
    db_session.commit()

    response = client_with_db.get(
        "/ratings/explain",
        params={
            "username": "testuser",
            "time_control": "rapid",
            "since": since_time.isoformat(),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["wins"] == 5
    assert data["stats"]["losses"] == 0
    assert data["rating"]["reference_rating"] == 1400
    assert data["rating"]["reference_is_approx"] is False
    assert data["insufficient_data"] is False
    assert data["confidence"] == "low"  # 5 games: below medium (10) threshold
    assert any("outperformed" in driver["text"].lower() for driver in data["drivers"])


def test_rating_history_returns_snapshots(client_with_db, db_session):
    """GET /ratings/history returns snapshots in chronological order."""
    now = datetime.now(timezone.utc)
    for i, rating in enumerate([1400, 1420, 1415]):
        db_session.add(
            RatingSnapshot(
                username="testuser",
                source="chesscom",
                time_control="rapid",
                rating=rating,
                recorded_at=now - timedelta(days=3 - i),
            )
        )
    # Different time control — should not appear
    db_session.add(
        RatingSnapshot(
            username="testuser",
            source="chesscom",
            time_control="blitz",
            rating=1300,
            recorded_at=now,
        )
    )
    db_session.commit()

    response = client_with_db.get(
        "/ratings/history",
        params={"username": "testuser", "time_control": "rapid"},
    )
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 3
    assert items[0]["rating"] == 1400
    assert items[1]["rating"] == 1420
    assert items[2]["rating"] == 1415


def test_rating_history_returns_most_recent(client_with_db, db_session):
    """GET /ratings/history with limit returns the most recent snapshots, not oldest."""
    now = datetime.now(timezone.utc)
    # Create 5 snapshots: ratings 1400..1440
    for i in range(5):
        db_session.add(
            RatingSnapshot(
                username="testuser",
                source="chesscom",
                time_control="rapid",
                rating=1400 + i * 10,
                recorded_at=now - timedelta(days=5 - i),
            )
        )
    db_session.commit()

    # Request only 3 — should get the 3 most recent (1420, 1430, 1440) in chrono order
    response = client_with_db.get(
        "/ratings/history",
        params={"username": "testuser", "time_control": "rapid", "limit": "3"},
    )
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 3
    assert items[0]["rating"] == 1420
    assert items[1]["rating"] == 1430
    assert items[2]["rating"] == 1440


def test_rating_history_empty(client_with_db):
    """GET /ratings/history returns empty list when no snapshots exist."""
    response = client_with_db.get(
        "/ratings/history",
        params={"username": "nobody", "time_control": "rapid"},
    )
    assert response.status_code == 200
    assert response.json() == []


@patch("services.api.sessions.get_player_stats")
def test_auto_snapshot_skips_unchanged_rating(mock_stats, client_with_db, db_session):
    """_auto_snapshot should not create a duplicate when rating hasn't changed."""
    from services.api.models import TrainingSession

    # Seed a snapshot with rating 1500 for rapid
    db_session.add(
        RatingSnapshot(
            username="testuser",
            source="chesscom",
            time_control="rapid",
            rating=1500,
            recorded_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    # Create a session
    session = TrainingSession(
        id="sess-dup-test",
        username="testuser",
        requested_n=5,
        pass_count=3,
        fail_count=2,
        total_time_ms=60000,
    )
    db_session.add(session)
    db_session.commit()

    # Chess.com returns the same rating
    mock_stats.return_value = {
        "chess_rapid": {"last": {"rating": 1500}},
        "chess_blitz": {"last": {"rating": 1200}},
        "chess_bullet": {"last": {"rating": 1000}},
    }

    response = client_with_db.post(
        "/sessions/sess-dup-test/complete",
        json={"username": "testuser"},
    )
    assert response.status_code == 200

    # Rapid should still have only 1 snapshot (duplicate skipped)
    stmt = select(RatingSnapshot).where(
        RatingSnapshot.username == "testuser",
        RatingSnapshot.time_control == "rapid",
    )
    rapid_snapshots = db_session.scalars(stmt).all()
    assert len(rapid_snapshots) == 1

    # Blitz should have 1 new snapshot (rating 1200 is new)
    stmt = select(RatingSnapshot).where(
        RatingSnapshot.username == "testuser",
        RatingSnapshot.time_control == "blitz",
    )
    blitz_snapshots = db_session.scalars(stmt).all()
    assert len(blitz_snapshots) == 1
    assert blitz_snapshots[0].rating == 1200


@patch("services.api.sessions.get_player_stats")
def test_auto_snapshot_creates_on_changed_rating(
    mock_stats, client_with_db, db_session
):
    """_auto_snapshot should create a new snapshot when rating has changed."""
    from services.api.models import TrainingSession

    db_session.add(
        RatingSnapshot(
            username="testuser",
            source="chesscom",
            time_control="rapid",
            rating=1500,
            recorded_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    session = TrainingSession(
        id="sess-change-test",
        username="testuser",
        requested_n=5,
        pass_count=4,
        fail_count=1,
        total_time_ms=50000,
    )
    db_session.add(session)
    db_session.commit()

    # Rating changed from 1500 -> 1520
    mock_stats.return_value = {
        "chess_rapid": {"last": {"rating": 1520}},
        "chess_blitz": {"last": {"rating": 1200}},
        "chess_bullet": {"last": {"rating": 1000}},
    }

    response = client_with_db.post(
        "/sessions/sess-change-test/complete",
        json={"username": "testuser"},
    )
    assert response.status_code == 200

    stmt = (
        select(RatingSnapshot)
        .where(
            RatingSnapshot.username == "testuser",
            RatingSnapshot.time_control == "rapid",
        )
        .order_by(RatingSnapshot.recorded_at.asc())
    )
    rapid_snapshots = db_session.scalars(stmt).all()
    assert len(rapid_snapshots) == 2
    assert rapid_snapshots[0].rating == 1500
    assert rapid_snapshots[1].rating == 1520


def _seed_game(db_session, i, since_time, *, rated=True, pgn=None, result="win"):
    """Seed one rapid game for testuser ending i hours into the window."""
    white_result = result
    black_result = "loss" if result == "win" else "win" if result == "loss" else result
    db_session.add(
        Game(
            game_id=f"game-{rated}-{result}-{i}",
            url=f"https://chess.com/game/{rated}-{result}-{i}",
            username="testuser",
            white_username="testuser",
            black_username="opponent",
            white_result=white_result,
            black_result=black_result,
            time_control="600",
            end_time=int((since_time + timedelta(hours=1 + i)).timestamp()),
            rated=rated,
            pgn_blob=pgn,
        )
    )


def test_explain_excludes_casual_games(client_with_db, db_session):
    """Casual (unrated) games must not count toward rating attribution."""
    since_time = datetime.now(timezone.utc) - timedelta(days=2)
    pgn = (
        '[White "testuser"]\n[Black "opponent"]\n'
        '[WhiteElo "1400"]\n[BlackElo "1450"]\n\n1. e4 e5 1-0'
    )
    for i in range(5):
        _seed_game(db_session, i, since_time, rated=True, pgn=pgn)
    for i in range(5, 8):
        _seed_game(db_session, i, since_time, rated=False, pgn=pgn)
    db_session.commit()

    response = client_with_db.get(
        "/ratings/explain",
        params={
            "username": "testuser",
            "time_control": "rapid",
            "since": since_time.isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["games"] == 5
    assert data["stats"]["wins"] == 5
    assert data["stats"]["casual_games_excluded"] == 3


def test_explain_trajectory_and_estimated_net_change(client_with_db, db_session):
    """With no snapshots, per-game Elo headers give a trajectory and an
    estimated start/end so net change still renders (flagged estimated)."""
    since_time = datetime.now(timezone.utc) - timedelta(days=2)
    for i, elo in enumerate([1400, 1410, 1395, 1420, 1432]):
        pgn = (
            '[White "testuser"]\n[Black "opponent"]\n'
            f'[WhiteElo "{elo}"]\n[BlackElo "1450"]\n\n1. e4 e5 1-0'
        )
        _seed_game(db_session, i, since_time, rated=True, pgn=pgn)
    db_session.commit()

    response = client_with_db.get(
        "/ratings/explain",
        params={
            "username": "testuser",
            "time_control": "rapid",
            "since": since_time.isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert [p["rating"] for p in data["trajectory"]] == [1400, 1410, 1395, 1420, 1432]
    assert data["rating"]["start"] == 1400
    assert data["rating"]["end"] == 1432
    assert data["rating"]["net_change"] == 32
    assert data["rating"]["is_estimated"] is True
    # Reference falls back to the player's own Elo average, not opponents'.
    assert data["rating"]["reference_rating"] == int(
        (1400 + 1410 + 1395 + 1420 + 1432) / 5
    )
    assert data["rating"]["reference_is_approx"] is True


def test_explain_snapshot_end_beats_stale_game_elo(client_with_db, db_session):
    """An in-window snapshot recorded after the last game wins as the end
    anchor; one recorded before the last game loses to the game's own Elo."""
    since_time = datetime.now(timezone.utc) - timedelta(days=2)
    pgn = (
        '[White "testuser"]\n[Black "opponent"]\n'
        '[WhiteElo "1500"]\n[BlackElo "1450"]\n\n1. e4 e5 1-0'
    )
    _seed_game(db_session, 5, since_time, rated=True, pgn=pgn)
    # Snapshot recorded BEFORE the game -> stale, game Elo (1500) should win.
    db_session.add(
        RatingSnapshot(
            username="testuser",
            source="chesscom",
            time_control="rapid",
            rating=1480,
            recorded_at=since_time + timedelta(hours=1),
        )
    )
    db_session.commit()

    response = client_with_db.get(
        "/ratings/explain",
        params={
            "username": "testuser",
            "time_control": "rapid",
            "since": since_time.isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["rating"]["start"] == 1480
    assert data["rating"]["end"] == 1500
    assert data["rating"]["is_estimated"] is True


def test_explain_highlights_include_opponent_username(client_with_db, db_session):
    since_time = datetime.now(timezone.utc) - timedelta(days=2)
    pgn = (
        '[White "testuser"]\n[Black "opponent"]\n'
        '[WhiteElo "1400"]\n[BlackElo "1600"]\n\n1. e4 e5 1-0'
    )
    for i in range(5):
        _seed_game(db_session, i, since_time, rated=True, pgn=pgn)
    db_session.commit()

    response = client_with_db.get(
        "/ratings/explain",
        params={
            "username": "testuser",
            "time_control": "rapid",
            "since": since_time.isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    best = data["highlights"]["best_surprises"]
    assert best, "wins vs a higher-rated opponent should be positive surprises"
    assert best[0]["opponent_username"] == "opponent"
    # rating_diff is now measured against the player's own per-game Elo.
    assert best[0]["rating_diff"] == 200
