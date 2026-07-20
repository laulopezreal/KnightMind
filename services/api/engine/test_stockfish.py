"""Tests for Stockfish engine wrapper."""

import os
import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import Base
from services.api.models import FenEvalCache

from . import stockfish as sf_module
from .stockfish import (
    EVAL_CONVERSION_VERSION,
    MATE_EVALUATION,
    UNKNOWN_ENGINE_VERSION,
    EvalResult,
    MoveEval,
    StockfishEngineDeadError,
    StockfishError,
    StockfishNotFoundError,
    _compute_cache_key,
    _convert_eval_to_pawns,
    _convert_evaluation,
    close_engine,
    evaluate_fen,
    get_analysis_params,
    get_engine_version,
    get_or_compute_eval,
    get_stockfish_path,
    get_top_moves,
    is_stockfish_available,
)


class TestConvertEvalToPawns:
    """Test evaluation conversion logic."""

    def test_centipawns_positive(self):
        """Positive centipawn evaluation."""
        result = _convert_eval_to_pawns({"type": "cp", "value": 150})
        assert result == 1.5

    def test_centipawns_negative(self):
        """Negative centipawn evaluation."""
        result = _convert_eval_to_pawns({"type": "cp", "value": -200})
        assert result == -2.0

    def test_centipawns_zero(self):
        """Equal position."""
        result = _convert_eval_to_pawns({"type": "cp", "value": 0})
        assert result == 0.0

    def test_mate_winning(self):
        """Mate in N (winning)."""
        result = _convert_eval_to_pawns({"type": "mate", "value": 5})
        assert result == MATE_EVALUATION

    def test_mate_losing(self):
        """Mate in N (losing)."""
        result = _convert_eval_to_pawns({"type": "mate", "value": -3})
        assert result == -MATE_EVALUATION

    def test_unknown_type(self):
        """Unknown evaluation type returns 0."""
        result = _convert_eval_to_pawns({"type": "unknown", "value": 100})
        assert result == 0.0


class TestMateDistanceSemantics:
    """Mate scores must preserve distance-to-mate, not just a clamped sentinel."""

    def test_mate_in_one_and_eight_are_distinguishable(self):
        """mate_in must differ for mate-in-1 vs mate-in-8 even though the pawn
        sentinel is identical (the pre-fix bug clamped both to +100)."""
        eval_1, mate_1 = _convert_evaluation({"type": "mate", "value": 1})
        eval_8, mate_8 = _convert_evaluation({"type": "mate", "value": 8})

        assert eval_1 == eval_8 == MATE_EVALUATION  # sentinel unchanged
        assert mate_1 == 1
        assert mate_8 == 8
        assert mate_1 != mate_8  # distance preserved

    def test_getting_mated_carries_negative_distance(self):
        eval_pawns, mate_in = _convert_evaluation({"type": "mate", "value": -3})
        assert eval_pawns == -MATE_EVALUATION
        assert mate_in == -3

    def test_cp_has_no_mate_distance(self):
        eval_pawns, mate_in = _convert_evaluation({"type": "cp", "value": 250})
        assert eval_pawns == 2.5
        assert mate_in is None


class TestTerminalPositions:
    """Terminal positions must be reported explicitly, not raised & swallowed."""

    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_checkmate_position_returns_terminal_result(self, mock_engine_class):
        """A checkmated side-to-move yields a distinct terminal EvalResult
        instead of raising StockfishError (which callers dropped)."""
        mock_engine = MagicMock()
        mock_engine.is_fen_valid.return_value = True
        # Fool's-mate final position, white (side to move) is checkmated.
        fen = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
        mock_engine_class.return_value = mock_engine

        result = evaluate_fen(fen)

        assert result.is_terminal is True
        assert result.best_move_uci is None
        assert result.eval == -MATE_EVALUATION
        assert result.mate_in == 0
        # Engine should never have been asked for a move on a terminal position.
        mock_engine.get_best_move.assert_not_called()

    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_stalemate_position_returns_draw_terminal_result(self, mock_engine_class):
        """A stalemated position is a draw (0.0), reported as terminal."""
        mock_engine = MagicMock()
        mock_engine.is_fen_valid.return_value = True
        # Black to move, stalemated (classic K+Q vs K stalemate).
        fen = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"
        mock_engine_class.return_value = mock_engine

        result = evaluate_fen(fen)

        assert result.is_terminal is True
        assert result.best_move_uci is None
        assert result.eval == 0.0
        mock_engine.get_best_move.assert_not_called()


