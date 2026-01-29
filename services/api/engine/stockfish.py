"""
Stockfish engine wrapper for position evaluation.

Provides a simple interface to evaluate chess positions using Stockfish,
returning the best move and evaluation in pawns from the side-to-move perspective.
"""

import os
from dataclasses import dataclass

try:
    from stockfish import Stockfish as StockfishEngine
except ImportError:
    StockfishEngine = None


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
    """Get analysis parameters from environment."""
    depth = os.environ.get("STOCKFISH_DEPTH")
    movetime = os.environ.get("STOCKFISH_MOVETIME_MS")

    if depth:
        return {"depth": int(depth)}
    elif movetime:
        return {"movetime": int(movetime)}
    else:
        # Default: depth 12 for reasonable speed/quality tradeoff
        return {"depth": 12}


def _create_engine() -> "StockfishEngine":
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


def evaluate_fen(fen: str) -> EvalResult:
    """
    Evaluate a chess position given its FEN string.
    
    Args:
        fen: FEN string representing the position to evaluate
        
    Returns:
        EvalResult with best_move_uci and eval (in pawns, from side-to-move POV)
        
    Raises:
        StockfishNotFoundError: If Stockfish is not available
        StockfishError: If evaluation fails
    """
    engine = _create_engine()

    try:
        # Set position
        if not engine.is_fen_valid(fen):
            raise StockfishError(f"Invalid FEN: {fen}")

        engine.set_fen_position(fen)

        # Get analysis parameters
        params = get_analysis_params()

        # Get best move
        best_move = engine.get_best_move(**params)
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
    """Check if Stockfish is available and working."""
    try:
        engine = _create_engine()
        # Quick test
        engine.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        return True
    except (StockfishNotFoundError, StockfishError):
        return False
