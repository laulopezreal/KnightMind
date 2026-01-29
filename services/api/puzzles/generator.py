"""
Puzzle generation logic.

Analyzes user games to find blunders and generate puzzles.
Uses bounded compute to work on Render and similar platforms.
"""

import io
import os
from dataclasses import dataclass

import chess
import chess.pgn

from services.api.engine import evaluate_fen
from services.api.storage import get_puzzle_storage, get_storage


def get_swing_threshold() -> float:
    """Get the minimum evaluation swing to consider a move a blunder."""
    return float(os.environ.get("SWING_THRESHOLD", "2.0"))


def get_ply_range() -> tuple[int, int]:
    """Get the range of plies to analyze."""
    start = int(os.environ.get("MAX_PLIES_START", "8"))
    end = int(os.environ.get("MAX_PLIES_END", "80"))
    return start, end


@dataclass
class GenerationResult:
    """Result of puzzle generation."""

    generated: int  # Number of new puzzles created
    skipped: int  # Number of duplicates skipped
    analyzed_positions: int  # Total positions analyzed


def _is_user_move(board: chess.Board, username: str, white_username: str, black_username: str) -> bool:
    """
    Check if it's the user's turn to move.

    Args:
        board: Current board position
        username: Username of the player
        white_username: White player's username
        black_username: Black player's username

    Returns:
        True if it's the user's turn
    """
    username_lower = username.lower()
    if board.turn == chess.WHITE:
        return white_username.lower() == username_lower
    else:
        return black_username.lower() == username_lower


def generate_puzzles(
    username: str,
    max_games: int = 30,
    max_puzzles: int = 30,
) -> GenerationResult:
    """
    Generate puzzles from a user's imported games by detecting blunders.

    Args:
        username: Username to generate puzzles for
        max_games: Maximum number of recent games to analyze
        max_puzzles: Maximum number of puzzles to generate

    Returns:
        GenerationResult with counts

    Raises:
        ValueError: If user has no games
    """
    storage = get_storage()
    puzzle_storage = get_puzzle_storage()

    # Get user's games
    all_metadata = storage.get_all_metadata(username)
    if not all_metadata:
        raise ValueError(f"No games found for user '{username}'")

    # Take most recent games (already sorted by end_time descending)
    recent_games = all_metadata[:max_games]

    # Get configuration
    swing_threshold = get_swing_threshold()
    ply_start, ply_end = get_ply_range()

    generated = 0
    skipped = 0
    analyzed_positions = 0

    for game_meta in recent_games:
        if generated >= max_puzzles:
            break

        # Load PGN
        pgn_text = storage.get_pgn(username, game_meta.game_id)
        if not pgn_text:
            continue

        # Parse game
        pgn_io = io.StringIO(pgn_text)
        game = chess.pgn.read_game(pgn_io)
        if not game:
            continue

        # Get player usernames
        white_username = game.headers.get("White", "")
        black_username = game.headers.get("Black", "")

        # Walk through the game
        board = game.board()
        ply = 0

        for move in game.mainline_moves():
            ply += 1

            # Skip moves outside the target range
            if ply < ply_start or ply > ply_end:
                board.push(move)
                continue

            # Only analyze when it's the user's move
            if not _is_user_move(board, username, white_username, black_username):
                board.push(move)
                continue

            analyzed_positions += 1

            # Get FEN before the move
            fen_before = board.fen()
            side_to_move = "white" if board.turn == chess.WHITE else "black"

            try:
                # Evaluate position before the move
                eval_result_before = evaluate_fen(fen_before)
                eval_before = eval_result_before.eval
                best_move_uci = eval_result_before.best_move_uci

                # Make the user's move
                played_move_uci = move.uci()
                board.push(move)

                # Evaluate position after the move
                fen_after = board.fen()
                eval_result_after = evaluate_fen(fen_after)
                eval_after = eval_result_after.eval

                # Calculate swing
                # Important: eval is always from side-to-move perspective
                # So we need to flip the sign for eval_after since it's from opponent's perspective
                swing = eval_before - (-eval_after)

                # Check if this is a blunder
                if swing >= swing_threshold:
                    # Save puzzle
                    is_new, _ = puzzle_storage.save_puzzle(
                        username=username,
                        source_game_id=game_meta.game_id,
                        ply=ply,
                        fen=fen_before,
                        side_to_move=side_to_move,
                        played_move_uci=played_move_uci,
                        best_move_uci=best_move_uci,
                        eval_before=eval_before,
                        eval_after=-eval_after,  # Store from original player's perspective
                        swing=swing,
                    )

                    if is_new:
                        generated += 1
                        if generated >= max_puzzles:
                            break
                    else:
                        skipped += 1

            except Exception as e:
                # Skip positions that fail to evaluate
                # (e.g., checkmate, stalemate, engine errors)
                continue

    return GenerationResult(
        generated=generated,
        skipped=skipped,
        analyzed_positions=analyzed_positions,
    )
