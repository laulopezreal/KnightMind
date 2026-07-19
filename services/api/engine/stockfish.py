"""
Stockfish engine wrapper for position evaluation.

Provides a simple interface to evaluate chess positions using Stockfish,
returning the best move and evaluation in pawns from the side-to-move perspective.
"""

import hashlib
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import chess

try:
    from stockfish import Stockfish as StockfishEngine
except ImportError:
    StockfishEngine = None

from sqlalchemy.exc import IntegrityError

from services.api.db import SessionLocal
from services.api.models import FenEvalCache

logger = logging.getLogger(__name__)


class StockfishNotFoundError(Exception):
    """Raised when Stockfish binary is not available."""

    pass


class StockfishError(Exception):
    """Raised when Stockfish encounters an error during evaluation."""

    pass


class StockfishEngineDeadError(StockfishError):
    """
    Raised when an evaluation timed out and its engine subprocess was killed.

    Subclasses StockfishError so existing broad handlers still catch it, but
    lets a caller that owns a long-lived engine (e.g. the puzzle-generator
    batch) detect that the shared subprocess is now dead and recreate it
    instead of running every subsequent eval against a corpse.
    """

    pass


@dataclass
class EvalResult:
    """Result of a position evaluation.

    ``eval`` is a centipawn-derived pawn score from the side-to-move
    perspective. Mate scores are *not* representable as centipawns, so they are
    clamped to the ``MATE_EVALUATION`` sentinel in ``eval`` **and** carried
    distinctly in ``mate_in`` so callers can tell "mate in 1" from "mate in 8"
    and from a merely large centipawn advantage.

    ``mate_in`` semantics (from the side-to-move perspective):
        - positive N  -> side to move delivers mate in N
        - negative N  -> side to move gets mated in N
        - 0           -> side to move is already checkmated (terminal)
        - None        -> not a forced mate (ordinary centipawn eval)

    ``is_terminal`` is True when the position itself is game-over (checkmate,
    stalemate, or another draw-by-rule); in that case ``best_move_uci`` is None
    because there is no move to make.
    """

    best_move_uci: Optional[str]
    eval: float  # In pawns, from side-to-move perspective
    mate_in: Optional[int] = None
    is_terminal: bool = False


@dataclass
class MoveEval:
    """A single candidate move with its evaluation (for multi-PV analysis)."""

    uci: str
    eval: float  # In pawns, from side-to-move perspective
    mate_in: Optional[int] = None


def get_stockfish_path() -> str:
    """Get Stockfish binary path from environment or default."""
    return os.environ.get("STOCKFISH_PATH", "stockfish")


def get_analysis_params() -> dict:
    """Get analysis parameters from environment in UCI format."""
    # This should return ENGINE OPTIONS like Hash, Threads, etc.
    # "Depth" is a search limit, not an option.
    return {}


def get_search_depth() -> int:
    """Get search depth from environment."""
    depth = os.environ.get("STOCKFISH_DEPTH")
    if depth:
        return int(depth)
    return 12


def create_engine() -> "StockfishEngine":
    """Create and configure a Stockfish engine instance."""
    if StockfishEngine is None:
        raise StockfishNotFoundError(
            "The 'stockfish' Python package is not installed. "
            "Install it with: pip install stockfish"
        )

    path = get_stockfish_path()

    try:
        engine = StockfishEngine(path=path)
    except Exception as e:
        error_msg = str(e).lower()
        if (
            "no such file" in error_msg
            or "not found" in error_msg
            or "permission" in error_msg
        ):
            raise StockfishNotFoundError(
                f"Stockfish binary not found at '{path}'. "
                "Please install Stockfish and ensure it's in your PATH, "
                "or set STOCKFISH_PATH environment variable. "
                "On macOS: brew install stockfish. "
                "On Ubuntu: apt install stockfish."
            ) from e
        raise StockfishError(f"Failed to initialize Stockfish: {e}") from e

    return engine


def _get_env_positive(name: str, default: float) -> float:
    """Read a positive numeric env var, falling back to ``default`` on error."""
    raw = os.environ.get(name)
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            logger.warning("Invalid %s=%r, using default %s", name, raw, default)
    return default


def _get_max_concurrency() -> int:
    """Max number of concurrent Stockfish evaluations allowed in this process."""
    return int(_get_env_positive("STOCKFISH_MAX_CONCURRENCY", 2))


def _get_eval_timeout_s() -> float:
    """Per-evaluation wall-clock timeout in seconds."""
    return _get_env_positive("STOCKFISH_EVAL_TIMEOUT_S", 30.0)


