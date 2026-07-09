"""
Stockfish engine wrapper module.

Provides position evaluation using Stockfish chess engine.
"""

from .stockfish import (
    EvalResult,
    StockfishError,
    StockfishNotFoundError,
    create_engine,
    evaluate_fen,
    is_stockfish_available,
    get_or_compute_eval,
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
    "evaluate_position",
    "is_engine_available",
    "EngineNotAvailableError",
    "InvalidFenError",
    "EvalResult",
    "StockfishNotFoundError",
    "StockfishError",
    "evaluate_fen",
    "is_stockfish_available",
    "create_engine",
    "get_or_compute_eval",
]
