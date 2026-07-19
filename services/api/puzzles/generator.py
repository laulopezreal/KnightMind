"""
Puzzle generation logic.

Analyzes user games to find blunders and generate puzzles.
Uses bounded compute to work on Render and similar platforms.
"""

import io
import logging
import os
from dataclasses import dataclass

import chess
import chess.pgn

from services.api.db import SessionLocal
from services.api.engine import create_engine, get_or_compute_eval
from services.api.storage import GameRepository, PuzzleRepository

logger = logging.getLogger(__name__)


# How often (in plies) to heartbeat / check cancellation *within* a single
# game. The per-game check between games isn't enough on its own: one very deep
# game (or the initial bulk PGN load + first game) could otherwise outlast the
# crash-recovery lease and be falsely reset. Checking every N plies bounds the
# gap between heartbeats to N positions regardless of game length.
HEARTBEAT_PLY_INTERVAL = 10


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
    cache_hits: int = 0  # Number of cache hits
    cache_misses: int = 0  # Number of cache misses


def _is_user_move(
    board: chess.Board, username: str, white_username: str, black_username: str
) -> bool:
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
    cancellation_check: callable = None,
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
    with SessionLocal() as db:
        game_repository = GameRepository(db)
        puzzle_repository = PuzzleRepository(db)

        # Get user's games
        all_metadata = game_repository.get_all_metadata(username)
        if not all_metadata:
            raise ValueError(f"No games found for user '{username}'")

        # Take most recent games (already sorted by end_time descending)
        recent_games = all_metadata[:max_games]

        # Get configuration
        swing_threshold = get_swing_threshold()
        ply_start, ply_end = get_ply_range()

        # Create engine instance for the whole batch
        try:
            engine = create_engine()
        except Exception:
            # If engine creation fails, we can't generate anything
            # (logging would be good here)
            return GenerationResult(generated=0, skipped=0, analyzed_positions=0)

        # Bulk-load PGNs keyed by game_id (one query per 1000 ids instead of
        # one per game). A dict rather than iter_pgns because each PGN must
        # stay paired with its game_id for save_puzzle(source_game_id=...);
        # memory is bounded by max_games PGN blobs (capped at the endpoint
        # and worker). Fetched only after the engine is known to be usable.
        pgns_by_game_id = game_repository.get_pgns(
            username, [game.game_id for game in recent_games]
        )

        generated = 0
        skipped = 0
        analyzed_positions = 0
        cache_stats = {"hits": 0, "misses": 0}  # Track cache performance

        for game_meta in recent_games:
            # Check for cancellation before processing each game
            if cancellation_check and cancellation_check():
                logger.info(f"Puzzle generation canceled for {username}")
                break

            if generated >= max_puzzles:
                break

            # Load PGN (bulk-fetched above)
            pgn_text = pgns_by_game_id.get(game_meta.game_id)
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

                # Heartbeat + cancellation *within* the game so no single deep
                # game can exceed the crash-recovery lease. The per-game check
                # above only fires between games; this fires every
                # HEARTBEAT_PLY_INTERVAL plies. On cancel we break the inner
                # loop; the per-game check then ends the outer loop too.
                if (
                    cancellation_check
                    and ply % HEARTBEAT_PLY_INTERVAL == 0
                    and cancellation_check()
                ):
                    logger.info(f"Puzzle generation canceled for {username}")
                    break

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
                    # Evaluate position before the move (with cache)
                    eval_result_before = get_or_compute_eval(
                        fen_before, engine=engine, cache_stats=cache_stats
                    )
                    eval_before = eval_result_before.eval
                    best_move_uci = eval_result_before.best_move_uci

                    # Make the user's move
                    played_move_uci = move.uci()
                    board.push(move)

                    # Evaluate position after the move (with cache)
                    fen_after = board.fen()
                    eval_result_after = get_or_compute_eval(
                        fen_after, engine=engine, cache_stats=cache_stats
                    )
                    eval_after = eval_result_after.eval

                    # Calculate swing
                    # Important: eval is always from side-to-move perspective
                    # So we need to flip the sign for eval_after since it's from opponent's perspective
                    swing = eval_before - (-eval_after)

                    # Check if this is a blunder
                    if swing >= swing_threshold:
                        # Save puzzle
                        is_new, _ = puzzle_repository.save_puzzle(
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
                    logger.warning(
                        f"Failed to generate puzzle for FEN {fen_before}", exc_info=e
                    )
                    continue

        return GenerationResult(
            generated=generated,
            skipped=skipped,
            analyzed_positions=analyzed_positions,
            cache_hits=cache_stats["hits"],
            cache_misses=cache_stats["misses"],
        )