def _get_acquire_timeout_s() -> float:
    """How long to wait for a concurrency slot before rejecting the request."""
    return _get_env_positive("STOCKFISH_ACQUIRE_TIMEOUT_S", 60.0)


# Bounds how many engine evaluations may run at once, so a caller cannot spawn
# unbounded Stockfish work. Shared by every path that goes through evaluate_fen
# (the /engine/eval endpoint and the puzzle generator batch).
_EVAL_SEMAPHORE = threading.BoundedSemaphore(_get_max_concurrency())

# Dedicated pool used only to enforce the per-eval timeout. Concurrency is
# bounded by _EVAL_SEMAPHORE before we ever submit here; the extra headroom
# lets a wedged worker linger without blocking healthy evaluations.
_EVAL_EXECUTOR = ThreadPoolExecutor(
    max_workers=_get_max_concurrency() + 2, thread_name_prefix="sf-eval"
)


def _kill_engine_process(engine: Optional["StockfishEngine"]) -> None:
    """Best-effort hard kill of the underlying Stockfish subprocess."""
    if engine is None:
        return
    proc = getattr(engine, "_stockfish", None)
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            logger.debug("Failed to kill Stockfish subprocess", exc_info=True)


def close_engine(engine: Optional["StockfishEngine"]) -> None:
    """
    Terminate a Stockfish engine subprocess, releasing its OS process.

    stockfish>=4.x exposes ``send_quit_command()``, which sends ``quit`` and
    waits for the subprocess to exit. We guard it so a partially constructed or
    already-dead engine cannot raise, and fall back to killing the underlying
    subprocess directly if the quit command is unavailable or fails.
    """
    if engine is None:
        return
    quit_cmd = getattr(engine, "send_quit_command", None)
    if callable(quit_cmd):
        try:
            quit_cmd()
            return
        except Exception:
            logger.debug("send_quit_command failed during teardown", exc_info=True)
    _kill_engine_process(engine)


def _run_with_timeout(
    func: Callable[[], EvalResult],
    timeout_s: float,
    engine: "StockfishEngine",
) -> EvalResult:
    """
    Run ``func`` with a wall-clock timeout so a wedged engine call cannot block
    forever. On timeout the underlying subprocess is killed (which unblocks the
    worker thread) and a StockfishEngineDeadError is raised so the caller knows
    the engine is dead.

    Note: future.result(timeout=...) cannot cancel an already-running task, so
    the worker thread only unwinds once the killed subprocess makes its pending
    read return. If proc.kill() ever fails to unblock the read, that worker can
    linger; _EVAL_EXECUTOR is sized with headroom above the concurrency bound so
    a stray lingering worker does not starve healthy evaluations.
    """
    future = _EVAL_EXECUTOR.submit(func)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeoutError as e:
        _kill_engine_process(engine)
        raise StockfishEngineDeadError(
            f"Engine evaluation timed out after {timeout_s}s"
        ) from e


def evaluate_fen(fen: str, engine: Optional["StockfishEngine"] = None) -> EvalResult:
    """
    Evaluate a chess position given its FEN string.

    Args:
        fen: FEN string representing the position to evaluate
        engine: Optional existing Stockfish engine instance to reuse

    Returns:
        EvalResult with best_move_uci and eval (in pawns, from side-to-move POV)

    Raises:
        StockfishNotFoundError: If Stockfish is not available
        StockfishError: If evaluation fails
    """
    # Bound concurrency BEFORE creating an engine so a flood of callers cannot
    # spawn unbounded Stockfish subprocesses. Reject rather than queue forever.
    if not _EVAL_SEMAPHORE.acquire(timeout=_get_acquire_timeout_s()):
        raise StockfishError(
            "Engine evaluation concurrency limit reached; try again later"
        )

    try:
        owns_engine = engine is None
        if owns_engine:
            engine = create_engine()

        try:
            return _run_with_timeout(
                lambda: _evaluate_fen_core(fen, engine),
                _get_eval_timeout_s(),
                engine,
            )
        finally:
            # Only tear down engines we created. If one was passed in, the
            # caller owns its lifecycle (e.g. the puzzle generator batch).
            if owns_engine:
                close_engine(engine)
    finally:
        _EVAL_SEMAPHORE.release()


