"""
Puzzle generation module.

Generates chess puzzles from user blunders by analyzing games with Stockfish.
"""

__all__ = ["generate_puzzles", "GenerationResult", "GenerationStatus"]


def __getattr__(name: str):
    """Load generator exports lazily so identity imports stay cycle-free."""

    if name in __all__:
        from .generator import GenerationResult, GenerationStatus, generate_puzzles

        return {
            "generate_puzzles": generate_puzzles,
            "GenerationResult": GenerationResult,
            "GenerationStatus": GenerationStatus,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