class TestGetTopMoves:
    """Multi-PV acceptance-set support."""

    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_returns_ranked_move_evals(self, mock_engine_class):
        mock_engine = MagicMock()
        mock_engine.get_top_moves.return_value = [
            {"Move": "d2d4", "Centipawn": 30, "Mate": None},
            {"Move": "e2e4", "Centipawn": 25, "Mate": None},
            {"Move": "g1f3", "Centipawn": None, "Mate": 5},
        ]
        mock_engine_class.return_value = mock_engine

        moves = get_top_moves("startpos-fen", engine=mock_engine, k=3)

        assert [m.uci for m in moves] == ["d2d4", "e2e4", "g1f3"]
        assert moves[0] == MoveEval(uci="d2d4", eval=0.3, mate_in=None)
        assert moves[2].mate_in == 5
        assert moves[2].eval == MATE_EVALUATION


class TestGetStockfishPath:
    """Test path configuration."""

    def test_default_path(self):
        """Default path is 'stockfish'."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove STOCKFISH_PATH if set
            os.environ.pop("STOCKFISH_PATH", None)
            assert get_stockfish_path() == "stockfish"

    def test_custom_path(self):
        """Custom path from env var."""
        with patch.dict(os.environ, {"STOCKFISH_PATH": "/custom/stockfish"}):
            assert get_stockfish_path() == "/custom/stockfish"


class TestGetAnalysisParams:
    """Test analysis parameter configuration."""

    def test_default_depth(self):
        """Default is depth 12."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("STOCKFISH_DEPTH", None)
            os.environ.pop("STOCKFISH_MOVETIME_MS", None)
            assert get_analysis_params() == {}

    def test_custom_depth(self):
        """Custom depth from env var."""
        with patch.dict(os.environ, {"STOCKFISH_DEPTH": "15"}):
            assert get_analysis_params() == {}

    def test_movetime_when_depth_not_set(self):
        """Movetime from env var is used when depth is not set."""
        with patch.dict(os.environ, {"STOCKFISH_MOVETIME_MS": "500"}, clear=True):
            os.environ.pop("STOCKFISH_DEPTH", None)
            assert get_analysis_params() == {}

    def test_depth_has_precedence_over_movetime(self):
        """Depth from env var has precedence over movetime."""
        env_vars = {"STOCKFISH_DEPTH": "10", "STOCKFISH_MOVETIME_MS": "500"}
        with patch.dict(os.environ, env_vars):
            assert get_analysis_params() == {}


class TestEvaluateFen:
    """Test FEN evaluation with mocked Stockfish."""

    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_evaluate_starting_position(self, mock_engine_class):
        """Evaluate starting position."""
        mock_engine = MagicMock()
        mock_engine.is_fen_valid.return_value = True
        mock_engine.get_best_move.return_value = "e2e4"
        mock_engine.get_evaluation.return_value = {"type": "cp", "value": 20}
        mock_engine_class.return_value = mock_engine

        result = evaluate_fen(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        )

        assert isinstance(result, EvalResult)
        assert result.best_move_uci == "e2e4"
        assert result.eval == 0.2

    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_evaluate_invalid_fen(self, mock_engine_class):
        """Invalid FEN raises error."""
        mock_engine = MagicMock()
        mock_engine.is_fen_valid.return_value = False
        mock_engine_class.return_value = mock_engine

        with pytest.raises(StockfishError, match="Invalid FEN"):
            evaluate_fen("invalid fen string")

    @patch("services.api.engine.stockfish.StockfishEngine", None)
    def test_stockfish_package_not_installed(self):
        """Error when stockfish package not installed."""
        # Temporarily set StockfishEngine to None to simulate missing package
        import services.api.engine.stockfish as sf_module

        original = sf_module.StockfishEngine
        sf_module.StockfishEngine = None

        try:
            with pytest.raises(StockfishNotFoundError, match="not installed"):
                evaluate_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        finally:
            sf_module.StockfishEngine = original


class TestIsStockfishAvailable:
    """Test Stockfish availability check."""

    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_available(self, mock_engine_class):
        """Returns True when Stockfish works."""
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine

        assert is_stockfish_available() is True

    @patch("services.api.engine.stockfish.create_engine")
    def test_not_available(self, mock_create):
        """Returns False when Stockfish not found."""
        mock_create.side_effect = StockfishNotFoundError("Not found")

        assert is_stockfish_available() is False


