"""
Puzzle generation logic.

Analyzes user games to find blunders and generate puzzles.
Uses bounded compute to work on Render and similar platforms.
"""

import io
import logging
import os
from dataclasses import dataclass
from enum import Enum

import chess
import chess.pgn

from services.api.db import SessionLocal
from services.api.engine import (
    MATE_EVALUATION,
    StockfishEngineDeadError,
    StockfishError,
    StockfishNotFoundError,
    close_engine,
    create_engine,
    get_or_compute_eval,
    get_top_moves,
)
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


def get_equiv_tolerance() -> float:
    """Max eval gap (in pawns) for a move to count as equivalent to the best.

    Moves within this tolerance of the top move form the puzzle's acceptance
    set, so a solver is not marked wrong for a genuinely equal alternative.
    """
    return float(os.environ.get("PUZZLE_EQUIV_TOLERANCE", "0.3"))


def get_ply_range() -> tuple[int, int]:
    """Get the range of plies to analyze."""
    start = int(os.environ.get("MAX_PLIES_START", "8"))
    end = int(os.environ.get("MAX_PLIES_END", "80"))
    return start, end


class GenerationStatus(str, Enum):
    """Outcome of a puzzle-generation run.

    Distinguishes conditions that previously all collapsed to "0 puzzles":
        - SUCCESS: engine ran and produced puzzles.
        - NO_MISTAKES: engine ran fine, no qualifying blunders were found.
        - ENGINE_UNAVAILABLE: the engine could not be created at all.
        - ALL_FAILED: every analyzed position failed to evaluate.
        - PARTIAL: some puzzles produced, but some positions failed.
    """

    SUCCESS = "success"
    NO_MISTAKES = "no_mistakes"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    ALL_FAILED = "all_failed"
    PARTIAL = "partial"


@dataclass
class GenerationResult:
    """Result of puzzle generation."""

    generated: int  # Number of new puzzles created
    skipped: int  # Number of duplicates skipped
    analyzed_positions: int  # Total positions analyzed
    cache_hits: int = 0  # Number of cache hits
    cache_misses: int = 0  # Number of cache misses
    failed_positions: int = 0  # Positions that raised during evaluation
    # Machine-readable outcome so callers can tell engine-unavailable /
    # all-failed / no-mistakes / partial / success apart. Stored as a plain
    # string (via GenerationStatus.value) so it is JSON-serializable.
    status: str = GenerationStatus.SUCCESS.value

    @staticmethod
    def engine_unavailable() -> "GenerationResult":
        """A run that could not start because the engine was unavailable."""
        return GenerationResult(
            generated=0,
            skipped=0,
            analyzed_positions=0,
            status=GenerationStatus.ENGINE_UNAVAILABLE.value,
        )


def _terminal_eval_after(board: chess.Board) -> tuple[float, int | None]:
    """
    Evaluate a position that is game-over *after* the user's move, from the
    side-to-move (opponent) perspective, without invoking the engine.

    Returns (eval_pawns, mate_in). Checkmate means the user just delivered mate
    (opponent is mated -> -MATE for the opponent); stalemate / draw -> 0.0.
    This lets us capture "stalemated a won game" and "walked into mate"
    blunders that used to be dropped when the engine raised on a terminal FEN.
    """
    if board.is_checkmate():
        return -MATE_EVALUATION, 0
    return 0.0, None


def _build_accept_set(fen: str, engine, best_move_uci: str) -> list[str]:
    """
    Build the acceptance set of near-equivalent best moves for a position.

    Uses multi-PV so a solver who plays a move that is just as good as the
    stored best move is not marked wrong. Degrades gracefully to just the
    single best move if multi-PV is unavailable.
    """
    accept = [best_move_uci]
    try:
        top = get_top_moves(fen, engine=engine, k=3)
    except Exception as e:
        # Acceptance-set enrichment is best-effort: any multi-PV failure must
        # degrade to the single best move, never break generation.
        logger.debug("Multi-PV unavailable for %s: %s", fen[:40], e)
        return accept
    if not top:
        return accept

    tolerance = get_equiv_tolerance()
    best_eval = top[0].eval
    for candidate in top:
        if best_eval - candidate.eval <= tolerance and candidate.uci not in accept:
            accept.append(candidate.uci)
    return accept


