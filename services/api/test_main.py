import shutil
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.api.main import app
from services.api.storage import GameStorage


@pytest.fixture
def temp_storage():
    """Create a temporary storage directory for tests."""
    temp_dir = tempfile.mkdtemp()
    storage = GameStorage(temp_dir)
    yield storage
    shutil.rmtree(temp_dir)


@pytest.fixture
def client_with_temp_storage(temp_storage):
    """Create a test client with temporary storage."""
    with patch("services.api.main.get_storage", return_value=temp_storage):
        yield TestClient(app)


client = TestClient(app)


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


@patch("services.api.main.fetch_games_from_archive")
@patch("services.api.main.get_player_archives")
def test_get_openings_with_games(mock_get_archives, mock_fetch_games, client_with_temp_storage):
    """Test /openings endpoint with imported games."""
    # First import some games
    mock_get_archives.return_value = MOCK_ARCHIVES
    mock_fetch_games.return_value = MOCK_GAMES

    import_response = client_with_temp_storage.post("/import/chesscom?username=testuser")
    assert import_response.status_code == 200

    # Now fetch openings
    response = client_with_temp_storage.get("/openings?username=testuser")
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


def test_get_openings_no_games(client_with_temp_storage):
    """Test /openings returns 404 when user has no games."""
    response = client_with_temp_storage.get("/openings?username=unknownuser")
    assert response.status_code == 404
    assert "no games" in response.json()["detail"].lower()


# --- Import endpoint tests with mocking ---

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


@patch("services.api.main.fetch_games_from_archive")
@patch("services.api.main.get_player_archives")
def test_import_chesscom_success(mock_get_archives, mock_fetch_games, client_with_temp_storage):
    """Test successful import of games."""
    mock_get_archives.return_value = MOCK_ARCHIVES
    mock_fetch_games.return_value = MOCK_GAMES

    response = client_with_temp_storage.post("/import/chesscom?username=testuser")

    assert response.status_code == 200
    data = response.json()
    assert data["games_count"] == 2
    assert data["new_games"] == 2
    assert data["skipped_duplicates"] == 0
    assert "testuser" in data["message"]


@patch("services.api.main.fetch_games_from_archive")
@patch("services.api.main.get_player_archives")
def test_import_chesscom_deduplication(mock_get_archives, mock_fetch_games, client_with_temp_storage):
    """Test that duplicate games are not re-imported."""
    mock_get_archives.return_value = MOCK_ARCHIVES
    mock_fetch_games.return_value = MOCK_GAMES

    # First import
    response1 = client_with_temp_storage.post("/import/chesscom?username=testuser")
    assert response1.status_code == 200
    assert response1.json()["new_games"] == 2

    # Second import should skip duplicates
    response2 = client_with_temp_storage.post("/import/chesscom?username=testuser")
    assert response2.status_code == 200
    data = response2.json()
    assert data["new_games"] == 0
    assert data["skipped_duplicates"] == 2
    assert data["games_count"] == 2  # Total still 2


@patch("services.api.main.get_player_archives")
def test_import_chesscom_user_not_found(mock_get_archives, client_with_temp_storage):
    """Test error handling for non-existent user."""
    from services.ingest import UserNotFoundError
    mock_get_archives.side_effect = UserNotFoundError("nonexistent_user")

    response = client_with_temp_storage.post("/import/chesscom?username=nonexistent_user")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@patch("services.api.main.get_player_archives")
def test_import_chesscom_rate_limit(mock_get_archives, client_with_temp_storage):
    """Test error handling for rate limiting."""
    from services.ingest import RateLimitError
    mock_get_archives.side_effect = RateLimitError(retry_after=60)

    response = client_with_temp_storage.post("/import/chesscom?username=testuser")

    assert response.status_code == 429
    assert "rate limit" in response.json()["detail"].lower()


@patch("services.api.main.get_player_archives")
def test_import_chesscom_network_error(mock_get_archives, client_with_temp_storage):
    """Test error handling for network errors."""
    from services.ingest import NetworkError
    mock_get_archives.side_effect = NetworkError("Connection refused")

    response = client_with_temp_storage.post("/import/chesscom?username=testuser")

    assert response.status_code == 502
    assert "network" in response.json()["detail"].lower()


@patch("services.api.main.get_player_archives")
def test_import_chesscom_no_games(mock_get_archives, client_with_temp_storage):
    """Test handling user with no games."""
    mock_get_archives.return_value = []  # No archives

    response = client_with_temp_storage.post("/import/chesscom?username=newuser")

    assert response.status_code == 200
    data = response.json()
    assert data["games_count"] == 0
    assert data["new_games"] == 0


# --- Puzzle generation endpoint tests ---

