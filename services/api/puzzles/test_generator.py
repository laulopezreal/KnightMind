"""
Unit tests for puzzle generator.
"""

from contextlib import contextmanager
from unittest.mock import Mock, patch

import chess
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import Base
from services.api.engine import EvalResult
from services.api.models import Game
from services.api.puzzles.generator import _is_user_move, generate_puzzles
from services.api.storage.puzzle_repository import PuzzleRepository


@pytest.fixture
def db_session():
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
        Base.metadata.drop_all(engine)


@pytest.fixture
def temp_storage(db_session):
    """Patch SessionLocal so generator uses our in-memory test DB."""

    @contextmanager
    def fake_session_local():
        yield db_session

    with patch("services.api.puzzles.generator.SessionLocal", fake_session_local):
        yield db_session


def _store_game(db, pgn: str, username: str = "testuser", url: str = "https://chess.com/game/test",
                white_username: str = "testuser", black_username: str = "opponent",
                white_result: str = "win", black_result: str = "loss"):
    """Helper to store a game in the DB."""
    import hashlib
    game_id = hashlib.sha256(url.encode()).hexdigest()[:16]
    db.add(Game(
        game_id=game_id,
        url=url,
        username=username.lower(),
        white_username=white_username,
        black_username=black_username,
        white_result=white_result,
        black_result=black_result,
        time_control="600",
        end_time=1234567890,
        rated=True,
        pgn_blob=pgn,
    ))
    db.commit()


def test_is_user_move_white():
    """Test detecting when it's the user's move as white."""
    board = chess.Board()  # White to move

    assert _is_user_move(board, "player1", "player1", "player2") is True
    assert _is_user_move(board, "player2", "player1", "player2") is False


def test_is_user_move_black():
    """Test detecting when it's the user's move as black."""
    board = chess.Board()
    board.push(chess.Move.from_uci("e2e4"))  # Black to move

    assert _is_user_move(board, "player1", "player1", "player2") is False
    assert _is_user_move(board, "player2", "player1", "player2") is True


def test_is_user_move_case_insensitive():
    """Test that username matching is case-insensitive."""
    board = chess.Board()

    assert _is_user_move(board, "Player1", "player1", "player2") is True
    assert _is_user_move(board, "PLAYER1", "player1", "player2") is True


def test_generate_puzzles_no_games(temp_storage):
    """Test that generating puzzles with no games raises ValueError."""
    with pytest.raises(ValueError, match="No games found"):
        generate_puzzles("nonexistent_user")


@patch("services.api.puzzles.generator.create_engine")
@patch("services.api.puzzles.generator.get_ply_range")
def test_generate_puzzles_swing_calculation(mock_get_ply_range, mock_create_engine, temp_storage):
    """Test that swing is calculated correctly with proper sign."""
    db = temp_storage
    mock_create_engine.return_value = Mock()
    mock_get_ply_range.return_value = (0, 100)

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6"""

    _store_game(db, pgn)

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=0.5),    # Before white's move
            EvalResult(best_move_uci="e7e5", eval=1.5),    # After (from black's perspective)
            EvalResult(best_move_uci="e2e4", eval=0.3),    # Before next move
            EvalResult(best_move_uci="e7e6", eval=0.3),    # After
        ]

        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

        assert result.generated >= 1
        assert result.analyzed_positions >= 1

        # Check that the puzzle was saved with correct swing
        puzzle_repo = PuzzleRepository(db)
        puzzles = puzzle_repo.get_all_puzzles("testuser")
        if len(puzzles) > 0:
            puzzle = puzzles[0]
            assert puzzle.swing >= 2.0


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_with_mocked_engine(mock_create_engine, temp_storage):
    """Test generation returns empty result when engine creation fails."""
    db = temp_storage
    mock_create_engine.side_effect = RuntimeError("Engine unavailable")

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6"""

    _store_game(db, pgn)

    result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

    assert result.generated == 0
    assert result.skipped == 0
    assert result.analyzed_positions == 0


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_only_user_moves(mock_create_engine, temp_storage):
    """Test that only the user's moves are analyzed."""
    db = temp_storage
    mock_create_engine.return_value = Mock()

    pgn = """[Event "Test Game"]
[White "opponent"]
[Black "testuser"]

1. e4 e5 2. Nf3 Nc6"""

    _store_game(db, pgn, white_username="opponent", black_username="testuser",
                white_result="loss", black_result="win")

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.return_value = EvalResult(best_move_uci="e2e4", eval=0.3)
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

        # With ply range 8-80, no moves should be analyzed in this short game
        assert result.analyzed_positions == 0


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_respects_max_puzzles(mock_create_engine, temp_storage):
    """Test that generation stops at max_puzzles."""
    db = temp_storage
    mock_create_engine.return_value = Mock()

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 Bc5 5. O-O O-O 6. c3 d6 7. h3 h6 8. Re1 a6 9. Bb3 Ba7"""

    _store_game(db, pgn)

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20

        result = generate_puzzles("testuser", max_games=1, max_puzzles=3)

        assert result.generated <= 3


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_deduplication(mock_create_engine, temp_storage):
    """Test that running generation twice doesn't create duplicate puzzles."""
    db = temp_storage
    mock_create_engine.return_value = Mock()

    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 Bc5 5. O-O O-O"""

    _store_game(db, pgn)

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20

        result1 = generate_puzzles("testuser", max_games=1, max_puzzles=10)
        generated_first = result1.generated

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20

        result2 = generate_puzzles("testuser", max_games=1, max_puzzles=10)

        assert result2.skipped >= generated_first
        assert result2.generated == 0


@patch("services.api.puzzles.generator.create_engine")
def test_generate_puzzles_ply_range_filtering(mock_create_engine, temp_storage):
    """Test that moves outside ply range are skipped."""
    db = temp_storage
    mock_create_engine.return_value = Mock()

    # Short game (all moves before ply 8)
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6"""

    _store_game(db, pgn)

    with patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval:
        mock_eval.return_value = EvalResult(best_move_uci="e2e4", eval=3.0)

        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)

        assert result.analyzed_positions == 0
        assert result.generated == 0
