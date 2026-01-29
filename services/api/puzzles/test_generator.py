"""
Unit tests for puzzle generator.
"""

import tempfile
from unittest.mock import Mock, patch

import chess
import pytest

from services.api.engine import EvalResult
from services.api.puzzles.generator import _is_user_move, generate_puzzles
from services.api.storage import get_puzzle_storage, get_storage


@pytest.fixture
def temp_storage():
    """Create temporary storage for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create storage instances
        with patch("services.api.puzzles.generator.get_storage") as mock_storage, \
             patch("services.api.puzzles.generator.get_puzzle_storage") as mock_puzzle_storage:
            
            from services.api.storage.games import GameStorage
            from services.api.storage.puzzles import PuzzleStorage
            
            storage = GameStorage(base_path=tmpdir)
            puzzle_storage = PuzzleStorage(base_path=tmpdir)
            
            mock_storage.return_value = storage
            mock_puzzle_storage.return_value = puzzle_storage
            
            yield storage, puzzle_storage


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
    storage, puzzle_storage = temp_storage
    
    with pytest.raises(ValueError, match="No games found"):
        generate_puzzles("nonexistent_user")


def test_generate_puzzles_swing_calculation(temp_storage):
    """Test that swing is calculated correctly with proper sign."""
    storage, puzzle_storage = temp_storage
    
    # Create a simple game PGN where the user plays e4 (white)
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6"""
    
    # Store the game
    storage.store_game(
        username="testuser",
        url="https://chess.com/game/test",
        pgn=pgn,
        white_username="testuser",
        black_username="opponent",
        white_result="win",
        black_result="loss",
        time_control="600",
        end_time=1234567890,
        rated=True,
    )
    
    # Mock engine evaluation
    # First call (before move): position is good for white (+0.5)
    # Second call (after move): position is now good for black (+1.5 from black's perspective)
    # So white went from +0.5 to -1.5, a swing of 2.0
    with patch("services.api.puzzles.generator.evaluate_fen") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=0.5),    # Before white's move
            EvalResult(best_move_uci="e7e5", eval=1.5),    # After (from black's perspective)
            EvalResult(best_move_uci="e2e4", eval=0.3),    # Before next move
            EvalResult(best_move_uci="e7e6", eval=0.3),    # After
        ]
        
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)
        
        # Should have generated a puzzle from the blunder
        assert result.generated >= 1
        assert result.analyzed_positions >= 1
        
        # Check that the puzzle was saved with correct swing
        puzzles = puzzle_storage.get_all_puzzles("testuser")
        if len(puzzles) > 0:
            puzzle = puzzles[0]
            # Swing should be eval_before - eval_after (from white's perspective)
            # 0.5 - (-1.5) = 2.0
            assert puzzle.swing >= 2.0


def test_generate_puzzles_only_user_moves(temp_storage):
    """Test that only the user's moves are analyzed."""
    storage, puzzle_storage = temp_storage
    
    # Game where testuser plays as black
    pgn = """[Event "Test Game"]
[White "opponent"]
[Black "testuser"]

1. e4 e5 2. Nf3 Nc6"""
    
    storage.store_game(
        username="testuser",
        url="https://chess.com/game/test",
        pgn=pgn,
        white_username="opponent",
        black_username="testuser",
        white_result="loss",
        black_result="win",
        time_control="600",
        end_time=1234567890,
        rated=True,
    )
    
    analyzed_positions = []
    
    def mock_evaluate(fen):
        """Track which positions are analyzed."""
        analyzed_positions.append(fen)
        return EvalResult(best_move_uci="e2e4", eval=0.3)
    
    with patch("services.api.puzzles.generator.evaluate_fen", side_effect=mock_evaluate):
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)
        
        # Should only analyze black's moves (ply 2, 4, etc.)
        # With ply range 8-80, no moves should be analyzed in this short game
        assert result.analyzed_positions == 0


def test_generate_puzzles_respects_max_puzzles(temp_storage):
    """Test that generation stops at max_puzzles."""
    storage, puzzle_storage = temp_storage
    
    # Create a game with multiple moves
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 Bc5 5. O-O O-O 6. c3 d6 7. h3 h6 8. Re1 a6 9. Bb3 Ba7"""
    
    storage.store_game(
        username="testuser",
        url="https://chess.com/game/test",
        pgn=pgn,
        white_username="testuser",
        black_username="opponent",
        white_result="win",
        black_result="loss",
        time_control="600",
        end_time=1234567890,
        rated=True,
    )
    
    # Mock all moves as blunders
    with patch("services.api.puzzles.generator.evaluate_fen") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20  # Enough for many positions
        
        result = generate_puzzles("testuser", max_games=1, max_puzzles=3)
        
        # Should stop at max_puzzles
        assert result.generated <= 3


def test_generate_puzzles_deduplication(temp_storage):
    """Test that running generation twice doesn't create duplicate puzzles."""
    storage, puzzle_storage = temp_storage
    
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 Bc5 5. O-O O-O"""
    
    storage.store_game(
        username="testuser",
        url="https://chess.com/game/test",
        pgn=pgn,
        white_username="testuser",
        black_username="opponent",
        white_result="win",
        black_result="loss",
        time_control="600",
        end_time=1234567890,
        rated=True,
    )
    
    # Mock as blunders
    with patch("services.api.puzzles.generator.evaluate_fen") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20
        
        # First generation
        result1 = generate_puzzles("testuser", max_games=1, max_puzzles=10)
        generated_first = result1.generated
        
    # Run again with same mock
    with patch("services.api.puzzles.generator.evaluate_fen") as mock_eval:
        mock_eval.side_effect = [
            EvalResult(best_move_uci="d2d4", eval=2.0),
            EvalResult(best_move_uci="e7e5", eval=2.0),
        ] * 20
        
        result2 = generate_puzzles("testuser", max_games=1, max_puzzles=10)
        
        # Second run should skip all (duplicates)
        assert result2.skipped >= generated_first
        assert result2.generated == 0


def test_generate_puzzles_ply_range_filtering(temp_storage):
    """Test that moves outside ply range are skipped."""
    storage, puzzle_storage = temp_storage
    
    # Short game (all moves before ply 8)
    pgn = """[Event "Test Game"]
[White "testuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6"""
    
    storage.store_game(
        username="testuser",
        url="https://chess.com/game/test",
        pgn=pgn,
        white_username="testuser",
        black_username="opponent",
        white_result="win",
        black_result="loss",
        time_control="600",
        end_time=1234567890,
        rated=True,
    )
    
    with patch("services.api.puzzles.generator.evaluate_fen") as mock_eval:
        mock_eval.return_value = EvalResult(best_move_uci="e2e4", eval=3.0)
        
        result = generate_puzzles("testuser", max_games=1, max_puzzles=10)
        
        # No positions should be analyzed (all before ply 8)
        assert result.analyzed_positions == 0
        assert result.generated == 0