@patch("services.api.main.generate_puzzles")
def test_generate_puzzles_success(mock_generate, client_with_temp_storage):
    """Test successful puzzle generation."""
    from services.api.puzzles import GenerationResult
    
    mock_generate.return_value = GenerationResult(
        generated=5,
        skipped=2,
        analyzed_positions=100
    )
    
    response = client_with_temp_storage.post(
        "/puzzles/generate?username=testuser&max_games=10&max_puzzles=10"
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["generated"] == 5
    assert data["skipped"] == 2
    assert data["analyzed_positions"] == 100
    assert "100" in data["message"]


@patch("services.api.main.generate_puzzles")
def test_generate_puzzles_no_games(mock_generate, client_with_temp_storage):
    """Test puzzle generation when user has no games."""
    mock_generate.side_effect = ValueError("No games found for user 'unknownuser'")
    
    response = client_with_temp_storage.post("/puzzles/generate?username=unknownuser")
    
    assert response.status_code == 404
    assert "no games" in response.json()["detail"].lower()


def test_generate_puzzles_missing_username():
    """Test that /puzzles/generate requires username parameter."""
    response = client.post("/puzzles/generate")
    assert response.status_code == 422  # Validation error


# --- Daily puzzles endpoint tests ---

@pytest.fixture
def temp_puzzle_storage():
    """Create a temporary puzzle storage directory for tests."""
    from services.api.storage import PuzzleStorage
    temp_dir = tempfile.mkdtemp()
    storage = PuzzleStorage(temp_dir)
    yield storage
    shutil.rmtree(temp_dir)


@pytest.fixture
def client_with_temp_puzzle_storage(temp_puzzle_storage):
    """Create a test client with temporary puzzle storage."""
    with patch("services.api.main.get_puzzle_storage", return_value=temp_puzzle_storage):
        yield TestClient(app), temp_puzzle_storage


def test_get_daily_puzzles_success(client_with_temp_puzzle_storage):
    """Test successful retrieval of daily puzzles."""
    from datetime import date
    from services.api.storage import Puzzle
    
    client, puzzle_storage = client_with_temp_puzzle_storage
    
    # Create test puzzles
    test_puzzles = [
        Puzzle(
            id=f"puzzle-{i}",
            username="testuser",
            source_game_id=f"game-{i}",
            ply=10 + i,
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
            created_at=f"2024-01-0{i+1}T12:00:00Z",
            used_on=None
        )
        for i in range(5)
    ]
    
    # Save puzzles
    for puzzle in test_puzzles:
        puzzle_storage.save_puzzle(
            username=puzzle.username,
            source_game_id=puzzle.source_game_id,
            ply=puzzle.ply,
            fen=puzzle.fen,
            side_to_move=puzzle.side_to_move,
            played_move_uci=puzzle.played_move_uci,
            best_move_uci=puzzle.best_move_uci,
            eval_before=puzzle.eval_before,
            eval_after=puzzle.eval_after,
            swing=puzzle.swing
        )
    
    # Get daily puzzles
    response = client.get("/puzzles/daily?username=testuser&n=3")
    
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 3
    assert len(data["puzzles"]) == 3
    
    # Verify all returned puzzles are marked with today's date
    today_str = date.today().isoformat()
    for puzzle_data in data["puzzles"]:
        assert puzzle_data["used_on"] == today_str
        assert puzzle_data["username"] == "testuser"


def test_get_daily_puzzles_rotation(client_with_temp_puzzle_storage):
    """Test that puzzles rotate correctly - unused first, then used."""
    from datetime import date, timedelta
    from services.api.storage import Puzzle
    
    client, puzzle_storage = client_with_temp_puzzle_storage
    
    # Create 5 puzzles and collect IDs for the first 2 to mark as used
    yesterday_date = date.today() - timedelta(days=1)
    used_puzzle_ids = []
    
    for i in range(5):
        _, puzzle_id = puzzle_storage.save_puzzle(
            username="testuser",
            source_game_id=f"game-{i}",
            ply=10 + i,
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0
        )
        if i < 2:
            used_puzzle_ids.append(puzzle_id)
    
    # Mark the first 2 puzzles as used yesterday in a single batch
    puzzle_storage.mark_puzzles_used("testuser", used_puzzle_ids, yesterday_date)
    
    # Request 4 puzzles - should get 3 unused + 1 used
    response = client.get("/puzzles/daily?username=testuser&n=4")
    
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 4
    
    # All should now be marked with today
    today_str = date.today().isoformat()
    for puzzle_data in data["puzzles"]:
        assert puzzle_data["used_on"] == today_str


def test_get_daily_puzzles_no_puzzles(client_with_temp_puzzle_storage):
    """Test 404 when user has no puzzles."""
    client, _ = client_with_temp_puzzle_storage
    
    response = client.get("/puzzles/daily?username=unknownuser&n=5")
    
    assert response.status_code == 404
    assert "no puzzles" in response.json()["detail"].lower()
    assert "generate puzzles first" in response.json()["detail"].lower()


def test_get_daily_puzzles_validation():
    """Test parameter validation for daily puzzles endpoint."""
    # Missing username
    response = client.get("/puzzles/daily")
    assert response.status_code == 422
    
    # n too small
    response = client.get("/puzzles/daily?username=test&n=0")
    assert response.status_code == 422
    
    # n too large
    response = client.get("/puzzles/daily?username=test&n=21")
    assert response.status_code == 422


def test_get_daily_puzzles_idempotent(client_with_temp_puzzle_storage):
    """Test that calling endpoint multiple times on same day returns same puzzles."""
    from datetime import date
    
    client, puzzle_storage = client_with_temp_puzzle_storage
    
    # Create test puzzles
    for i in range(5):
        puzzle_storage.save_puzzle(
            username="testuser",
            source_game_id=f"game-{i}",
            ply=10 + i,
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0
        )
    
    # First call
    response1 = client.get("/puzzles/daily?username=testuser&n=3")
    assert response1.status_code == 200
    puzzle_ids_1 = {p["id"] for p in response1.json()["puzzles"]}
    
    # Second call - should return same puzzles (already marked for today)
    response2 = client.get("/puzzles/daily?username=testuser&n=3")
    assert response2.status_code == 200
    puzzle_ids_2 = {p["id"] for p in response2.json()["puzzles"]}
    
    # Should be the same puzzles
    assert puzzle_ids_1 == puzzle_ids_2


