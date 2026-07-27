"""Tests for deterministic evidence extraction.

Engine-free and database-free by construction: every expectation is a pure
chess-rules fact verifiable by hand from the FEN in the test.
"""

import dataclasses

import chess
import pytest

from services.api.diagnosis.evidence import (
    EXTRACTION_VERSION,
    EvidenceUnavailable,
    GameFacts,
    HistoryFacts,
    PuzzleFacts,
    evidence_hash,
    extract_evidence,
    to_evidence_items,
)
from services.api.diagnosis.pgn_context import (
    EMPTY_GAME_CONTEXT,
    GameContext,
    parse_time_control,
)

STARTING_AFTER_1E4E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
# Black queen on d5 is undefended; so is White's on d1. White to move.
HANGING_QUEEN = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"
# Bare rook endgame.
ROOK_ENDGAME = "8/5k2/8/8/8/8/5K2/4R3 w - - 0 1"


def make_puzzle(fen, played, best, ply=30, **overrides):
    fields = dict(
        fen=fen,
        played_move_uci=played,
        best_move_uci=best,
        ply=ply,
        eval_before=1.0,
        eval_after=-1.5,
        swing=2.5,
    )
    fields.update(overrides)
    return PuzzleFacts(**fields)


def extract(
    fen,
    played,
    best,
    ply=30,
    context=EMPTY_GAME_CONTEXT,
    game=None,
    history=None,
    **overrides,
):
    """Extract with the user inferred as the side to move (always true for a
    KnightMind puzzle: the position is the one the user faced)."""
    game = game or GameFacts(user_is_white=chess.Board(fen).turn == chess.WHITE)
    return extract_evidence(
        make_puzzle(fen, played, best, ply, **overrides), game, context, history
    )


def capture_context(square: int, previous_uci: str = "e4d5") -> GameContext:
    """A context in which the opponent has just captured on ``square``."""
    return dataclasses.replace(
        EMPTY_GAME_CONTEXT,
        previous_move_uci=previous_uci,
        previous_move_was_capture=True,
        previous_capture_square=square,
    )


class TestPhase:
    def test_early_full_board_is_opening(self):
        packet = extract(STARTING_AFTER_1E4E5, "g1f3", "g1f3", ply=3)
        assert packet.position.phase == "opening"
        assert packet.position.non_pawn_material == 62

    def test_bare_rook_is_endgame(self):
        packet = extract(ROOK_ENDGAME, "e1e8", "e1e8", ply=61)
        assert packet.position.phase == "endgame"

    def test_material_beats_move_number(self):
        """A queenless, stripped-down position is an endgame even at move 3 —
        what makes a phase is what is on the board, not the clock on the wall."""
        packet = extract(ROOK_ENDGAME, "e1e8", "e1e8", ply=5)
        assert packet.position.phase == "endgame"

    def test_move_number_derives_from_ply(self):
        assert extract(HANGING_QUEEN, "d1d2", "d1d5", ply=41).position.move_number == 21
        assert extract(HANGING_QUEEN, "d1d2", "d1d5", ply=42).position.move_number == 21


class TestMoveClassification:
    def test_quiet_move_is_neither_capture_nor_check(self):
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        assert packet.played.move.san == "Qd2"
        assert packet.played.move.is_quiet
        assert not packet.played.move.is_capture

    def test_capture_records_the_value_taken(self):
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        assert packet.best.move.san == "Qxd5"
        assert packet.best.move.is_capture
        assert packet.best.move.captured_value == 9
        assert not packet.best.move.is_quiet

    def test_promotion_is_not_quiet(self):
        packet = extract("8/P4k2/8/8/8/8/5K2/8 w - - 0 1", "a7a8q", "a7a8q", ply=81)
        assert packet.played.move.is_promotion
        assert not packet.played.move.is_quiet

    def test_en_passant_capture_is_valued_as_a_pawn(self):
        packet = extract("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2", "e5d6", "e5d6", ply=21)
        assert packet.played.move.is_capture
        assert packet.played.move.captured_value == 1

    def test_check_is_forcing(self):
        packet = extract(ROOK_ENDGAME, "e1f1", "e1e7", ply=61)
        assert packet.best.move.is_check
        assert not packet.best.move.is_quiet


