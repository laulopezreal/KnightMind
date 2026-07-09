import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.main import app, get_db
from services.api.models import (
    Base,
    Game,
    Job,
    JobStatus,
    PuzzleStats,
)
from services.api.models import (
    Puzzle as PuzzleModel,
)


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
    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


client = TestClient(app)


# --- Helpers ---


def _create_game(
    db,
    game_id: str,
    username: str = "testuser",
    pgn: str = "",
    end_time: int | None = None,
):
    """Helper: create a Game row."""
    existing = db.get(Game, game_id)
    if existing:
        return
    db.add(
        Game(
            game_id=game_id,
            url=f"https://chess.com/game/{game_id}",
            username=username,
            white_username=username,
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=end_time or int(datetime.now(timezone.utc).timestamp()),
            rated=True,
            pgn_blob=pgn or '[Event "Test"]\n\n1. e4 e5 1/2-1/2',
        )
    )
    db.flush()


def _create_puzzle(
    db,
    puzzle_id: str,
    username: str = "testuser",
    source_game_id: str | None = None,
    ply: int = 10,
    swing: float = 2.0,
    created_at: datetime | None = None,
    used_on: date | None = None,
):
    """Helper: create a Puzzle row (and parent Game if needed)."""
    game_id = source_game_id or f"game-{puzzle_id}"
    _create_game(db, game_id, username)
    db.add(
        PuzzleModel(
            id=puzzle_id,
            username=username,
            source_game_id=game_id,
            ply=ply,
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=swing,
            created_at=created_at or datetime.now(timezone.utc),
            used_on=used_on,
        )
    )
    db.flush()


# --- Basic tests ---


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "KnightMind API"
    assert "version" in data


def test_import_chesscom_missing_username():
    response = client.post("/import/chesscom")
    assert response.status_code == 422  # Validation error


def test_get_openings_missing_username():
    """Test that /openings requires username parameter."""
    response = client.get("/openings")
    assert response.status_code == 422  # Validation error


# --- Openings tests ---


@patch("services.api.main.import_all_games")
def test_get_openings_with_games(mock_import_games, client_with_db):
    """Test /openings endpoint with imported games."""

    async def mock_generator(username):
        from services.ingest import ChessGame

        for game_data in MOCK_GAMES:
            yield ChessGame(
                url=game_data["url"],
                pgn=game_data["pgn"],
                time_control=game_data["time_control"],
                end_time=game_data["end_time"],
                rated=game_data["rated"],
                white_username=game_data["white"]["username"],
                black_username=game_data["black"]["username"],
                white_result=game_data["white"]["result"],
                black_result=game_data["black"]["result"],
            )

    mock_import_games.side_effect = mock_generator

    import_response = client_with_db.post("/import/chesscom?username=testuser")
    assert import_response.status_code == 200

    # Now fetch openings
    response = client_with_db.get("/openings?username=testuser")
    assert response.status_code == 200
    data = response.json()

    # Verify structure
    assert "move_san" in data
    assert "ply" in data
    assert "games_count" in data
    assert "wins" in data
    assert "draws" in data
    assert "losses" in data
    assert "win_rate" in data
    assert data["games_count"] == 2


def test_get_openings_no_games(client_with_db):
    """Test /openings returns 404 when user has no games."""
    response = client_with_db.get("/openings?username=unknownuser")
    assert response.status_code == 404
    assert "no games" in response.json()["detail"].lower()


# --- User tests ---


def test_get_users_list(client_with_db, db_session):
    """Test retrieving list of users."""
    _create_game(db_session, "game1", "user1")
    _create_game(db_session, "game2", "user2")
    db_session.commit()

    response = client_with_db.get("/users")

    assert response.status_code == 200
    assert response.json() == {"users": ["user1", "user2"]}


def test_user_status_empty(client_with_db):
    response = client_with_db.get("/users/testuser/status")

    assert response.status_code == 200
    assert response.json() == {
        "username": "testuser",
        "games_count": 0,
        "puzzles_count": 0,
        "due_count": 0,
        "next_due_at": None,
        "has_new_games": False,
    }