def _make_engine_mock() -> MagicMock:
    """A fake StockfishEngine that records teardown calls."""
    engine = MagicMock()
    engine.is_fen_valid.return_value = True
    engine.get_best_move.return_value = "e2e4"
    engine.get_evaluation.return_value = {"type": "cp", "value": 20}
    return engine


_START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class TestEngineLifecycle:
    """Regression tests: the engine subprocess must always be torn down.

    Previously evaluate_fen's ``finally`` block was a no-op ``pass``, so an
    engine it created itself was never quit and leaked its OS process. These
    tests assert it is now closed on both the success and exception paths.
    """

    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_owned_engine_closed_on_success(self, mock_engine_class):
        """An engine created inside evaluate_fen is quit after success."""
        engine = _make_engine_mock()
        mock_engine_class.return_value = engine

        result = evaluate_fen(_START_FEN)

        assert result.best_move_uci == "e2e4"
        engine.send_quit_command.assert_called_once()

    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_owned_engine_closed_on_exception(self, mock_engine_class):
        """The engine is released even when evaluation raises mid-eval."""
        engine = _make_engine_mock()
        engine.get_best_move.side_effect = RuntimeError("engine exploded")
        mock_engine_class.return_value = engine

        with pytest.raises(StockfishError, match="Evaluation failed"):
            evaluate_fen(_START_FEN)

        engine.send_quit_command.assert_called_once()

    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_passed_in_engine_not_closed(self, mock_engine_class):
        """A caller-supplied engine is NOT closed (caller owns its lifecycle)."""
        engine = _make_engine_mock()

        evaluate_fen(_START_FEN, engine=engine)

        engine.send_quit_command.assert_not_called()
        # create_engine must not be used when an engine is supplied.
        mock_engine_class.assert_not_called()

    def test_close_engine_falls_back_to_kill(self):
        """close_engine hard-kills the subprocess if quit is unavailable."""
        engine = MagicMock(spec=["_stockfish"])  # no send_quit_command attr
        close_engine(engine)
        engine._stockfish.kill.assert_called_once()

    def test_close_engine_none_is_noop(self):
        """close_engine(None) is safe."""
        close_engine(None)  # must not raise


class TestEngineTimeout:
    """A wedged engine call must not block forever."""

    @patch.dict(os.environ, {"STOCKFISH_EVAL_TIMEOUT_S": "0.1"})
    @patch("services.api.engine.stockfish.StockfishEngine")
    def test_eval_timeout_kills_engine(self, mock_engine_class):
        engine = _make_engine_mock()
        engine.get_best_move.side_effect = lambda: time.sleep(5)
        mock_engine_class.return_value = engine

        # Timeout signals engine death (subclass of StockfishError) so a batch
        # caller can recreate the shared engine.
        with pytest.raises(StockfishEngineDeadError, match="timed out"):
            evaluate_fen(_START_FEN)

        # The wedged subprocess is killed so the worker thread can unblock.
        engine._stockfish.kill.assert_called()


class TestEngineConcurrencyBound:
    """Concurrent evaluations are bounded by a shared semaphore."""

    @patch.dict(os.environ, {"STOCKFISH_ACQUIRE_TIMEOUT_S": "0.1"})
    def test_rejects_when_concurrency_exhausted(self):
        one_slot = threading.BoundedSemaphore(1)
        with patch.object(sf_module, "_EVAL_SEMAPHORE", one_slot):
            # Occupy the only slot, then a new eval must be rejected.
            assert one_slot.acquire() is True
            try:
                with pytest.raises(StockfishError, match="concurrency limit"):
                    evaluate_fen(_START_FEN)
            finally:
                one_slot.release()


# --------------------------------------------------------------------------- #
# Audit gate 4: version-aware, reproducible engine eval cache.
# --------------------------------------------------------------------------- #


def _base_key_kwargs(**overrides) -> dict:
    """Canonical set of cache-key inputs; override one dim per test."""
    kwargs = dict(
        fen=_START_FEN,
        depth=12,
        movetime_ms=None,
        engine_name="stockfish",
        engine_version="16.1",
        threads=1,
        hash_mb=16,
        multipv=1,
    )
    kwargs.update(overrides)
    return kwargs


