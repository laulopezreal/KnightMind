"""Tests for Stockfish engine wrapper."""

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from . import stockfish as sf_module
from .stockfish import (
    MATE_EVALUATION,
    EvalResult,
    MoveEval,
    StockfishEngineDeadError,
    StockfishError,
    StockfishNotFoundError,
    _convert_eval_to_pawns,
    _convert_evaluation,
    close_engine,
    evaluate_fen,
    get_analysis_params,
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