def _recreate_batch_engine():
    """
    Recreate the shared batch engine after it died mid-batch (an eval timed out
    and its subprocess was killed). Returns a fresh engine, or None if it could
    not be recreated (in which case the caller should abort remaining work).
    """
    logger.warning(
        "Stockfish engine died mid-batch (timeout); recreating before continuing"
    )
    try:
        return create_engine()
    except Exception:
        logger.error(
            "Failed to recreate Stockfish engine; aborting remaining puzzle generation"
        )
        return None


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
        except Exception as e:
            # Engine could not be created. This is NOT the same as "found no
            # blunders": surface a distinct status so the job result does not
            # masquerade as a successful zero-puzzle run.
            logger.error("Engine unavailable, cannot generate puzzles: %s", e)
            return GenerationResult.engine_unavailable()

        # Ensure the batch engine subprocess is always terminated, even on
        # early return, exception, or cancellation.
        try:
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
            failed_positions = 0
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

                    # Heartbeat + cancellation *within* the game so no single
                    # deep game can exceed the crash-recovery lease. The
                    # per-game check above only fires between games; this fires
                    # every HEARTBEAT_PLY_INTERVAL plies. On cancel we break the
                    # inner loop; the per-game check then ends the outer loop too.
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
                    if not _is_user_move(
                        board, username, white_username, black_username
                    ):
                        board.push(move)
                        continue

                    analyzed_positions += 1

                    # Get FEN before the move
                    fen_before = board.fen()
                    side_to_move = "white" if board.turn == chess.WHITE else "black"
                    played_move_uci = move.uci()

                    # Evaluate position before the move (with cache).
                    # fen_before is never terminal -- the user has a move to make.
                    # A dead engine (eval timeout) is recreated and the position
                    # skipped; a genuine engine failure is counted. In both cases
                    # the board is advanced so the rest of the game stays in sync
                    # -- we never swallow a failure into a fake success.
                    try:
                        eval_result_before = get_or_compute_eval(
                            fen_before, engine=engine, cache_stats=cache_stats
                        )
                    except StockfishEngineDeadError:
                        # The shared batch engine is dead; recreate before
                        # continuing, else every remaining position runs against
                        # a corpse (each stalling until the timeout fires again).
                        engine = _recreate_batch_engine()
                        if engine is None:
                            break
                        board.push(move)
                        continue
                    except (StockfishError, StockfishNotFoundError) as e:
                        failed_positions += 1
                        logger.warning(
                            f"Failed to evaluate FEN {fen_before}", exc_info=e
                        )
                        board.push(move)
                        continue

                    eval_before = eval_result_before.eval
                    best_move_uci = eval_result_before.best_move_uci

                    # Make the user's move (board now advanced for the rest of
                    # the game).
                    board.push(move)
                    fen_after = board.fen()

                    # Evaluate position after the move. If the move ends the game
                    # (checkmate delivered, or a won game thrown away by
                    # stalemate), derive the eval directly from the board rather
                    # than asking the engine -- the engine raises on terminal
                    # FENs and those errors used to be swallowed, silently
                    # dropping the biggest blunders.
                    try:
                        if board.is_game_over():
                            eval_after, _ = _terminal_eval_after(board)
                        else:
                            eval_after = get_or_compute_eval(
                                fen_after, engine=engine, cache_stats=cache_stats
                            ).eval
                    except StockfishEngineDeadError:
                        # Board is already advanced; recreate and move on.
                        engine = _recreate_batch_engine()
                        if engine is None:
                            break
                        continue
                    except (StockfishError, StockfishNotFoundError) as e:
                        failed_positions += 1
                        logger.warning(
                            f"Failed to evaluate FEN {fen_after}", exc_info=e
                        )
                        continue

                    # Calculate swing.
                    # eval is always from side-to-move perspective, so flip the
                    # sign of eval_after (it is from the opponent's perspective).
                    swing = eval_before - (-eval_after)

                    # Check if this is a blunder
                    if swing >= swing_threshold:
                        # Build the acceptance set of equally-good moves so an
                        # alternative best move is not judged wrong at solve time.
                        accept_moves = _build_accept_set(
                            fen_before, engine, best_move_uci
                        )
                        is_new, _ = puzzle_repository.save_puzzle(
                            username=username,
                            source_game_id=game_meta.game_id,
                            ply=ply,
                            fen=fen_before,
                            side_to_move=side_to_move,
                            played_move_uci=played_move_uci,
                            best_move_uci=best_move_uci,
                            eval_before=eval_before,
                            eval_after=-eval_after,  # From original player's POV
                            swing=swing,
                            accept_moves_uci=",".join(accept_moves),
                        )

                        if is_new:
                            generated += 1
                            if generated >= max_puzzles:
                                break
                        else:
                            skipped += 1

            # Derive a machine-readable outcome from the counters.
            if analyzed_positions > 0 and failed_positions == analyzed_positions:
                status = GenerationStatus.ALL_FAILED
            elif failed_positions > 0 and generated > 0:
                status = GenerationStatus.PARTIAL
            elif generated > 0:
                status = GenerationStatus.SUCCESS
            else:
                status = GenerationStatus.NO_MISTAKES

            return GenerationResult(
                generated=generated,
                skipped=skipped,
                analyzed_positions=analyzed_positions,
                cache_hits=cache_stats["hits"],
                cache_misses=cache_stats["misses"],
                failed_positions=failed_positions,
                status=status.value,
            )
        finally:
            close_engine(engine)
