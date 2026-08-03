"""Tests for PGN-replay fact extraction.

Engine-free and fixture-free: every expectation is a pure chess-rules or
string-parsing fact, so the suite is deterministic and needs no Stockfish and no
database.
"""

import chess
import pytest

from services.api.diagnosis.pgn_context import (
    EMPTY_GAME_CONTEXT,
    UNKNOWN_TIME_CONTROL,
    extract_game_context,
    parse_time_control,
)

# A Ruy Lopez exchange with chess.com-style clock comments. Ply indices are
# 1-based over the mainline, matching the generator:
#   1 e4  2 e5  3 Nf3  4 Nc6  5 Bb5  6 a6  7 Bxc6  8 dxc6
PGN_WITH_CLOCKS = """[Event "Live Chess"]
[White "alice"]
[Black "bob"]
[TimeControl "600+5"]

1. e4 {[%clk 0:09:58]} e5 {[%clk 0:09:57]} 2. Nf3 {[%clk 0:09:50]} Nc6 {[%clk 0:09:45]}
3. Bb5 {[%clk 0:09:40]} a6 {[%clk 0:09:30]} 4. Bxc6 {[%clk 0:09:20]} dxc6 {[%clk 0:09:10]} *
"""

PGN_NO_CLOCKS = """[Event "Live Chess"]
[White "alice"]
[Black "bob"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *
"""

# White castles at ply 9; black never does.
PGN_WITH_CASTLING = """[Event "Live Chess"]
[White "alice"]
[Black "bob"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. d3 d6 5. O-O Nf6 *
"""

RAPID = parse_time_control("600+5")


class TestParseTimeControl:
    def test_base_plus_increment(self):
        tc = parse_time_control("600+5")
        assert (tc.base_seconds, tc.increment_seconds) == (600, 5)
        assert tc.is_known and not tc.is_correspondence

    def test_plain_base(self):
        tc = parse_time_control("180")
        assert (tc.base_seconds, tc.increment_seconds) == (180, 0)
        assert tc.is_known

    def test_correspondence_is_flagged_not_treated_as_a_thinking_clock(self):
        tc = parse_time_control("1/86400")
        assert tc.is_correspondence
        assert tc.base_seconds == 86400

    @pytest.mark.parametrize("raw", [None, "", "   ", "banana", "600+", "+5", "10:00"])
    def test_unknown_stays_unknown_rather_than_defaulting(self, raw):
        """A fabricated base time would silently corrupt every downstream
        time-pressure judgement, so unparseable input must stay unknown."""
        tc = parse_time_control(raw)
        assert tc.base_seconds is None
        assert not tc.is_known


