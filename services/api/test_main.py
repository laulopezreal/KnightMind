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
