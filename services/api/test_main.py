import asyncio
import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

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
    from services.api.auth import require_operator

    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")
    app.dependency_overrides[get_db] = lambda: db_session
    # Default test client acts as an authenticated tailnet operator; the gate on
    # the bare /users list is covered explicitly in test_ops_gate.py.
    app.dependency_overrides[require_operator] = lambda: "test@operator"
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
    existing = db.get(Game, (game_id, username))
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
    solution_pv: str | None = None,
):
    """Helper: create a Puzzle row (and parent Game if needed).

    ``solution_pv`` optionally stores a full solution line (space-separated UCI)
    so multi-move training can be exercised. Its first move is d2d4, matching the
    seeded ``best_move_uci`` so single- and multi-move paths stay consistent.
    """
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
            solution_pv=solution_pv,
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

    async def mock_generator(username, since=None):
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


# --- Chess.com username validation tests ---


def test_chesscom_api_client_caps_tls_at_1_2():
    import ssl

    from services.ingest.chesscom import SSL_CONTEXT

    assert SSL_CONTEXT.maximum_version == ssl.TLSVersion.TLSv1_2


@patch("services.api.main.get_player_profile", new_callable=AsyncMock)
def test_validate_user_success(mock_get_player_profile):
    mock_get_player_profile.return_value = {"username": "lauureal"}

    response = client.get("/users/validate?username= lauureal ")

    assert response.status_code == 200
    assert response.json() == {"valid": True, "username": "lauureal"}
    mock_get_player_profile.assert_awaited_once_with("lauureal")


@patch("services.api.main.get_player_profile", new_callable=AsyncMock)
def test_validate_user_not_found(mock_get_player_profile):
    from services.ingest import UserNotFoundError

    mock_get_player_profile.side_effect = UserNotFoundError("missing-user")

    response = client.get("/users/validate?username=missing-user")

    assert response.status_code == 200
    assert response.json() == {"valid": False, "error": "User not found"}


@patch("services.api.main.get_player_profile", new_callable=AsyncMock)
def test_validate_user_network_error_returns_bad_gateway(mock_get_player_profile):
    from services.ingest import NetworkError

    mock_get_player_profile.side_effect = NetworkError("Failed to connect to Chess.com")

    response = client.get("/users/validate?username=lauureal")

    assert response.status_code == 502
    assert "Failed to connect to Chess.com" in response.json()["detail"]


@patch("services.api.main.get_player_profile", new_callable=AsyncMock)
def test_validate_user_rate_limit_sets_retry_after(mock_get_player_profile):
    from services.ingest import RateLimitError

    mock_get_player_profile.side_effect = RateLimitError(retry_after=60)

    response = client.get("/users/validate?username=lauureal")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert "rate limit" in response.json()["detail"].lower()


def test_get_player_profile_invalid_json_returns_network_error(monkeypatch):
    from services.ingest.chesscom import NetworkError, get_player_profile

    class FakeResponse:
        is_success = True

        def json(self):
            raise ValueError("not json")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(
        "services.ingest.chesscom._chesscom_client", lambda timeout: FakeClient()
    )

    with pytest.raises(NetworkError, match="invalid profile response"):
        asyncio.run(get_player_profile("lauureal"))


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

    async def mock_generator(username, since=None):
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
def test_import_chesscom_skips_malformed_game(mock_import_games, client_with_db):
    """One empty-url game is skipped; the good ones still import (no 500)."""

    async def mock_generator(username, since=None):
        from services.ingest import ChessGame

        # A malformed game with an empty url between two valid ones.
        payloads = [MOCK_GAMES[0], {**MOCK_GAMES[1], "url": ""}]
        for game_data in payloads:
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
    assert data["new_games"] == 1  # only the valid game stored
    assert data["skipped_duplicates"] == 1  # malformed game skipped