def _evaluate_fen_core(fen: str, engine: "StockfishEngine") -> EvalResult:
    """Run the actual Stockfish evaluation. Assumes ``engine`` is ready."""
    try:
        # Parse and validate the FEN with python-chess first. This also lets us
        # detect terminal positions (checkmate / stalemate / draw-by-rule)
        # explicitly rather than inferring them from a falsy get_best_move().
        # Order matters: the stockfish package's is_fen_valid() rejects some
        # terminal positions (e.g. checkmate), so it must run *after* the
        # terminal check or the biggest blunders get misreported as invalid.
        try:
            board = chess.Board(fen)
        except ValueError as e:
            raise StockfishError(f"Invalid FEN: {fen}") from e

        if board.is_game_over():
            return _terminal_eval_result(board)

        # Non-terminal: let the engine do its own structural validation too.
        if not engine.is_fen_valid(fen):
            raise StockfishError(f"Invalid FEN: {fen}")

        engine.set_fen_position(fen)

        # Get analysis parameters and configure engine
        params = get_analysis_params()
        if params:
            engine.update_engine_parameters(params)

        # Set search limits
        depth = get_search_depth()
        engine.set_depth(depth)

        # Get best move
        best_move = engine.get_best_move()
        if not best_move:
            # Non-terminal position with no move means the engine itself
            # failed -- surface it rather than pretending the game is over.
            raise StockfishError("Engine returned no move for a non-terminal position")

        # Get evaluation
        evaluation = engine.get_evaluation()

        # Convert evaluation to pawns from side-to-move perspective
        eval_pawns, mate_in = _convert_evaluation(evaluation)

        return EvalResult(best_move_uci=best_move, eval=eval_pawns, mate_in=mate_in)

    except (StockfishNotFoundError, StockfishError):
        raise
    except Exception as e:
        raise StockfishError(f"Evaluation failed: {e}") from e


# Arbitrary large value (in pawns) representing a decisive advantage (mate).
# Distance-to-mate is preserved separately via EvalResult.mate_in.
MATE_EVALUATION = 100.0


def _convert_evaluation(evaluation: dict) -> tuple[float, Optional[int]]:
    """
    Convert a Stockfish evaluation to (pawns, mate_in).

    Stockfish returns evaluation as either:
    - {"type": "cp", "value": centipawns} for normal positions
    - {"type": "mate", "value": moves_to_mate} for mate positions

    Returns:
        (eval_pawns, mate_in) from the side-to-move perspective, where
        eval_pawns is the ``MATE_EVALUATION`` sentinel for mates and mate_in is
        the signed distance to mate (None for ordinary evals). This keeps the
        centipawn channel backward-compatible while exposing mate distance so
        callers no longer conflate "mate in 1" with "mate in 20".
    """
    eval_type = evaluation.get("type")
    value = evaluation.get("value", 0)

    if eval_type == "cp":
        return value / 100.0, None
    elif eval_type == "mate":
        mate_in = int(value)
        if mate_in > 0:
            return MATE_EVALUATION, mate_in  # Side to move mates
        elif mate_in < 0:
            return -MATE_EVALUATION, mate_in  # Side to move gets mated
        else:
            # value == 0 -> side to move is already checkmated
            return -MATE_EVALUATION, 0
    else:
        return 0.0, None


def _convert_eval_to_pawns(evaluation: dict) -> float:
    """Backward-compatible pawn-only view of :func:`_convert_evaluation`."""
    return _convert_evaluation(evaluation)[0]


def _terminal_eval_result(board: chess.Board) -> EvalResult:
    """
    Build an :class:`EvalResult` for a game-over position.

    Checkmate is scored as a loss for the side to move (they have been mated);
    stalemate and other draws-by-rule are scored as equal. There is no best
    move in a terminal position, so ``best_move_uci`` is None.
    """
    if board.is_checkmate():
        return EvalResult(
            best_move_uci=None, eval=-MATE_EVALUATION, mate_in=0, is_terminal=True
        )
    # Stalemate, insufficient material, 75-move / fivefold, etc.
    return EvalResult(best_move_uci=None, eval=0.0, mate_in=None, is_terminal=True)


def get_top_moves(fen: str, engine: "StockfishEngine", k: int = 3) -> list["MoveEval"]:
    """
    Return the engine's top-K candidate moves (multi-PV) for a position.

    Used to build an acceptance set of near-equivalent best moves so a puzzle
    solver is not marked wrong for choosing a move that is just as good as the
    single stored best move.

    Args:
        fen: FEN string to analyze (must be a non-terminal position).
        engine: An initialized Stockfish engine instance.
        k: Number of principal variations to request.

    Returns:
        A list of MoveEval sorted best-first (as returned by Stockfish), each
        with eval in pawns and mate distance from the side-to-move perspective.
        Empty if the engine returns nothing (e.g. terminal position).
    """
    engine.set_fen_position(fen)
    engine.set_depth(get_search_depth())

    raw = engine.get_top_moves(k) or []
    results: list[MoveEval] = []
    for entry in raw:
        uci = entry.get("Move")
        if not uci:
            continue
        mate = entry.get("Mate")
        if mate is not None:
            mate_in = int(mate)
            ev = MATE_EVALUATION if mate_in > 0 else -MATE_EVALUATION
            results.append(MoveEval(uci=uci, eval=ev, mate_in=mate_in))
        else:
            cp = entry.get("Centipawn") or 0
            results.append(MoveEval(uci=uci, eval=cp / 100.0, mate_in=None))
    return results


