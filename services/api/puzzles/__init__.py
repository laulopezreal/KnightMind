"""
Puzzle generation module.

Generates chess puzzles from user blunders by analyzing games with Stockfish.
"""

from .generator import GenerationResult, generate_puzzles

__all__ = ["generate_puzzles", "GenerationResult"]