@patch("services.api.main.import_all_games")
def test_import_status_after_import(mock_import_games, client_with_db):
    """Test import status before and after an import."""

    async def mock_generator(username, since=None):
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

    async def mock_generator(username, since=None):
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
def test_import_chesscom_batches_commits(mock_import_games, client_with_db, db_session):
    """A large import commits per batch, never once per game."""
    from services.api.main import IMPORT_COMMIT_BATCH_SIZE
    from services.ingest import ChessGame

    total_games = IMPORT_COMMIT_BATCH_SIZE + 50

    async def mock_generator(username, since=None):
        for i in range(total_games):
            yield ChessGame(
                url=f"https://www.chess.com/game/live/{i}",
                pgn='[Event "Test"]\n\n1. e4 e5 1/2-1/2',
                time_control="600",
                end_time=1704067200 + i,
                rated=True,
                white_username="testuser",
                black_username=f"opponent{i}",
                white_result="win",
                black_result="lose",
            )

    mock_import_games.side_effect = mock_generator

    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        response = client_with_db.post("/import/chesscom?username=testuser")

    assert response.status_code == 200
    data = response.json()
    assert data["games_count"] == total_games
    assert data["new_games"] == total_games
    assert data["skipped_duplicates"] == 0

    # Two game batches (200 + 50) plus the import-summary write.
    assert mock_commit.call_count == 3


def _chesscom_game(url: str, end_time: int) -> dict:
    """Raw Chess.com archive game payload (nested white/black), as the API returns."""
    return {
        "url": url,
        "pgn": '[Event "Test"]\n\n1. e4 e5 1/2-1/2',
        "time_control": "600",
        "end_time": end_time,
        "rated": True,
        "white": {"username": "testuser", "result": "win"},
        "black": {"username": "opponent", "result": "lose"},
    }


def test_import_chesscom_incremental_skips_older_months(client_with_db):
    """End-to-end: a second sync skips fully-imported older monthly archives,
    re-fetches only the latest month, and imports just the new games there.

    Drives the real import_all_games with the Chess.com HTTP layer mocked, so
    the incremental archive-selection logic is actually exercised.
    """
    from services.ingest import chesscom

    base = "https://api.chess.com/pub/player/testuser/games"
    archives = [f"{base}/2023/12", f"{base}/2024/01"]

    def ts(y, m, d):
        return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())

    # State the mocked archives serve, mutated between the two syncs.
    games_by_archive = {
        archives[0]: [_chesscom_game("g-2023-12-a", ts(2023, 12, 10))],
        archives[1]: [_chesscom_game("g-2024-01-a", ts(2024, 1, 5))],
    }
    fetched: list[str] = []

    async def fake_archives(username):
        return list(archives)

    async def fake_fetch(url):
        fetched.append(url)
        return list(games_by_archive.get(url, []))

    with (
        patch.object(chesscom, "get_player_archives", side_effect=fake_archives),
        patch.object(chesscom, "fetch_games_from_archive", side_effect=fake_fetch),
    ):
        # First sync: no stored games → full history, both months fetched.
        r1 = client_with_db.post("/import/chesscom?username=testuser")
        assert r1.status_code == 200
        assert r1.json()["new_games"] == 2
        assert fetched == archives

        # A new game lands in the current (latest) month between syncs.
        games_by_archive[archives[1]].append(
            _chesscom_game("g-2024-01-b", ts(2024, 1, 20))
        )
        fetched.clear()

        # Second sync: cutoff derived from newest stored end_time (2024/01).
        r2 = client_with_db.post("/import/chesscom?username=testuser")
        assert r2.status_code == 200
        data = r2.json()

    # Only the latest month was re-fetched; the older month was skipped.
    assert fetched == [archives[1]]
    # That month had one already-stored game and one new one.
    assert data["games_count"] == 2
    assert data["new_games"] == 1
    assert data["skipped_duplicates"] == 1


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

    async def mock_generator(username, since=None):
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