def is_stockfish_available() -> bool:
    """Check if Stockfish is available and working."""
    engine = None
    try:
        engine = create_engine()
        # Quick test
        engine.set_fen_position(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        )
        return True
    except (StockfishNotFoundError, StockfishError):
        return False
    finally:
        close_engine(engine)


def _compute_cache_key(
    fen: str, depth: int, movetime_ms: Optional[int], engine_name: str
) -> str:
    """
    Compute deterministic cache key for a FEN + engine settings.

    Args:
        fen: FEN string
        depth: Search depth
        movetime_ms: Move time in milliseconds (if used instead of depth)
        engine_name: Engine identifier

    Returns:
        SHA256 hash as hex string
    """
    # Build key components
    key_parts = [
        f"fen={fen}",
        f"depth={depth}",
        f"movetime={movetime_ms}" if movetime_ms else "",
        f"engine={engine_name}",
    ]
    key_string = "|".join(p for p in key_parts if p)

    # Hash for consistent key length
    return hashlib.sha256(key_string.encode()).hexdigest()


def get_or_compute_eval(
    fen: str,
    engine: Optional["StockfishEngine"] = None,
    cache_stats: Optional[dict] = None,
) -> EvalResult:
    """
    Get evaluation from cache or compute with Stockfish.

    This is the main entry point for all evaluations - it checks the cache first,
    and only calls Stockfish if there's a cache miss.

    Args:
        fen: FEN string to evaluate
        engine: Optional existing Stockfish engine instance
        cache_stats: Optional dict to track cache hits/misses (will be updated in-place)

    Returns:
        EvalResult with best_move_uci and eval

    Raises:
        StockfishNotFoundError: If Stockfish is not available
        StockfishError: If evaluation fails
    """
    # Get engine settings for cache key
    depth = get_search_depth()
    movetime_ms = None  # We're using depth-based search
    engine_name = get_stockfish_path()

    # Compute cache key
    cache_key = _compute_cache_key(fen, depth, movetime_ms, engine_name)

    # Try cache lookup
    try:
        with SessionLocal() as db:
            cached = db.get(FenEvalCache, cache_key)
            if cached:
                # Cache hit!
                if cache_stats is not None:
                    cache_stats["hits"] = cache_stats.get("hits", 0) + 1
                logger.debug(f"Cache hit for FEN: {fen[:50]}...")
                return EvalResult(
                    best_move_uci=cached.best_move_uci, eval=cached.eval_pawns
                )
    except Exception as e:
        # Cache lookup failed, log but continue to compute
        logger.warning(f"Cache lookup failed: {e}")

    # Cache miss - compute evaluation
    if cache_stats is not None:
        cache_stats["misses"] = cache_stats.get("misses", 0) + 1

    logger.debug(f"Cache miss for FEN: {fen[:50]}...")
    result = evaluate_fen(fen, engine=engine)

    # Terminal positions have no best move and are cheap to recompute; skip the
    # cache (best_move_uci is NOT NULL) and return them directly.
    if result.best_move_uci is None:
        return result

    # Store in cache
    try:
        with SessionLocal() as db:
            cache_entry = FenEvalCache(
                key=cache_key,
                fen=fen,
                best_move_uci=result.best_move_uci,
                eval_pawns=result.eval,
                depth=depth,
                movetime_ms=movetime_ms,
                engine_name=engine_name,
                engine_version=None,  # Could extract from engine if available
                created_at=datetime.now(timezone.utc),
            )
            db.add(cache_entry)
            try:
                db.commit()
            except IntegrityError as commit_error:
                # Handle concurrent insert (unique constraint violation)
                db.rollback()
                # Re-select to get the value inserted by another process
                cached = db.get(FenEvalCache, cache_key)
                if cached:
                    logger.debug(
                        "Concurrent insert detected, using existing cache entry"
                    )
                    return EvalResult(
                        best_move_uci=cached.best_move_uci, eval=cached.eval_pawns
                    )
                # If still not found, something else went wrong
                logger.error(
                    f"Failed to cache evaluation after integrity error: {commit_error}"
                )
    except Exception as e:
        # Cache insert failed, log but return the computed result
        logger.error(f"Failed to cache evaluation: {e}")

    return result