class TestCacheKeyInputs:
    """The cache key must fold in every result-changing input."""

    def test_identical_inputs_yield_identical_key(self):
        assert _compute_cache_key(**_base_key_kwargs()) == _compute_cache_key(
            **_base_key_kwargs()
        )

    @pytest.mark.parametrize(
        "override",
        [
            {"engine_version": "17.0"},
            {"engine_name": "/opt/other/stockfish"},
            {"depth": 20},
            {"movetime_ms": 500},
            {"threads": 4},
            {"hash_mb": 256},
            {"multipv": 3},
            {"conversion_version": EVAL_CONVERSION_VERSION + 1},
        ],
    )
    def test_each_dimension_changes_the_key(self, override):
        """Flipping any single result-changing dimension must change the key,
        so an old entry can never be reused under a different engine/config."""
        base = _compute_cache_key(**_base_key_kwargs())
        changed = _compute_cache_key(**_base_key_kwargs(**override))
        assert base != changed, f"key did not change for {override}"

    def test_fen_is_normalized(self):
        """Trivially different spellings of the same FEN (extra whitespace)
        collapse to the same key."""
        spaced = "  rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR   w KQkq - 0 1 "
        assert _compute_cache_key(**_base_key_kwargs()) == _compute_cache_key(
            **_base_key_kwargs(fen=spaced)
        )


class TestGetEngineVersion:
    """Version resolution must degrade, never crash, when no engine is present."""

    def test_unknown_when_no_binary(self):
        sf_module._ENGINE_VERSION_CACHE.clear()
        try:
            with patch.object(sf_module, "create_engine") as mock_create:
                mock_create.side_effect = StockfishNotFoundError("no binary")
                assert get_engine_version() == UNKNOWN_ENGINE_VERSION
        finally:
            sf_module._ENGINE_VERSION_CACHE.clear()

    def test_reads_version_from_live_engine(self):
        """A live engine's version is read directly (no probe spawned)."""
        engine = MagicMock()
        engine.get_stockfish_major_minor_version.return_value = "16.1"
        engine.get_stockfish_sha_version.return_value = ""
        with patch.object(sf_module, "create_engine") as mock_create:
            assert get_engine_version(engine) == "16.1"
            mock_create.assert_not_called()  # no throwaway probe when engine given


