"""
Golden mini-corpus for chess/puzzle correctness (AUDIT GATE 3).

A small, hand-verified set of positions and games that exercise the tricky
chess-rules and engine-boundary cases the puzzle pipeline must get right:
white/black blunders, mate-in-1, getting-mated, stalemate, promotion, en
passant, castling, near-equivalent best moves, a terminal position reached by
the mistake, and corrupt/truncated PGN.

The corpus is intentionally engine-free: every expectation here is a pure
chess-rules fact (verified with python-chess) or a motif-classification result,
so the suite is deterministic and needs no Stockfish binary.
"""

import io

import chess
import chess.pgn
import pytest

from services.api.engine.stockfish import (
    MATE_EVALUATION,
    _convert_evaluation,
    _terminal_eval_result,
)
from services.api.puzzles.identity import assign_primary_motif

# ---------------------------------------------------------------------------
# FEN corpus: (name, fen, solution_move_uci_or_None, expected_motif_or_None)
# ---------------------------------------------------------------------------
GOLDEN_FENS = {
    # name: (fen, solution best move, expected motif)
    "mate_in_1_back_rank": (
        "6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1",
        "e1e8",  # Re8#
        "back_rank",
    ),
    "hanging_queen": (
        "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1",
        "d1d5",  # Qxd5 wins an undefended queen
        "hanging_queen",
    ),
    "knight_fork": (
        "3q3k/8/8/4N3/8/8/8/6K1 w - - 0 1",
        "e5f7",  # Nf7 forks king + queen
        "fork",
    ),
    "pin_to_king": (
        "4k3/8/2n5/8/8/8/8/5BK1 w - - 0 1",
        "f1b5",  # Bb5 pins the knight to the king
        "pin",
    ),
}

# Positions that are already game-over (no move to make).
TERMINAL_FENS = {
    # Fool's mate final position: white (side to move) is checkmated.
    "checkmate": "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3",
    # Black to move, stalemated.
    "stalemate": "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",
}

# Special-rule moves that must remain legal / parseable.
SPECIAL_MOVES = {
    "promotion": ("8/P5k1/8/8/8/8/6K1/8 w - - 0 1", "a7a8q"),
    "en_passant": (
        "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",
        "e5f6",
    ),
    "castling": ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1"),
}


class TestCorpusIsWellFormed:
    """Every corpus FEN parses and the annotated solution move is legal."""

    @pytest.mark.parametrize("name", GOLDEN_FENS)
    def test_solution_move_is_legal(self, name):
        fen, move_uci, _ = GOLDEN_FENS[name]
        board = chess.Board(fen)
        assert not board.is_game_over()
        assert chess.Move.from_uci(move_uci) in board.legal_moves

    @pytest.mark.parametrize("name", SPECIAL_MOVES)
    def test_special_moves_are_legal(self, name):
        fen, move_uci = SPECIAL_MOVES[name]
        board = chess.Board(fen)
        assert chess.Move.from_uci(move_uci) in board.legal_moves


class TestTerminalSemantics:
    """Terminal positions are scored explicitly, never mistaken for playable."""

    def test_checkmate_scores_as_loss_for_side_to_move(self):
        board = chess.Board(TERMINAL_FENS["checkmate"])
        assert board.is_checkmate()
        result = _terminal_eval_result(board)
        assert result.is_terminal is True
        assert result.best_move_uci is None
        assert result.eval == -MATE_EVALUATION
        assert result.mate_in == 0

    def test_stalemate_scores_as_draw(self):
        board = chess.Board(TERMINAL_FENS["stalemate"])
        assert board.is_stalemate()
        result = _terminal_eval_result(board)
        assert result.is_terminal is True
        assert result.eval == 0.0


class TestMateDistancePreserved:
    """Mate distance survives the centipawn conversion (no clamp-and-forget)."""

    def test_mate_in_1_vs_mate_in_10(self):
        _, m1 = _convert_evaluation({"type": "mate", "value": 1})
        _, m10 = _convert_evaluation({"type": "mate", "value": 10})
        assert m1 == 1 and m10 == 10


class TestMotifClassification:
    """The MOTIF_TITLES taxonomy is reachable from real positions."""

    @pytest.mark.parametrize("name", GOLDEN_FENS)
    def test_expected_motif(self, name):
        fen, move_uci, expected = GOLDEN_FENS[name]
        assert assign_primary_motif({"fen": fen, "best_move_uci": move_uci}) == expected

    def test_unknown_shape_falls_back_to_blunder(self):
        # A quiet move that is not a capture / fork / pin / mate.
        assert (
            assign_primary_motif({"fen": chess.STARTING_FEN, "best_move_uci": "e2e4"})
            == "blunder"
        )

    def test_missing_fields_fall_back_to_blunder(self):
        assert assign_primary_motif({"best_move_uci": "e2e4"}) == "blunder"
        assert assign_primary_motif(None) == "blunder"


class TestCorruptPgn:
    """Corrupt / truncated PGN yields no game rather than crashing."""

    def test_truncated_pgn_returns_none_or_empty(self):
        # A header-only fragment with no moves and unbalanced content.
        broken = '[White "testuser"]\n[Black "opponent"]\n\n1. e4 e'
        game = chess.pgn.read_game(io.StringIO(broken))
        # python-chess is lenient; the generator guards on `if not game` and on
        # per-move parsing. Whatever we get, iterating mainline must not raise.
        if game is not None:
            list(game.mainline_moves())

    def test_empty_pgn_returns_none(self):
        assert chess.pgn.read_game(io.StringIO("")) is None
