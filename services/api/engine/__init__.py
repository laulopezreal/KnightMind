"""
Stockfish engine wrapper module.

Provides position evaluation using Stockfish chess engine.
"""

from .stockfish import (
    MATE_EVALUATION,
    EvalResult,
    MoveEval,
    StockfishEngineDeadError,
    StockfishError,
    StockfishNotFoundError,
    close_engine,
    create_engine,
    evaluate_fen,
    get_or_compute_eval,
    get_top_moves,
    is_stockfish_available,
)

# Aliases for backward compatibility/main.py usage
evaluate_position = evaluate_fen
EngineNotAvailableError = StockfishNotFoundError
InvalidFenError = StockfishError


def is_engine_available() -> tuple[bool, str]:
    """
    Check if Stockfish is available.

    Returns:
        Tuple of (available, message)
    """
    if is_stockfish_available():
        return True, "Stockfish is ready"
    return False, "Stockfish not available. Check STOCKFISH_PATH or install Stockfish."


__all__ = [
    "MATE_EVALUATION",
    "evaluate_position",
    "is_engine_available",
    "EngineNotAvailableError",
    "InvalidFenError",
    "EvalResult",
    "MoveEval",
    "StockfishNotFoundError",
    "StockfishError",
    "StockfishEngineDeadError",
    "evaluate_fen",
    "is_stockfish_available",
    "create_engine",
    "close_engine",
    "get_or_compute_eval",
    "get_top_moves",
]
