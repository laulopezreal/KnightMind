"""Tests for Stockfish engine wrapper."""

import os
from unittest.mock import MagicMock, patch

import pytest

from .stockfish import (
    MATE_EVALUATION,
    EvalResult,
    StockfishError,
    StockfishNotFoundError,
    _convert_eval_to_pawns,
    evaluate_fen,
    get_analysis_params,
    get_stockfish_path,
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