def test_user_status_games_without_puzzles(client_with_db, db_session):
    _create_game(db_session, "game-99999", "testuser")
    db_session.commit()

    response = client_with_db.get("/users/testuser/status")

    assert response.status_code == 200
    data = response.json()
    assert data["games_count"] == 1
    assert data["puzzles_count"] == 0
    assert data["due_count"] == 0
    assert data["next_due_at"] is None
    assert data["has_new_games"] is True


def test_user_status_with_due_puzzles(client_with_db, db_session):
    game_end_time = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp())
    game_id = "game-status-test"
    _create_game(db_session, game_id, "testuser", end_time=game_end_time)

    puzzle_id_one = "puzzle-due-1"
    puzzle_id_two = "puzzle-due-2"
    _create_puzzle(
        db_session,
        puzzle_id_one,
        "testuser",
        source_game_id=game_id,
        ply=10,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    _create_puzzle(
        db_session,
        puzzle_id_two,
        "testuser",
        source_game_id=game_id,
        ply=12,
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    past_due = datetime.now(timezone.utc) - timedelta(days=1)
    future_due = datetime.now(timezone.utc) + timedelta(days=2)
    db_session.add_all(
        [
            PuzzleStats(
                puzzle_id=puzzle_id_one,
                username="testuser",
                attempts=1,
                pass_count=0,
                fail_count=1,
                last_reviewed_at=datetime.now(timezone.utc) - timedelta(days=3),
                last_result="fail",
                next_due_at=past_due,
                interval_days=1,
                ease_factor=2.0,
            ),
            PuzzleStats(
                puzzle_id=puzzle_id_two,
                username="testuser",
                attempts=2,
                pass_count=1,
                fail_count=1,
                last_reviewed_at=datetime.now(timezone.utc) - timedelta(days=1),
                last_result="pass",
                next_due_at=future_due,
                interval_days=3,
                ease_factor=2.1,
            ),
        ]
    )
    db_session.commit()

    response = client_with_db.get("/users/testuser/status")

    assert response.status_code == 200
    data = response.json()
    assert data["games_count"] == 1
    assert data["puzzles_count"] == 2
    assert data["due_count"] == 1
    assert data["has_new_games"] is False
    assert data["next_due_at"].startswith(future_due.date().isoformat())


# --- Import tests ---

MOCK_ARCHIVES = ["https://api.chess.com/pub/player/testuser/games/2024/01"]

MOCK_GAMES = [
    {
        "url": "https://www.chess.com/game/live/12345",
        "pgn": '[Event "Live Chess"]\n[Site "Chess.com"]\n[White "testuser"]\n[Black "opponent1"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0',
        "time_control": "600",
        "end_time": 1704067200,
        "rated": True,
        "white": {"username": "testuser", "result": "win"},
        "black": {"username": "opponent1", "result": "lose"},
    },
    {
        "url": "https://www.chess.com/game/live/12346",
        "pgn": '[Event "Live Chess"]\n[Site "Chess.com"]\n[White "opponent2"]\n[Black "testuser"]\n[Result "0-1"]\n\n1. d4 d5 2. c4 e6 0-1',
        "time_control": "300",
        "end_time": 1704153600,
        "rated": True,
        "white": {"username": "opponent2", "result": "lose"},
        "black": {"username": "testuser", "result": "win"},
    },
]


@patch("services.api.main.import_all_games")
def test_import_chesscom_success(mock_import_games, client_with_db):
    """Test successful import of games."""

    async def mock_generator(username):
        from services.ingest import ChessGame

        for game_data in MOCK_GAMES:
            yield ChessGame(
                url=game_data["url"],
                pgn=game_data["pgn"],
                time_control=game_data["time_control"],
                end_time=game_data["end_time"],
                rated=game_data["rated"],
                white_username=game_data["white"]["username"],
                black_username=game_data["black"]["username"],
                white_result=game_data["white"]["result"],
                black_result=game_data["black"]["result"],
            )

    mock_import_games.side_effect = mock_generator

    response = client_with_db.post("/import/chesscom?username=testuser")

    assert response.status_code == 200
    data = response.json()
    assert data["games_count"] == 2
    assert data["new_games"] == 2
    assert data["skipped_duplicates"] == 0
    assert "testuser" in data["message"]


@patch("services.api.main.import_all_games")
def test_import_status_after_import(mock_import_games, client_with_db):
    """Test import status before and after an import."""

    async def mock_generator(username):
        from services.ingest import ChessGame

        for game_data in MOCK_GAMES:
            yield ChessGame(
                url=game_data["url"],
                pgn=game_data["pgn"],
                time_control=game_data["time_control"],
                end_time=game_data["end_time"],
                rated=game_data["rated"],
                white_username=game_data["white"]["username"],
                black_username=game_data["black"]["username"],
                white_result=game_data["white"]["result"],
                black_result=game_data["black"]["result"],
            )

    response = client_with_db.get("/import/status?username=testuser")
    assert response.status_code == 200
    data = response.json()
    assert data["last_imported_at"] is None
    assert data["last_new_games"] is None

    mock_import_games.side_effect = mock_generator
    import_response = client_with_db.post("/import/chesscom?username=testuser")
    assert import_response.status_code == 200

    response = client_with_db.get("/import/status?username=testuser")
    assert response.status_code == 200
    data = response.json()
    assert data["last_imported_at"] is not None
    assert isinstance(data["last_new_games"], int)


@patch("services.api.main.import_all_games")
def test_import_chesscom_deduplication(mock_import_games, client_with_db):
    """Test that duplicate games are not re-imported."""

    async def mock_generator(username):
        from services.ingest import ChessGame

        for game_data in MOCK_GAMES:
            yield ChessGame(
                url=game_data["url"],
                pgn=game_data["pgn"],
                time_control=game_data["time_control"],
                end_time=game_data["end_time"],
                rated=game_data["rated"],
                white_username=game_data["white"]["username"],
                black_username=game_data["black"]["username"],
                white_result=game_data["white"]["result"],
                black_result=game_data["black"]["result"],
            )

    mock_import_games.side_effect = mock_generator

    # First import
    response1 = client_with_db.post("/import/chesscom?username=testuser")
    assert response1.status_code == 200
    assert response1.json()["new_games"] == 2

    # Second import should skip duplicates
    mock_import_games.side_effect = mock_generator

    response2 = client_with_db.post("/import/chesscom?username=testuser")
    assert response2.status_code == 200
    data = response2.json()
    assert data["new_games"] == 0
    assert data["skipped_duplicates"] == 2
    assert data["games_count"] == 2  # Total processed this run


@patch("services.api.main.import_all_games")
def test_import_chesscom_user_not_found(mock_import_games, client_with_db):
    """Test error handling for non-existent user."""
    from services.ingest import UserNotFoundError

    mock_import_games.side_effect = UserNotFoundError("nonexistent_user")

    response = client_with_db.post("/import/chesscom?username=nonexistent_user")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@patch("services.api.main.import_all_games")
def test_import_chesscom_rate_limit(mock_import_games, client_with_db):
    """Test error handling for rate limiting."""
    from services.ingest import RateLimitError

    mock_import_games.side_effect = RateLimitError(retry_after=60)

    response = client_with_db.post("/import/chesscom?username=testuser")

    assert response.status_code == 429
    assert "rate limit" in response.json()["detail"].lower()


@patch("services.api.main.import_all_games")
def test_import_chesscom_network_error(mock_import_games, client_with_db):
    """Test error handling for network errors."""
    from services.ingest import NetworkError

    mock_import_games.side_effect = NetworkError("Connection refused")

    response = client_with_db.post("/import/chesscom?username=testuser")

    assert response.status_code == 502
    assert "network" in response.json()["detail"].lower()


@patch("services.api.main.import_all_games")
def test_import_chesscom_no_games(mock_import_games, client_with_db):
    """Test handling user with no games."""

    async def mock_generator(username):
        if False:
            yield  # Empty generator

    mock_import_games.side_effect = mock_generator

    response = client_with_db.post("/import/chesscom?username=newuser")

    assert response.status_code == 200
    data = response.json()
    assert data["games_count"] == 0
    assert data["new_games"] == 0


# --- Puzzle generation endpoint tests ---


def test_generate_puzzles_success(client_with_db, db_session):
    """Test puzzle generation enqueues a job with default params."""
    response = client_with_db.post("/puzzles/generate?username=testuser")
    assert response.status_code == 200
    data = response.json()

    job = db_session.get(Job, data["job_id"])
    assert job is not None
    assert job.status == JobStatus.QUEUED
    assert job.params == {"max_games": 30, "max_puzzles": 30}


def test_generate_puzzles_custom_params(client_with_db, db_session):
    """Test puzzle generation stores custom params on the job."""
    response = client_with_db.post(
        "/puzzles/generate?username=testuser&max_games=5&max_puzzles=7"
    )
    assert response.status_code == 200
    data = response.json()

    job = db_session.get(Job, data["job_id"])
    assert job is not None
    assert job.params == {"max_games": 5, "max_puzzles": 7}


def test_generate_puzzles_missing_username():
    """Test that /puzzles/generate requires username parameter."""
    response = client.post("/puzzles/generate")
    assert response.status_code == 422  # Validation error


# --- Daily puzzles endpoint tests ---


def test_get_daily_puzzles_success(client_with_db, db_session):
    """Test successful retrieval of daily puzzles."""
    for i in range(5):
        _create_puzzle(
            db_session,
            f"puzzle-{i}",
            "testuser",
            source_game_id=f"game-daily-{i}",
            ply=10 + i,
            created_at=datetime(2024, 1, i + 1, 12, 0, 0, tzinfo=timezone.utc),
        )
    db_session.commit()

    response = client_with_db.post(
        "/daily-puzzle-sessions", json={"username": "testuser", "n": 3}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 3
    assert len(data["puzzles"]) == 3

    today_str = date.today().isoformat()
    for puzzle_data in data["puzzles"]:
        assert puzzle_data["used_on"] == today_str
        assert puzzle_data["username"] == "testuser"


def test_get_daily_puzzles_rotation(client_with_db, db_session):
    """Test that puzzles rotate correctly - unused first, then used."""
    yesterday = date.today() - timedelta(days=1)

    for i in range(5):
        _create_puzzle(
            db_session,
            f"puzzle-rot-{i}",
            "testuser",
            source_game_id=f"game-rot-{i}",
            ply=10 + i,
            used_on=yesterday if i < 2 else None,
        )
    db_session.commit()

    # Request 4 puzzles - should get 3 unused + 1 used
    response = client_with_db.post(
        "/daily-puzzle-sessions", json={"username": "testuser", "n": 4}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 4

    today_str = date.today().isoformat()
    for puzzle_data in data["puzzles"]:
        assert puzzle_data["used_on"] == today_str


def test_get_daily_puzzles_no_puzzles(client_with_db):
    """Test 404 when user has no puzzles."""
    response = client_with_db.post(
        "/daily-puzzle-sessions", json={"username": "unknownuser", "n": 5}
    )

    assert response.status_code == 404
    assert "no puzzles" in response.json()["detail"].lower()
    assert "generate puzzles first" in response.json()["detail"].lower()


def test_get_daily_puzzles_validation():
    """Test parameter validation for daily puzzles endpoint."""
    # Missing username
    response = client.post("/daily-puzzle-sessions", json={})
    assert response.status_code == 422

    # n too small
    response = client.post("/daily-puzzle-sessions", json={"username": "test", "n": 0})
    assert response.status_code == 400

    # n too large
    response = client.post("/daily-puzzle-sessions", json={"username": "test", "n": 21})
    assert response.status_code == 400


def test_get_daily_puzzles_idempotent(client_with_db, db_session):
    """Test that calling endpoint multiple times on same day returns same puzzles."""
    for i in range(5):
        _create_puzzle(
            db_session,
            f"puzzle-idem-{i}",
            "testuser",
            source_game_id=f"game-idem-{i}",
            ply=10 + i,
        )
    db_session.commit()

    # First call
    response1 = client_with_db.post(
        "/daily-puzzle-sessions", json={"username": "testuser", "n": 3}
    )
    assert response1.status_code == 200
    puzzle_ids_1 = {p["id"] for p in response1.json()["puzzles"]}

    # Second call - should return same puzzles (already marked for today)
    response2 = client_with_db.post(
        "/daily-puzzle-sessions", json={"username": "testuser", "n": 3}
    )
    assert response2.status_code == 200
    puzzle_ids_2 = {p["id"] for p in response2.json()["puzzles"]}

    # Should be the same puzzles
    assert puzzle_ids_1 == puzzle_ids_2


# --- Engine tests ---


@patch("services.api.main.is_engine_available")
def test_engine_status_available(mock_available):
    mock_available.return_value = (True, "Stockfish is ready")
    response = client.get("/engine/status")
    assert response.status_code == 200
    assert response.json() == {"available": True, "message": "Stockfish is ready"}


@patch("services.api.main.get_or_compute_eval")
def test_engine_eval_invalid_fen(mock_eval):
    from services.api.engine import InvalidFenError

    mock_eval.side_effect = InvalidFenError("Invalid FEN")
    response = client.post("/engine/eval", json={"fen": "invalid"})
    assert response.status_code == 400
    assert "Invalid FEN" in response.json()["detail"]


@patch("services.api.main.get_or_compute_eval")
def test_engine_eval_unavailable(mock_eval):
    from services.api.engine import EngineNotAvailableError

    mock_eval.side_effect = EngineNotAvailableError("Engine not available")
    response = client.post("/engine/eval", json={"fen": "any"})
    assert response.status_code == 503
    assert "Engine not available" in response.json()["detail"]


# --- Tricky Puzzles Endpoint Tests ---


_SENTINEL = object()


def _create_puzzle_stats(
    db_session, puzzle_id, username, fail_count, last_reviewed_at=None, title=_SENTINEL
):
    """Helper to create a PuzzleStats record with required Puzzle and Game parents."""
    _create_game(db_session, f"game-{puzzle_id}", username)
    existing_puzzle = db_session.get(PuzzleModel, puzzle_id)
    if not existing_puzzle:
        db_session.add(
            PuzzleModel(
                id=puzzle_id,
                username=username,
                source_game_id=f"game-{puzzle_id}",
                ply=10,
                fen="8/8/8/8/8/8/8/8 w - - 0 1",
                side_to_move="white",
                played_move_uci="e2e4",
                best_move_uci="d2d4",
                eval_before=0.2,
                eval_after=-0.5,
                swing=0.7,
                created_at=datetime.now(timezone.utc),
            )
        )
    db_session.add(
        PuzzleStats(
            puzzle_id=puzzle_id,
            username=username,
            title=f"Puzzle {puzzle_id}" if title is _SENTINEL else title,
            attempts=fail_count + 1,
            pass_count=1,
            fail_count=fail_count,
            last_reviewed_at=last_reviewed_at,
            last_result="fail",
            next_due_at=datetime.now(timezone.utc),
            interval_days=1,
            ease_factor=2.0,
        )
    )
    db_session.commit()


def test_tricky_puzzles_empty(client_with_db):
    """Returns empty list when user has no tricky puzzles."""
    response = client_with_db.get("/users/testuser/puzzles/tricky")
    assert response.status_code == 200
    data = response.json()
    assert data["puzzles"] == []
    assert data["total_count"] == 0


def test_tricky_puzzles_filters_below_threshold(client_with_db, db_session):
    """Puzzles with fewer than 2 failures are excluded."""
    _create_puzzle_stats(
        db_session,
        "p-1fail",
        "testuser",
        fail_count=1,
        last_reviewed_at=datetime.now(timezone.utc),
    )
    response = client_with_db.get("/users/testuser/puzzles/tricky")
    assert response.status_code == 200
    data = response.json()
    assert data["puzzles"] == []
    assert data["total_count"] == 0


def test_tricky_puzzles_includes_at_threshold(client_with_db, db_session):
    """Puzzles with exactly 2 failures are included."""
    reviewed_at = datetime.now(timezone.utc) - timedelta(hours=1)
    _create_puzzle_stats(
        db_session,
        "p-2fail",
        "testuser",
        fail_count=2,
        last_reviewed_at=reviewed_at,
        title="Fork Tactic",
    )
    response = client_with_db.get("/users/testuser/puzzles/tricky")
    assert response.status_code == 200
    data = response.json()
    assert len(data["puzzles"]) == 1
    assert data["puzzles"][0]["puzzle_id"] == "p-2fail"
    assert data["puzzles"][0]["title"] == "Fork Tactic"
    assert data["puzzles"][0]["fail_count"] == 2
    assert data["total_count"] == 1


def test_tricky_puzzles_sorted_by_fail_count_desc(client_with_db, db_session):
    """Puzzles are sorted by fail_count descending, then by last_reviewed_at descending."""
    now = datetime.now(timezone.utc)
    _create_puzzle_stats(
        db_session,
        "p-low",
        "testuser",
        fail_count=2,
        last_reviewed_at=now - timedelta(hours=1),
    )
    _create_puzzle_stats(
        db_session,
        "p-high",
        "testuser",
        fail_count=5,
        last_reviewed_at=now - timedelta(hours=2),
    )
    _create_puzzle_stats(
        db_session, "p-mid", "testuser", fail_count=3, last_reviewed_at=now
    )

    response = client_with_db.get("/users/testuser/puzzles/tricky")
    assert response.status_code == 200
    ids = [p["puzzle_id"] for p in response.json()["puzzles"]]
    assert ids == ["p-high", "p-mid", "p-low"]


def test_tricky_puzzles_respects_limit(client_with_db, db_session):
    """The limit parameter caps the number of returned puzzles."""
    now = datetime.now(timezone.utc)
    for i in range(5):
        _create_puzzle_stats(
            db_session,
            f"p-limit-{i}",
            "testuser",
            fail_count=3,
            last_reviewed_at=now - timedelta(hours=i),
        )

    response = client_with_db.get("/users/testuser/puzzles/tricky?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["puzzles"]) == 2
    # total_count reflects all matching puzzles, not just returned ones
    assert data["total_count"] == 5


def test_tricky_puzzles_excludes_null_last_reviewed(client_with_db, db_session):
    """Puzzles with null last_reviewed_at are excluded even if fail_count >= 2."""
    _create_puzzle_stats(
        db_session, "p-null-date", "testuser", fail_count=3, last_reviewed_at=None
    )
    _create_puzzle_stats(
        db_session,
        "p-has-date",
        "testuser",
        fail_count=2,
        last_reviewed_at=datetime.now(timezone.utc),
    )

    response = client_with_db.get("/users/testuser/puzzles/tricky")
    assert response.status_code == 200
    data = response.json()
    assert len(data["puzzles"]) == 1
    assert data["puzzles"][0]["puzzle_id"] == "p-has-date"
    assert data["total_count"] == 1


def test_tricky_puzzles_scoped_to_username(client_with_db, db_session):
    """Tricky puzzles for one user don't leak into another user's results."""
    now = datetime.now(timezone.utc)
    _create_puzzle_stats(
        db_session, "p-alice", "alice", fail_count=4, last_reviewed_at=now
    )
    _create_puzzle_stats(db_session, "p-bob", "bob", fail_count=3, last_reviewed_at=now)

    response = client_with_db.get("/users/alice/puzzles/tricky")
    assert response.status_code == 200
    data = response.json()
    assert len(data["puzzles"]) == 1
    assert data["puzzles"][0]["puzzle_id"] == "p-alice"


def test_tricky_puzzles_title_fallback(client_with_db, db_session):
    """Puzzles with no title get 'Untitled Puzzle' as fallback."""
    _create_puzzle_stats(
        db_session,
        "p-no-title",
        "testuser",
        fail_count=2,
        last_reviewed_at=datetime.now(timezone.utc),
        title=None,
    )

    response = client_with_db.get("/users/testuser/puzzles/tricky")
    assert response.status_code == 200
    assert response.json()["puzzles"][0]["title"] == "Untitled Puzzle"