def test_daily_puzzles_use_utc_day_boundary(db_session, monkeypatch):
    """Daily-puzzle read and write both use the UTC day boundary.

    Independent of the server's local `date.today()`: mark_puzzles_used stamps
    the UTC date and get_daily_puzzles reads back the same "used today" set, so
    the rotation flips at the same midnight as the training streak.
    """
    from services.api.storage.puzzle_repository import PuzzleRepository

    fixed_utc_day = date(2026, 3, 14)
    # Patch the repository's day-boundary helper; leave real date.today() alone
    # so the test proves the code no longer depends on server-local time.
    monkeypatch.setattr(
        "services.api.storage.puzzle_repository.utc_today", lambda: fixed_utc_day
    )

    for i in range(3):
        _create_puzzle(
            db_session,
            f"utc-puz-{i}",
            "utcuser",
            source_game_id=f"utc-game-{i}",
            ply=10 + i,
        )
    db_session.commit()

    repo = PuzzleRepository(db_session)

    # Write: no explicit date -> stamps the UTC day.
    marked = repo.mark_puzzles_used("utcuser", ["utc-puz-0", "utc-puz-1"])
    assert marked == 2
    stamped = {
        p.used_on
        for p in db_session.query(PuzzleModel).filter(
            PuzzleModel.id.in_(["utc-puz-0", "utc-puz-1"])
        )
    }
    assert stamped == {fixed_utc_day}

    # Read: those two are treated as "used today" (UTC), so a request for 2
    # returns exactly them rather than the unused puzzle.
    selected = repo.get_daily_puzzles("utcuser", n=2)
    assert {p.id for p in selected} == {"utc-puz-0", "utc-puz-1"}


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


# --- Training integrity: no pre-exposure of the solution (audit gate 13) ---

# _create_puzzle seeds best_move_uci="d2d4" on the initial position, so in these
# tests "d2d4" is the correct solution, "e2e4" is legal-but-wrong, and "e2e5" is
# illegal.


def test_due_puzzles_do_not_leak_solution(client_with_db, db_session):
    """SCORED training payload must NOT ship the answer before an attempt."""
    _create_puzzle(db_session, "p-due-leak", "testuser")
    db_session.commit()

    response = client_with_db.get("/puzzles/due?username=testuser&n=5")
    assert response.status_code == 200
    puzzles = response.json()["puzzles"]
    assert puzzles, "expected the new puzzle to be returned for training"
    for p in puzzles:
        # The board renders from fen/side_to_move — but the solution (and the
        # original blunder, which narrows it) must never be pre-sent.
        assert "best_move_uci" not in p
        assert "accept_moves_uci" not in p
        assert "played_move_uci" not in p
        assert "fen" in p and "side_to_move" in p


def test_daily_puzzles_do_not_leak_solution(client_with_db, db_session):
    """Post-generation warm-up payload must not carry the solution either."""
    _create_puzzle(db_session, "p-daily-leak", "testuser")
    db_session.commit()

    response = client_with_db.post(
        "/daily-puzzle-sessions", json={"username": "testuser", "n": 1}
    )
    assert response.status_code == 200
    for p in response.json()["puzzles"]:
        assert "best_move_uci" not in p
        assert "accept_moves_uci" not in p
        assert "played_move_uci" not in p


def test_check_endpoint_correct_move(client_with_db, db_session):
    """A correct move returns correct=True and reveals no solution."""
    _create_puzzle(db_session, "p-check-ok", "testuser")
    db_session.commit()

    response = client_with_db.post(
        "/puzzles/p-check-ok/check",
        json={"username": "testuser", "attempted_move": "d2d4"},
    )
    assert response.status_code == 200
    body = response.json()
    # Legacy single-move puzzle (no stored line): correct move completes it, with
    # no forced reply to play out.
    assert body["correct"] is True
    assert body["result"] == "pass"
    assert body["complete"] is True
    assert body["reply"] is None
    assert body["next_ply_index"] is None
    # The solution never appears in the response body.
    assert "d2d4" not in {v for v in body.values() if isinstance(v, str)}
    assert "best_move_uci" not in body


