"""
Stockfish engine wrapper for position evaluation.

Provides a simple interface to evaluate chess positions using Stockfish,
returning the best move and evaluation in pawns from the side-to-move perspective.

When STOCKFISH_SERVICE_URL is set, the API calls a separate Stockfish HTTP service
instead of spawning the binary locally.
"""

import os
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone

try:
    from stockfish import Stockfish as StockfishEngine
except ImportError:
    StockfishEngine = None

try:
    import httpx
except ImportError:
    httpx = None

from services.api.db import SessionLocal
from services.api.models import FenEvalCache

logger = logging.getLogger(__name__)


def _stockfish_service_url() -> Optional[str]:
    """Base URL of the Stockfish service (no trailing slash), or None to use local engine."""
    url = os.environ.get("STOCKFISH_SERVICE_URL", "").strip()
    return url or None


class StockfishNotFoundError(Exception):
    """Raised when Stockfish binary is not available."""
    pass


class StockfishError(Exception):
    """Raised when Stockfish encounters an error during evaluation."""
    pass


@dataclass
class EvalResult:
    """Result of a position evaluation."""
    best_move_uci: str
    eval: float  # In pawns, from side-to-move perspective


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
        if "no such file" in error_msg or "not found" in error_msg or "permission" in error_msg:
            raise StockfishNotFoundError(
                f"Stockfish binary not found at '{path}'. "
                "Please install Stockfish and ensure it's in your PATH, "
                "or set STOCKFISH_PATH environment variable. "
                "On macOS: brew install stockfish. "
                "On Ubuntu: apt install stockfish."
            )
        raise StockfishError(f"Failed to initialize Stockfish: {e}")
    
    return engine


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
    url = _stockfish_service_url()
    if url and httpx:
        try:
            r = httpx.post(
                f"{url.rstrip('/')}/eval",
                json={"fen": fen},
                timeout=60.0,
            )
            if r.status_code == 503:
                raise StockfishNotFoundError("Stockfish service not available")
            r.raise_for_status()
            data = r.json()
            return EvalResult(
                best_move_uci=data["best_move_uci"],
                eval=float(data["eval"]),
            )
        except StockfishNotFoundError:
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", "Invalid FEN")
                except Exception:
                    detail = "Invalid FEN"
                raise StockfishError(detail)
            raise StockfishError(f"Evaluation failed: {e}")
        except Exception as e:
            raise StockfishError(f"Evaluation failed: {e}")

    should_close_engine = False
    if engine is None:
        engine = create_engine()
        should_close_engine = True
    
    try:
        # Set position
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
            raise StockfishError("No legal moves available")
        
        # Get evaluation
        evaluation = engine.get_evaluation()
        
        # Convert evaluation to pawns from side-to-move perspective
        eval_pawns = _convert_eval_to_pawns(evaluation)
        
        return EvalResult(best_move_uci=best_move, eval=eval_pawns)
        
    except (StockfishNotFoundError, StockfishError):
        raise
    except Exception as e:
        raise StockfishError(f"Evaluation failed: {e}")
    finally:
        # Only close if we created it locally. 
        # Note: The stockfish library wrapper might not have a close/quit method exposed simply,
        # but generally for a local variable it's fine. 
        # If we passed it in, the caller is responsible.
        pass


# Arbitrary large value representing a decisive advantage (mate)
MATE_EVALUATION = 100.0


def _convert_eval_to_pawns(evaluation: dict) -> float:
    """
    Convert Stockfish evaluation to pawns.
    
    Stockfish returns evaluation as either:
    - {"type": "cp", "value": centipawns} for normal positions
    - {"type": "mate", "value": moves_to_mate} for mate positions
    
    Returns value in pawns from side-to-move perspective.
    """
    eval_type = evaluation.get("type")
    value = evaluation.get("value", 0)
    
    if eval_type == "cp":
        # Centipawns to pawns
        return value / 100.0
    elif eval_type == "mate":
        # Mate in N moves - use large value with sign
        # Positive = side to move is winning, negative = losing
        if value > 0:
            return MATE_EVALUATION  # Winning
        elif value < 0:
            return -MATE_EVALUATION  # Losing
        else:
            return 0.0
    else:
        return 0.0


def is_stockfish_available() -> bool:
    """Check if Stockfish is available and working (local binary or remote service)."""
    url = _stockfish_service_url()
    if url and httpx:
        try:
            r = httpx.get(f"{url.rstrip('/')}/status", timeout=5.0)
            if r.status_code == 200:
                data = r.json()
                return data.get("available", False)
            return False
        except Exception as e:
            logger.warning("Stockfish service check failed (is the service running on %s?): %s", url, e)
            return False
    try:
        engine = create_engine()
        engine.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        return True
    except (StockfishNotFoundError, StockfishError):
        return False


def _compute_cache_key(fen: str, depth: int, movetime_ms: Optional[int], engine_name: str) -> str:
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
        f"engine={engine_name}"
    ]
    key_string = "|".join(p for p in key_parts if p)
    
    # Hash for consistent key length
    return hashlib.sha256(key_string.encode()).hexdigest()


def get_or_compute_eval(
    fen: str, 
    engine: Optional["StockfishEngine"] = None,
    cache_stats: Optional[dict] = None
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
                    cache_stats['hits'] = cache_stats.get('hits', 0) + 1
                logger.debug(f"Cache hit for FEN: {fen[:50]}...")
                return EvalResult(
                    best_move_uci=cached.best_move_uci,
                    eval=cached.eval_pawns
                )
    except Exception as e:
        # Cache lookup failed, log but continue to compute
        logger.warning(f"Cache lookup failed: {e}")
    
    # Cache miss - compute evaluation
    if cache_stats is not None:
        cache_stats['misses'] = cache_stats.get('misses', 0) + 1
    
    logger.debug(f"Cache miss for FEN: {fen[:50]}...")
    result = evaluate_fen(fen, engine=engine)
    
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
                created_at=datetime.now(timezone.utc)
            )
            db.add(cache_entry)
            try:
                db.commit()
            except Exception as commit_error:
                # Handle concurrent insert (unique constraint violation)
                db.rollback()
                # Re-select to get the value inserted by another process
                cached = db.get(FenEvalCache, cache_key)
                if cached:
                    logger.debug(f"Concurrent insert detected, using existing cache entry")
                    return EvalResult(
                        best_move_uci=cached.best_move_uci,
                        eval=cached.eval_pawns
                    )
                # If still not found, something else went wrong
                logger.error(f"Failed to cache evaluation: {commit_error}")
    except Exception as e:
        # Cache insert failed, log but return the computed result
        logger.error(f"Failed to cache evaluation: {e}")
    
    return result