class TestLoosePieces:
    def test_finds_undefended_pieces_on_both_sides(self):
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        assert [p.square for p in packet.loose.own] == ["d1"]
        assert [p.square for p in packet.loose.opponent] == ["d5"]
        assert packet.loose.own_value == 9
        assert packet.loose.own_count == 1

    def test_records_whether_a_loose_piece_is_already_attacked(self):
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        assert packet.loose.own[0].attacked_by_opponent

    def test_defended_pieces_are_not_loose(self):
        # Rook on a1 defended by the rook on b1.
        packet = extract("6k1/8/8/8/8/8/8/RR4K1 w - - 0 1", "a1a8", "a1a8", ply=61)
        assert packet.loose.own == ()

    def test_pawns_are_never_counted_as_loose(self):
        """An undefended pawn is ordinary; counting them would bury the signal."""
        packet = extract(STARTING_AFTER_1E4E5, "g1f3", "g1f3", ply=3)
        assert all(p.piece != "pawn" for p in packet.loose.own)
        assert all(p.piece != "pawn" for p in packet.loose.opponent)

    def test_loose_pieces_are_ordered_by_value(self):
        # Queen on d1 and knight on a5, neither defending the other.
        packet = extract("6k1/8/8/N7/8/8/8/3Q2K1 w - - 0 1", "d1d8", "d1d8", ply=61)
        assert [p.piece for p in packet.loose.own] == ["queen", "knight"]


class TestRecaptureAndZwischenzug:
    def test_recapture_on_the_contested_square_is_flagged(self):
        # Black to move; White just captured on d5, Black recaptures there.
        fen = "rnbqkbnr/ppp2ppp/8/3Pp3/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 3"
        packet = extract(fen, "d8d5", "d8d5", ply=6, context=capture_context(chess.D5))
        assert packet.played.is_recapture

    def test_a_move_elsewhere_is_not_a_recapture(self):
        fen = "rnbqkbnr/ppp2ppp/8/3Pp3/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 3"
        packet = extract(fen, "g8f6", "d8d5", ply=6, context=capture_context(chess.D5))
        assert not packet.played.is_recapture

    def test_no_prior_capture_means_no_recapture(self):
        packet = extract(HANGING_QUEEN, "d1d5", "d1d5", context=EMPTY_GAME_CONTEXT)
        assert not packet.played.is_recapture

    def test_forcing_solution_away_from_the_captured_square_is_zwischenzug_like(self):
        # The opponent captured on d5; the solution grabs the queen on d5's
        # *other* side of the board instead of dealing with that square.
        packet = extract(
            HANGING_QUEEN, "d1d2", "d1d5", context=capture_context(chess.A7)
        )
        assert packet.best.is_zwischenzug_like

    def test_recapturing_solution_is_not_zwischenzug_like(self):
        packet = extract(
            HANGING_QUEEN, "d1d2", "d1d5", context=capture_context(chess.D5)
        )
        assert not packet.best.is_zwischenzug_like

    def test_quiet_solution_is_never_zwischenzug_like(self):
        """A zwischenzug is by definition forcing; a quiet solution is a
        different phenomenon and must not borrow the label."""
        packet = extract(
            HANGING_QUEEN, "d1d5", "g1h1", context=capture_context(chess.A7)
        )
        assert not packet.best.is_zwischenzug_like


class TestThreats:
    def test_counts_the_users_forcing_options(self):
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        assert packet.threats.legal_captures == 1
        assert packet.threats.legal_checks == 0

    def test_counts_the_opponents_forcing_replies_after_the_mistake(self):
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        assert packet.threats.opponent_forcing_replies > 0

    def test_a_move_that_ends_the_game_leaves_no_replies(self):
        """The reply count is measured on the position *after* the played move,
        which may be terminal — counting must not walk a finished game."""
        mate = extract("6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1", "e1e8", "e1e8", ply=61)
        assert mate.threats.opponent_forcing_replies == 0


class TestKingSafety:
    def test_king_walled_in_by_its_own_pawns_is_back_rank_boxed(self):
        packet = extract("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", "a1a8", "a1a8", ply=61)
        assert packet.king.back_rank_boxed
        assert packet.king.king_square == "g1"

    def test_a_gap_in_the_pawn_shield_is_not_boxed(self):
        packet = extract("r5k1/5ppp/8/8/8/8/6PP/6K1 w - - 0 1", "g1h1", "g1h1", ply=61)
        assert not packet.king.back_rank_boxed

    def test_counts_attackers_on_the_king_ring(self):
        # The black queen on d5 rakes g2, next to White's king.
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        assert packet.king.ring_attackers == 1

    def test_castling_comes_from_the_replay_not_the_fen(self):
        """Castling rights vanish when the king merely *moves*, so a FEN cannot
        tell a castled king from a king that shuffled."""
        packet = extract(
            HANGING_QUEEN,
            "d1d2",
            "d1d5",
            context=dataclasses.replace(EMPTY_GAME_CONTEXT, user_castled=True),
        )
        assert packet.king.user_castled