def test_check_endpoint_wrong_move(client_with_db, db_session):
    """A legal-but-wrong move returns correct=False without revealing the answer."""
    _create_puzzle(db_session, "p-check-wrong", "testuser")
    db_session.commit()

    response = client_with_db.post(
        "/puzzles/p-check-wrong/check",
        json={"username": "testuser", "attempted_move": "e2e4"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["correct"] is False
    assert body["result"] == "fail"
    assert body["complete"] is False
    assert body["reply"] is None


def test_check_endpoint_illegal_move_is_incorrect(client_with_db, db_session):
    """An illegal move is simply incorrect (never a 500)."""
    _create_puzzle(db_session, "p-check-illegal", "testuser")
    db_session.commit()

    response = client_with_db.post(
        "/puzzles/p-check-illegal/check",
        json={"username": "testuser", "attempted_move": "e2e5"},
    )
    assert response.status_code == 200
    assert response.json()["correct"] is False


def test_check_endpoint_records_nothing(client_with_db, db_session):
    """/check is pure feedback — it must not create a review or stats row."""
    from services.api.models import PuzzleReview

    _create_puzzle(db_session, "p-check-norecord", "testuser")
    db_session.commit()

    client_with_db.post(
        "/puzzles/p-check-norecord/check",
        json={"username": "testuser", "attempted_move": "d2d4"},
    )
    assert db_session.query(PuzzleReview).count() == 0


def test_check_endpoint_puzzle_not_found(client_with_db):
    response = client_with_db.post(
        "/puzzles/does-not-exist/check",
        json={"username": "testuser", "attempted_move": "d2d4"},
    )
    assert response.status_code == 404


def test_reveal_endpoint_returns_solution(client_with_db, db_session):
    """Explicit reveal returns the solution on demand."""
    _create_puzzle(db_session, "p-reveal", "testuser")
    db_session.commit()

    response = client_with_db.post(
        "/puzzles/p-reveal/reveal", json={"username": "testuser"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["best_move_uci"] == "d2d4"
    assert "d2d4" in body["accept_moves_uci"]


def test_reveal_endpoint_puzzle_not_found(client_with_db):
    response = client_with_db.post(
        "/puzzles/nope/reveal", json={"username": "testuser"}
    )
    assert response.status_code == 404


# --- Full principal-variation (multi-move) puzzles (SCORECARD dim 12 -> 9) ---

# A Queen's-Gambit line legal from the seeded start position: the solver plays
# the even plies (d2d4, c2c4); the opponent's forced replies are the odd plies
# (g8f6, e7e6). "c2c4" is the solver's SECOND move — it must never leak from a
# ply-0 /check.
_PV_LINE = "d2d4 g8f6 c2c4 e7e6"


def test_check_pv_first_move_returns_forced_reply_not_next_answer(
    client_with_db, db_session
):
    """A correct first move returns the opponent's forced reply and marks the
    line incomplete — WITHOUT leaking the solver's next move."""
    _create_puzzle(db_session, "p-pv-1", "testuser", solution_pv=_PV_LINE)
    db_session.commit()

    response = client_with_db.post(
        "/puzzles/p-pv-1/check",
        json={"username": "testuser", "attempted_move": "d2d4", "ply_index": 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["correct"] is True
    assert body["result"] == "pass"
    assert body["reply"] == "g8f6"  # opponent's forced reply is safe to reveal
    assert body["complete"] is False
    assert body["next_ply_index"] == 2
    # The solver's UPCOMING answer (ply 2) must never appear in the payload.
    leaked = {v for v in body.values() if isinstance(v, str)}
    assert "c2c4" not in leaked
    assert "e7e6" not in leaked


def test_check_pv_completes_on_last_user_move(client_with_db, db_session):
    """The final correct user move completes the line (records the pass)."""
    _create_puzzle(db_session, "p-pv-2", "testuser", solution_pv=_PV_LINE)
    db_session.commit()

    response = client_with_db.post(
        "/puzzles/p-pv-2/check",
        json={"username": "testuser", "attempted_move": "c2c4", "ply_index": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["correct"] is True
    assert body["complete"] is True
    assert body["reply"] == "e7e6"  # last forced reply is still played out
    assert body["next_ply_index"] is None


def test_check_pv_wrong_mid_line_move_fails(client_with_db, db_session):
    """A wrong move at a later ply fails the line with no reply."""
    _create_puzzle(db_session, "p-pv-3", "testuser", solution_pv=_PV_LINE)
    db_session.commit()

    response = client_with_db.post(
        "/puzzles/p-pv-3/check",
        # b1c3 is legal but not the PV move (c2c4) at ply 2.
        json={"username": "testuser", "attempted_move": "b1c3", "ply_index": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["correct"] is False
    assert body["complete"] is False
    assert body["reply"] is None


def test_check_pv_rejects_odd_or_out_of_range_ply(client_with_db, db_session):
    """A ply index that is not one of the solver's moves is rejected outright."""
    _create_puzzle(db_session, "p-pv-4", "testuser", solution_pv=_PV_LINE)
    db_session.commit()

    for bad_ply in (1, 4, -2):
        response = client_with_db.post(
            "/puzzles/p-pv-4/check",
            json={
                "username": "testuser",
                "attempted_move": "d2d4",
                "ply_index": bad_ply,
            },
        )
        assert response.status_code == 200
        assert response.json()["correct"] is False


def test_check_legacy_puzzle_ignores_ply_index(client_with_db, db_session):
    """A legacy (null-PV) puzzle trains single-move regardless of ply_index."""
    _create_puzzle(db_session, "p-pv-legacy", "testuser")  # no solution_pv
    db_session.commit()

    response = client_with_db.post(
        "/puzzles/p-pv-legacy/check",
        json={"username": "testuser", "attempted_move": "d2d4", "ply_index": 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["correct"] is True
    assert body["complete"] is True  # single correct move completes it
    assert body["reply"] is None


def test_reveal_returns_full_pv(client_with_db, db_session):
    """Reveal hands back the whole line on demand."""
    _create_puzzle(db_session, "p-pv-reveal", "testuser", solution_pv=_PV_LINE)
    db_session.commit()

    response = client_with_db.post(
        "/puzzles/p-pv-reveal/reveal", json={"username": "testuser"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["best_move_uci"] == "d2d4"
    assert body["solution_pv"] == ["d2d4", "g8f6", "c2c4", "e7e6"]


def test_due_puzzles_do_not_leak_solution_pv(client_with_db, db_session):
    """The scored training payload must never carry the stored line."""
    _create_puzzle(db_session, "p-pv-noleak", "testuser", solution_pv=_PV_LINE)
    db_session.commit()

    response = client_with_db.get("/puzzles/due?username=testuser&n=5")
    assert response.status_code == 200
    for p in response.json()["puzzles"]:
        assert "solution_pv" not in p


def test_review_records_pass_only_on_full_line(client_with_db, db_session):
    """A verified pass requires the WHOLE line; a partial or wrong line fails."""
    _create_puzzle(db_session, "p-pv-review", "testuser", solution_pv=_PV_LINE)
    db_session.commit()

    # Full, correct line -> server-verified PASS.
    ok = client_with_db.post(
        "/puzzles/p-pv-review/review",
        json={"username": "testuser", "result": "pass", "attempted_move": "d2d4 c2c4"},
    )
    assert ok.status_code == 200
    ok_body = ok.json()
    assert ok_body["result"] == "pass"
    assert ok_body["verified"] is True

    # Only the first move played -> the line is not complete -> FAIL, even though
    # the client self-reports a pass.
    partial = client_with_db.post(
        "/puzzles/p-pv-review/review",
        json={"username": "testuser", "result": "pass", "attempted_move": "d2d4"},
    )
    assert partial.status_code == 200
    assert partial.json()["result"] == "fail"

    # Right first move, wrong second move -> FAIL.
    wrong = client_with_db.post(
        "/puzzles/p-pv-review/review",
        json={"username": "testuser", "result": "pass", "attempted_move": "d2d4 b1c3"},
    )
    assert wrong.status_code == 200
    assert wrong.json()["result"] == "fail"


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


@patch("services.api.main._ENGINE_EVAL_MAX_INFLIGHT", 0)
def test_engine_eval_rejects_when_at_capacity():
    """The unauthenticated /engine/eval guard returns 429 when saturated."""
    response = client.post("/engine/eval", json={"fen": "any"})
    assert response.status_code == 429
    assert "capacity" in response.json()["detail"].lower()


@patch("services.api.main.get_or_compute_eval")
def test_engine_eval_terminal_position_is_not_500(mock_eval):
    """A terminal FEN yields an EvalResult with best_move_uci=None. The
    response model must accept that (200 terminal shape) rather than raising a
    Pydantic ValidationError that escapes the handlers as a 500."""
    from services.api.engine import EvalResult

    mock_eval.return_value = EvalResult(
        best_move_uci=None, eval=-100.0, mate_in=0, is_terminal=True
    )
    response = client.post(
        "/engine/eval",
        json={"fen": "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"},
    )
    assert response.status_code != 500
    assert response.status_code == 200
    body = response.json()
    assert body["is_terminal"] is True
    assert body["best_move_uci"] is None
    assert body["mate_in"] == 0


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