@contextmanager
def _cache_db():
    """In-memory SQLite DB patched into stockfish.SessionLocal."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def fake_session_local():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    with patch.object(sf_module, "SessionLocal", fake_session_local):
        yield Session


def _live_key(engine_version: str, fen: str = _START_FEN) -> str:
    """Recompute the exact key get_or_compute_eval will use for ``fen`` under
    the current process config and the given engine version."""
    return _compute_cache_key(
        fen=fen,
        depth=sf_module.get_search_depth(),
        movetime_ms=None,
        engine_name=sf_module.get_stockfish_path(),
        engine_version=engine_version,
        threads=sf_module.get_engine_threads(),
        hash_mb=sf_module.get_engine_hash_mb(),
        multipv=sf_module.get_engine_multipv(),
    )


class TestVersionAwareCache:
    """End-to-end cache behaviour across engine versions/configs."""

    def test_stale_entry_not_reused_after_version_change(self):
        """Core regression: an entry cached under engine version A must NOT be
        returned for the same FEN under version B (the pre-fix bug returned the
        stale eval because the key omitted the version)."""
        with _cache_db() as Session:
            # Seed a row as if computed under version A -> eval 1.0.
            with Session() as db:
                db.add(
                    FenEvalCache(
                        key=_live_key("A"),
                        fen=_START_FEN,
                        best_move_uci="e2e4",
                        eval_pawns=1.0,
                        engine_version="A",
                    )
                )
                db.commit()

            # Engine is now version B and would compute a different eval.
            def fake_eval(fen, engine=None, depth=None):
                return EvalResult(best_move_uci="e2e4", eval=2.0)

            stats: dict = {}
            with (
                patch.object(sf_module, "get_engine_version", return_value="B"),
                patch.object(sf_module, "evaluate_fen", side_effect=fake_eval),
            ):
                result = get_or_compute_eval(_START_FEN, cache_stats=stats)

            assert result.eval == 2.0  # recomputed, not the stale 1.0
            assert stats == {"misses": 1}

            # The version-B row was written; the stale A row is untouched.
            with Session() as db:
                assert db.get(FenEvalCache, _live_key("A")).eval_pawns == 1.0
                b_row = db.get(FenEvalCache, _live_key("B"))
                assert b_row is not None
                assert b_row.eval_pawns == 2.0
                assert b_row.engine_version == "B"

    def test_same_config_hits_cache(self):
        """A matching engine version/config still hits the cache without
        touching the engine at all."""
        with _cache_db() as Session:
            with Session() as db:
                db.add(
                    FenEvalCache(
                        key=_live_key("16.1"),
                        fen=_START_FEN,
                        best_move_uci="e2e4",
                        eval_pawns=0.42,
                        engine_version="16.1",
                    )
                )
                db.commit()

            def boom(fen, engine=None, depth=None):  # pragma: no cover - must not run
                raise AssertionError("cache hit must not call evaluate_fen")

            stats: dict = {}
            with (
                patch.object(sf_module, "get_engine_version", return_value="16.1"),
                patch.object(sf_module, "evaluate_fen", side_effect=boom),
            ):
                result = get_or_compute_eval(_START_FEN, cache_stats=stats)

            assert result.eval == 0.42
            assert stats == {"hits": 1}

    def test_computed_entry_is_self_describing(self):
        """A freshly computed entry records engine version + full config so it
        is reproducible/auditable."""
        with _cache_db() as Session:
            with (
                patch.object(sf_module, "get_engine_version", return_value="16.1"),
                patch.object(
                    sf_module,
                    "evaluate_fen",
                    side_effect=lambda fen, engine=None, depth=None: EvalResult(
                        "e2e4", 0.2
                    ),
                ),
            ):
                get_or_compute_eval(_START_FEN)

            with Session() as db:
                row = db.get(FenEvalCache, _live_key("16.1"))
            assert row is not None
            assert row.engine_version == "16.1"
            assert row.threads == sf_module.get_engine_threads()
            assert row.hash_mb == sf_module.get_engine_hash_mb()
            assert row.multipv == sf_module.get_engine_multipv()
            assert row.conversion_version == EVAL_CONVERSION_VERSION

    def test_terminal_eval_not_cached(self):
        """Terminal positions (no best move) are intentionally not cached, so a
        cache miss/failure only ever costs performance, never correctness."""
        with _cache_db() as Session:
            terminal = EvalResult(
                best_move_uci=None, eval=-MATE_EVALUATION, mate_in=0, is_terminal=True
            )
            with (
                patch.object(sf_module, "get_engine_version", return_value="16.1"),
                patch.object(
                    sf_module,
                    "evaluate_fen",
                    side_effect=lambda fen, engine=None, depth=None: terminal,
                ),
            ):
                result = get_or_compute_eval(_START_FEN)

            assert result.is_terminal is True
            with Session() as db:
                assert db.get(FenEvalCache, _live_key("16.1")) is None

    def test_concurrent_insert_returns_existing_entry(self):
        """If a concurrent writer inserts the same key between our miss-read and
        our commit, the IntegrityError path re-selects and returns that row
        rather than raising."""
        with _cache_db() as Session:
            key = _live_key("16.1")

            # A fake SessionLocal that, on the *write* (2nd) use, first inserts a
            # conflicting row (simulating a concurrent process) before yielding.
            call_count = {"n": 0}
            real_cm = sf_module.SessionLocal  # the patched _cache_db context mgr

            @contextmanager
            def racing_session_local():
                call_count["n"] += 1
                if call_count["n"] == 2:
                    with Session() as other:
                        other.add(
                            FenEvalCache(
                                key=key,
                                fen=_START_FEN,
                                best_move_uci="e2e4",
                                eval_pawns=9.9,  # value written by the "other" process
                                engine_version="16.1",
                            )
                        )
                        other.commit()
                with real_cm() as db:
                    yield db

            with (
                patch.object(sf_module, "get_engine_version", return_value="16.1"),
                patch.object(sf_module, "SessionLocal", racing_session_local),
                patch.object(
                    sf_module,
                    "evaluate_fen",
                    side_effect=lambda fen, engine=None, depth=None: EvalResult(
                        "e2e4", 2.0
                    ),
                ),
            ):
                result = get_or_compute_eval(_START_FEN)

            # We must return the concurrently-inserted value, not raise.
            assert result.eval == 9.9