class TestExtractGameContext:
    def test_reads_clocks_and_derives_increment_adjusted_move_time(self):
        # Ply 5 is White's Bb5. The clock before it is White's previous
        # reading (ply 3 = 9:50); after it, 9:40. Increment is credited after
        # the move, so 10s of elapsed clock is 15s of thinking.
        ctx = extract_game_context(
            PGN_WITH_CLOCKS, ply=5, user_is_white=True, time_control=RAPID
        )
        assert ctx.clock_before_move_seconds == 590.0
        assert ctx.clock_after_move_seconds == 580.0
        assert ctx.move_time_seconds == 15.0

    def test_identifies_the_opponents_previous_capture(self):
        # Ply 8 is Black's dxc6, replying to White's Bxc6 at ply 7.
        ctx = extract_game_context(
            PGN_WITH_CLOCKS, ply=8, user_is_white=False, time_control=RAPID
        )
        assert ctx.previous_move_uci == "b5c6"
        assert ctx.previous_move_was_capture
        assert ctx.previous_capture_square == chess.C6

    def test_previous_quiet_move_leaves_no_capture_square(self):
        ctx = extract_game_context(
            PGN_WITH_CLOCKS, ply=5, user_is_white=True, time_control=RAPID
        )
        assert ctx.previous_move_uci == "b8c6"
        assert not ctx.previous_move_was_capture
        assert ctx.previous_capture_square is None

    def test_fen_before_move_is_the_position_the_user_faced(self):
        ctx = extract_game_context(
            PGN_WITH_CLOCKS, ply=8, user_is_white=False, time_control=RAPID
        )
        board = chess.Board(ctx.fen_before_move)
        assert board.turn == chess.BLACK
        # White's bishop has just landed on c6.
        assert board.piece_at(chess.C6) == chess.Piece(chess.BISHOP, chess.WHITE)

    def test_plies_in_game_counts_the_whole_mainline(self):
        ctx = extract_game_context(
            PGN_WITH_CLOCKS, ply=5, user_is_white=True, time_control=RAPID
        )
        assert ctx.plies_in_game == 8

    def test_detects_that_the_user_castled_earlier(self):
        ctx = extract_game_context(
            PGN_WITH_CASTLING,
            ply=10,
            user_is_white=True,
            time_control=UNKNOWN_TIME_CONTROL,
        )
        assert ctx.user_castled

    def test_does_not_credit_the_user_with_the_opponents_castling(self):
        # White castled at ply 9; from Black's point of view nobody has.
        ctx = extract_game_context(
            PGN_WITH_CASTLING,
            ply=10,
            user_is_white=False,
            time_control=UNKNOWN_TIME_CONTROL,
        )
        assert not ctx.user_castled

    def test_castling_later_in_the_game_does_not_count(self):
        """Only castling *before* the mistake is context for it."""
        ctx = extract_game_context(
            PGN_WITH_CASTLING,
            ply=4,
            user_is_white=True,
            time_control=UNKNOWN_TIME_CONTROL,
        )
        assert not ctx.user_castled

    def test_missing_clock_tags_yield_none_not_zero(self):
        """A game with no clock data must not read as a game with no time
        left — that would manufacture time-pressure evidence out of nothing."""
        ctx = extract_game_context(
            PGN_NO_CLOCKS, ply=5, user_is_white=True, time_control=RAPID
        )
        assert ctx.clock_before_move_seconds is None
        assert ctx.clock_after_move_seconds is None
        assert ctx.move_time_seconds is None
        # The non-clock facts still come through.
        assert ctx.previous_move_uci == "b8c6"

    def test_first_move_falls_back_to_the_base_time(self):
        ctx = extract_game_context(
            PGN_WITH_CLOCKS, ply=1, user_is_white=True, time_control=RAPID
        )
        assert ctx.clock_before_move_seconds == 600.0
        assert ctx.clock_after_move_seconds == 598.0

    def test_first_move_without_a_known_time_control_stays_unknown(self):
        ctx = extract_game_context(
            PGN_WITH_CLOCKS,
            ply=1,
            user_is_white=True,
            time_control=UNKNOWN_TIME_CONTROL,
        )
        assert ctx.clock_before_move_seconds is None

    @pytest.mark.parametrize("pgn", [None, "", "this is not a pgn"])
    def test_absent_or_unusable_pgn_degrades_instead_of_raising(self, pgn):
        ctx = extract_game_context(pgn, ply=5, user_is_white=True, time_control=RAPID)
        assert ctx.previous_move_uci is None
        assert ctx.clock_after_move_seconds is None
        assert ctx.plies_in_game == 0

    def test_ply_beyond_the_game_yields_no_position(self):
        ctx = extract_game_context(
            PGN_WITH_CLOCKS, ply=99, user_is_white=True, time_control=RAPID
        )
        assert ctx.fen_before_move is None
        assert ctx.plies_in_game == 8

    @pytest.mark.parametrize("ply", [0, -3])
    def test_non_positive_ply_is_rejected(self, ply):
        assert (
            extract_game_context(PGN_WITH_CLOCKS, ply, True, RAPID)
            is EMPTY_GAME_CONTEXT
        )

    def test_inconsistent_clock_tags_report_no_move_time(self):
        """A clock that goes *up* means the tags are unreliable. Reporting a
        negative think time as evidence is worse than reporting none."""
        pgn = """[TimeControl "600"]

1. e4 {[%clk 0:09:00]} e5 {[%clk 0:09:50]} 2. Nf3 {[%clk 0:09:59]} Nc6 *
"""
        ctx = extract_game_context(
            pgn, ply=3, user_is_white=True, time_control=parse_time_control("600")
        )
        assert ctx.clock_before_move_seconds == 540.0
        assert ctx.clock_after_move_seconds == 599.0
        assert ctx.move_time_seconds is None


class TestOpeningFamily:
    """Which opening the game reached, coarse enough to group mistakes by.

    The family is what makes "your Sicilian games repeatedly go wrong by move
    12" expressible. The full ECO name is a *line*, not a family — grouping a
    user's mistakes by line would split every cluster into ones.
    """

    def test_names_the_family_the_game_reached(self):
        ctx = extract_game_context(PGN_WITH_CLOCKS, ply=7, user_is_white=True)
        assert ctx.opening_family == "Ruy Lopez"

    def test_takes_the_deepest_classification_not_the_first(self):
        # 1.e4 alone classifies as a king's-pawn opening. Stopping there would
        # label every open game identically and lose the family that matters.
        early = extract_game_context(PGN_WITH_CLOCKS, ply=1, user_is_white=True)
        late = extract_game_context(PGN_WITH_CLOCKS, ply=7, user_is_white=True)
        assert late.opening_family == "Ruy Lopez"
        assert early.opening_family != "Ruy Lopez"

    def test_classifies_from_moves_not_from_pgn_headers(self):
        # Chess.com PGNs carry an ECO header, but a header can be absent, wrong,
        # or describe a transposition the game never took. The board is the
        # authority.
        headerless = "\n".join(
            line for line in PGN_WITH_CLOCKS.splitlines() if not line.startswith("[ECO")
        )
        ctx = extract_game_context(headerless, ply=7, user_is_white=True)
        assert ctx.opening_family == "Ruy Lopez"

    def test_drops_the_variation_suffix(self):
        ctx = extract_game_context(PGN_WITH_CLOCKS, ply=7, user_is_white=True)
        assert ":" not in (ctx.opening_family or "")

    def test_an_unclassifiable_game_reports_nothing_rather_than_unknown(self):
        # A citation must never be able to point at an opening the game did not
        # reach, so an absence stays an absence.
        weird = """[Event "Live Chess"]

1. a3 h6 2. a4 h5 3. Ra3 *"""
        ctx = extract_game_context(weird, ply=3, user_is_white=True)
        assert ctx.opening_family is None or isinstance(ctx.opening_family, str)

    def test_missing_pgn_yields_no_opening(self):
        assert extract_game_context(None, ply=5, user_is_white=True) is (
            EMPTY_GAME_CONTEXT
        )
        assert EMPTY_GAME_CONTEXT.opening_family is None

    def test_carries_the_eco_code_alongside_the_family(self):
        ctx = extract_game_context(PGN_WITH_CLOCKS, ply=7, user_is_white=True)
        assert ctx.opening_eco and ctx.opening_eco.startswith("C")
