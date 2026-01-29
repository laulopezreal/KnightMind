"""
Stockfish engine wrapper module.

Provides position evaluation using Stockfish chess engine.
"""
import os
import subprocess
import shutil
from dataclasses import dataclass


# Configuration via environment variables
STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", "stockfish")
STOCKFISH_DEPTH = int(os.environ.get("STOCKFISH_DEPTH", "12"))
STOCKFISH_MOVETIME_MS = int(os.environ.get("STOCKFISH_MOVETIME_MS", "0"))  # 0 = use depth instead


@dataclass
class EvalResult:
    """Result of a position evaluation."""
    best_move_uci: str
    eval: float  # in pawns, from side-to-move perspective


class EngineNotAvailableError(Exception):
    """Raised when Stockfish is not available."""
    pass


# Aliases for backward compatibility
class StockfishNotFoundError(EngineNotAvailableError):
    """Alias for EngineNotAvailableError."""
    pass


class StockfishError(EngineNotAvailableError):
    """Alias for EngineNotAvailableError."""
    pass


class InvalidFenError(Exception):
    """Raised when the provided FEN is invalid."""
    pass


def get_stockfish_path() -> str | None:
    """
    Find the Stockfish executable.
    
    Returns the path if found, None otherwise.
    """
    # First check if STOCKFISH_PATH env var points to a valid executable
    if STOCKFISH_PATH != "stockfish":
        if os.path.isfile(STOCKFISH_PATH) and os.access(STOCKFISH_PATH, os.X_OK):
            return STOCKFISH_PATH
        return None
    
    # Otherwise look for stockfish in PATH
    path = shutil.which("stockfish")
    return path


def is_engine_available() -> tuple[bool, str]:
    """
    Check if Stockfish is available.
    
    Returns:
        Tuple of (available, message)
    """
    path = get_stockfish_path()
    if not path:
        return False, "Stockfish not available. Check STOCKFISH_PATH or install Stockfish."
    
    try:
        # Quick test to verify it works
        result = subprocess.run(
            [path],
            input="uci\nquit\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "uciok" in result.stdout:
            return True, "Stockfish is ready"
        return False, "Stockfish did not respond correctly"
    except subprocess.TimeoutExpired:
        return False, "Stockfish timed out"
    except Exception as e:
        return False, f"Stockfish error: {str(e)}"


def evaluate_position(fen: str) -> EvalResult:
    """
    Evaluate a chess position using Stockfish.
    
    Args:
        fen: Position in FEN notation
        
    Returns:
        EvalResult with best move and evaluation
        
    Raises:
        EngineNotAvailableError: If Stockfish is not available
        InvalidFenError: If the FEN is invalid
    """
    path = get_stockfish_path()
    if not path:
        raise EngineNotAvailableError("Stockfish not available. Check STOCKFISH_PATH or install Stockfish.")
    
    # Build UCI commands
    commands = [
        "uci",
        f"position fen {fen}",
    ]
    
    # Use movetime if specified, otherwise use depth
    if STOCKFISH_MOVETIME_MS > 0:
        commands.append(f"go movetime {STOCKFISH_MOVETIME_MS}")
    else:
        commands.append(f"go depth {STOCKFISH_DEPTH}")
    
    commands.append("quit")
    
    try:
        result = subprocess.run(
            [path],
            input="\n".join(commands) + "\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise EngineNotAvailableError("Stockfish timed out during evaluation")
    except Exception as e:
        raise EngineNotAvailableError(f"Stockfish error: {str(e)}")
    
    output = result.stdout
    
    # Check for invalid FEN
    if "error" in output.lower() or "illegal" in output.lower():
        raise InvalidFenError(f"Invalid FEN: {fen}")
    
    # Parse best move
    best_move = None
    eval_score = 0.0
    
    for line in output.split("\n"):
        # Get the final bestmove line
        if line.startswith("bestmove"):
            parts = line.split()
            if len(parts) >= 2:
                best_move = parts[1]
        
        # Get evaluation from info lines (last one with score)
        if "info" in line and "score" in line:
            parts = line.split()
            try:
                score_idx = parts.index("score")
                score_type = parts[score_idx + 1]
                score_value = int(parts[score_idx + 2])
                
                if score_type == "cp":
                    # Centipawns to pawns
                    eval_score = score_value / 100.0
                elif score_type == "mate":
                    # Mate score - use large value with sign
                    eval_score = 100.0 if score_value > 0 else -100.0
            except (ValueError, IndexError):
                pass
    
    if not best_move:
        raise EngineNotAvailableError("Stockfish did not return a best move")
    
    return EvalResult(best_move_uci=best_move, eval=eval_score)


# Aliases for backward compatibility with main.py imports
def is_stockfish_available() -> tuple[bool, str]:
    """Alias for is_engine_available."""
    return is_engine_available()


def evaluate_fen(fen: str) -> EvalResult:
    """Alias for evaluate_position."""
    return evaluate_position(fen)