class TestClock:
    def test_flags_time_pressure_relative_to_the_format(self):
        context = dataclasses.replace(
            EMPTY_GAME_CONTEXT,
            clock_before_move_seconds=40.0,
            clock_after_move_seconds=32.0,
            move_time_seconds=8.0,
        )
        packet = extract(
            HANGING_QUEEN,
            "d1d2",
            "d1d5",
            context=context,
            game=GameFacts(user_is_white=True, time_control=parse_time_control("600")),
        )
        assert packet.clock.is_time_pressure  # 32s of a 10-minute game
        assert packet.clock.move_time_seconds == 8.0

    def test_a_comfortable_clock_is_not_pressure(self):
        context = dataclasses.replace(
            EMPTY_GAME_CONTEXT, clock_after_move_seconds=400.0
        )
        packet = extract(
            HANGING_QUEEN,
            "d1d2",
            "d1d5",
            context=context,
            game=GameFacts(user_is_white=True, time_control=parse_time_control("600")),
        )
        assert packet.clock.is_time_pressure is False

    def test_absolute_floor_protects_short_formats(self):
        """10% of a 60-second game is 6s; without the floor, 12 seconds left in
        a bullet game would read as comfortable."""
        context = dataclasses.replace(EMPTY_GAME_CONTEXT, clock_after_move_seconds=12.0)
        packet = extract(
            HANGING_QUEEN,
            "d1d2",
            "d1d5",
            context=context,
            game=GameFacts(user_is_white=True, time_control=parse_time_control("60")),
        )
        assert packet.clock.is_time_pressure

    def test_correspondence_is_never_time_pressure(self):
        context = dataclasses.replace(EMPTY_GAME_CONTEXT, clock_after_move_seconds=5.0)
        packet = extract(
            HANGING_QUEEN,
            "d1d2",
            "d1d5",
            context=context,
            game=GameFacts(
                user_is_white=True, time_control=parse_time_control("1/86400")
            ),
        )
        assert packet.clock.is_time_pressure is False

    def test_unknown_clock_is_none_not_false(self):
        """None and False are different claims: 'we don't know' must not be
        recorded as 'they had plenty of time'."""
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        assert packet.clock.is_time_pressure is None


class TestUnusableInput:
    def test_unparseable_fen_raises(self):
        with pytest.raises(EvidenceUnavailable, match="FEN"):
            extract_evidence(
                make_puzzle("not-a-fen", "d1d5", "d1d5"),
                GameFacts(user_is_white=True),
            )

    def test_illegal_played_move_raises(self):
        with pytest.raises(EvidenceUnavailable, match="illegal played"):
            extract(HANGING_QUEEN, "a1a8", "d1d5")

    def test_illegal_best_move_raises(self):
        with pytest.raises(EvidenceUnavailable, match="illegal best"):
            extract(HANGING_QUEEN, "d1d2", "a1a8")

    @pytest.mark.parametrize("bad", ["", None, "zz99"])
    def test_missing_or_malformed_move_raises(self, bad):
        with pytest.raises(EvidenceUnavailable):
            extract(HANGING_QUEEN, bad, "d1d5")

    def test_missing_optional_context_degrades_instead_of_raising(self):
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5", history=None)
        assert packet.history.puzzle_fail_count == 0
        assert packet.game.plies_in_game == 0


class TestPgnDesync:
    def test_a_pgn_describing_another_position_is_discarded(self):
        """PGN-derived facts belong to the position the PGN replayed to. If that
        is not the puzzle's position, using them would attribute another
        position's clock and previous move to this mistake."""
        context = dataclasses.replace(
            EMPTY_GAME_CONTEXT,
            fen_before_move=STARTING_AFTER_1E4E5,  # not HANGING_QUEEN
            previous_move_uci="e2e4",
            previous_move_was_capture=True,
            previous_capture_square=chess.D5,
            clock_after_move_seconds=12.0,
            user_castled=True,
        )
        packet = extract(HANGING_QUEEN, "d1d5", "d1d5", context=context)
        assert packet.game.pgn_desync
        assert packet.clock.seconds_left_after_move is None
        assert not packet.played.is_recapture
        assert not packet.king.user_castled

    def test_a_matching_pgn_is_trusted(self):
        board = chess.Board(HANGING_QUEEN)
        context = dataclasses.replace(
            EMPTY_GAME_CONTEXT,
            fen_before_move=board.fen(),
            clock_after_move_seconds=12.0,
        )
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5", context=context)
        assert not packet.game.pgn_desync
        assert packet.clock.seconds_left_after_move == 12.0


