"""Reproducible performance benchmarks for the KnightMind API.

This package provides a deterministic, self-contained benchmark harness for the
API's hot paths. Everything is driven by a fixed seed and a mocked Stockfish
engine (a deterministic fixed-eval stub), so results are repeatable and no real
engine binary, network, or production data is ever touched.

Entry points:
    python -m services.api.benchmarks --scale small
    python -m services.api.benchmarks --scale medium --out results.json

See ``docs/benchmarks.md`` for how to run, the fixture scales, and baselines.
"""

from services.api.benchmarks.runner import SCALES, run_all

__all__ = ["SCALES", "run_all"]
