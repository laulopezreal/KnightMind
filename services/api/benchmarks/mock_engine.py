"""Deterministic Stockfish stub for reproducible benchmarks.

The real engine is a subprocess whose latency depends on the host, the binary
version, and search depth -- none of which we want in a repeatable benchmark.
Instead we mock every engine entry point with a pure function of the FEN, so the
same position always yields the same evaluation and the timing measures only our
own Python/DB code, never Stockfish itself.

Determinism contract:
    * ``eval`` for a FEN is a stable pseudo-value derived from ``crc32(fen)``.
    * The confirmation pass re-evaluates the SAME FENs, so it returns the SAME
      evals -> a shallow blunder always survives confirmation (validity 1.0).
    * ``get_top_moves`` returns a clear best move plus a strictly worse
      alternative, so the generator's uniqueness margin always passes.

This intentionally does NOT reproduce real engine accuracy; it reproduces the
*shape* of the engine's outputs so the surrounding code paths run to completion.
"""

import zlib
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from services.api.engine import EvalResult, MoveEval

# A stable "best move" for every mocked position. The generator never plays this
# move on the board (it only stores it and checks set membership), so a constant
# is sufficient and keeps the stub free of legal-move generation overhead.
_STUB_BEST_MOVE = "e2e4"
_STUB_SECOND_MOVE = "d2d4"

# Gap (in pawns) between the best and second move returned by get_top_moves.
# Must exceed the generator's PUZZLE_UNIQUENESS_MARGIN (default 0.5) so every
# confirmed candidate is treated as a real, unique puzzle.
_UNIQUENESS_GAP = 1.0


def eval_for_fen(fen: str, lo: float = -3.0, hi: float = 3.0) -> float:
    """Deterministic pseudo-evaluation (pawns) for a FEN.

    Uses crc32 so the value is stable across processes and Python versions
    (unlike ``hash()``, which is salted per-process). Rounded to 2 decimals to
    mimic centipawn resolution.
    """
    digest = zlib.crc32(fen.encode("utf-8")) & 0xFFFFFFFF
    frac = digest / 0xFFFFFFFF
    return round(lo + frac * (hi - lo), 2)


def fake_evaluate_fen(fen, engine=None, depth=None) -> EvalResult:
    """Stand-in for ``engine.stockfish.evaluate_fen`` (the cache-miss compute)."""
    return EvalResult(best_move_uci=_STUB_BEST_MOVE, eval=eval_for_fen(fen))


def fake_get_or_compute_eval(fen, engine=None, cache_stats=None, depth=None):
    """Stand-in for the generator's ``get_or_compute_eval`` (no DB, no cache).

    Records a miss in ``cache_stats`` so the generator's cache accounting still
    produces sensible numbers.
    """
    if cache_stats is not None:
        cache_stats["misses"] = cache_stats.get("misses", 0) + 1
    return EvalResult(best_move_uci=_STUB_BEST_MOVE, eval=eval_for_fen(fen))


def fake_get_top_moves(fen, engine=None, k=3, depth=None):
    """Stand-in for multi-PV. Best move first, then a strictly worse move."""
    base = eval_for_fen(fen)
    moves = [
        MoveEval(uci=_STUB_BEST_MOVE, eval=base),
        MoveEval(uci=_STUB_SECOND_MOVE, eval=round(base - _UNIQUENESS_GAP, 2)),
    ]
    return moves[:k]


class _DummyEngine:
    """A do-nothing object standing in for a live Stockfish engine instance.

    The generator passes this around and eventually calls ``close_engine`` on it;
    every attribute access returns another no-op so nothing raises.
    """

    def __getattr__(self, _name):  # pragma: no cover - defensive no-op
        return lambda *a, **k: None


def fake_create_engine():
    """Stand-in for ``create_engine`` -- returns a dummy, spawns no subprocess."""
    return _DummyEngine()


@contextmanager
def mock_generator_engine():
    """Patch every engine entry point the puzzle generator uses.

    Patches ``create_engine``, ``get_or_compute_eval``, ``get_top_moves`` and
    ``close_engine`` in ``services.api.puzzles.generator`` so a full generation
    run executes with zero real engine work.
    """
    mod = "services.api.puzzles.generator"
    with ExitStack() as stack:
        stack.enter_context(patch(f"{mod}.create_engine", fake_create_engine))
        stack.enter_context(patch(f"{mod}.close_engine", lambda *a, **k: None))
        stack.enter_context(
            patch(f"{mod}.get_or_compute_eval", fake_get_or_compute_eval)
        )
        stack.enter_context(patch(f"{mod}.get_top_moves", fake_get_top_moves))
        yield


@contextmanager
def mock_engine_cache_compute():
    """Patch the cache path's compute + version lookup for the cache benchmark.

    ``get_or_compute_eval`` (the REAL one) is exercised here -- we only mock the
    underlying ``evaluate_fen`` (so a miss "computes" instantly) and
    ``get_engine_version`` (so no probe subprocess is spawned). This lets the
    benchmark measure the genuine DB cache read/write cost of a hit vs a miss.
    """
    mod = "services.api.engine.stockfish"
    with ExitStack() as stack:
        stack.enter_context(patch(f"{mod}.evaluate_fen", fake_evaluate_fen))
        stack.enter_context(
            patch(f"{mod}.get_engine_version", lambda engine=None: "bench-sf")
        )
        yield