class TestNoIdentityLeaks:
    """Guards the redaction promise structurally (plan decision D3): the packet
    type simply has nowhere to put an identity, so redaction cannot be forgotten
    at a call site."""

    FORBIDDEN = {"username", "user_name", "email", "account", "account_id", "handle"}

    def _field_names(self, obj, seen=None):
        seen = seen if seen is not None else set()
        if not dataclasses.is_dataclass(obj):
            return seen
        for field in dataclasses.fields(obj):
            seen.add(field.name)
            value = getattr(obj, field.name)
            if dataclasses.is_dataclass(value):
                self._field_names(value, seen)
            elif isinstance(value, tuple):
                for item in value:
                    self._field_names(item, seen)
        return seen

    def test_no_identity_field_anywhere_in_the_packet(self):
        packet = extract(
            HANGING_QUEEN,
            "d1d2",
            "d1d5",
            history=HistoryFacts(puzzle_attempts=3, puzzle_fail_count=2),
        )
        assert not self._field_names(packet) & self.FORBIDDEN

    def test_serialised_packet_carries_no_identity_key(self):
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        rendered = str(dataclasses.asdict(packet)).lower()
        for token in ("username", "email", "account_id"):
            assert token not in rendered


class TestEvidenceHash:
    def test_is_stable_for_identical_input(self):
        a = extract(HANGING_QUEEN, "d1d2", "d1d5")
        b = extract(HANGING_QUEEN, "d1d2", "d1d5")
        assert evidence_hash(a) == evidence_hash(b)

    def test_changes_when_a_fact_changes(self):
        a = extract(HANGING_QUEEN, "d1d2", "d1d5")
        b = extract(
            HANGING_QUEEN, "d1d2", "d1d5", history=HistoryFacts(puzzle_fail_count=4)
        )
        assert evidence_hash(a) != evidence_hash(b)

    def test_folds_in_the_extraction_version(self, monkeypatch):
        """A change in what a field *means* must invalidate cached diagnoses
        even when every value is byte-identical."""
        packet = extract(HANGING_QUEEN, "d1d2", "d1d5")
        before = evidence_hash(packet)
        monkeypatch.setattr(
            "services.api.diagnosis.evidence.EXTRACTION_VERSION",
            EXTRACTION_VERSION + 1,
        )
        assert evidence_hash(packet) != before


class TestEvidenceItems:
    def test_ids_are_unique(self):
        items = to_evidence_items(extract(HANGING_QUEEN, "d1d2", "d1d5"))
        ids = [i.id for i in items]
        assert len(ids) == len(set(ids))

    def test_only_measured_facts_are_citable(self):
        """An unknown clock must produce no clock item, so a citation can never
        point at an absence."""
        items = to_evidence_items(extract(HANGING_QUEEN, "d1d2", "d1d5"))
        assert not [i for i in items if i.id.startswith("clock.")]

    def test_measured_clock_becomes_citable(self):
        context = dataclasses.replace(
            EMPTY_GAME_CONTEXT,
            clock_after_move_seconds=32.0,
            move_time_seconds=4.0,
        )
        items = {
            i.id: i.value
            for i in to_evidence_items(
                extract(HANGING_QUEEN, "d1d2", "d1d5", context=context)
            )
        }
        assert items["clock.seconds_left"] == "32"
        assert items["clock.move_time"] == "4"

    def test_loose_pieces_are_described_in_words(self):
        items = {
            i.id: i.value
            for i in to_evidence_items(extract(HANGING_QUEEN, "d1d2", "d1d5"))
        }
        assert items["loose.own"] == "queen on d1"
        assert items["loose.own_value"] == "9"

    def test_core_facts_are_always_present(self):
        items = {
            i.id for i in to_evidence_items(extract(HANGING_QUEEN, "d1d2", "d1d5"))
        }
        assert {"position.phase", "played.move", "best.move", "eval.swing"} <= items
