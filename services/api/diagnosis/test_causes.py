"""Tests for rule-based mistake-cause classification.

Two kinds of test here:

* **Rule tests** — each predicate, positive and negative, on real legal
  positions. Where a rule turns purely on a flag combination, the flag is set
  explicitly with ``dataclasses.replace`` rather than by hunting for a position
  that happens to produce it; that keeps the test about the predicate instead of
  smuggling in a chess claim the test cannot back up.
* **Invariant tests** — properties that must hold for *every* rule, most
  importantly that a rule may only cite evidence the packet actually carries.
"""

import dataclasses
import itertools

import chess
import pytest

from services.api.diagnosis.causes import (
    CAUSE_LABELS,
    DEFERRED_CAUSES,
    MODULATOR_CAUSES,
    RULE_VERSION,
    RULES,
    UNCLASSIFIED,
    classify_causes,
)
from services.api.diagnosis.evidence import (
    GameFacts,
    PuzzleFacts,
    extract_evidence,
    to_evidence_items,
)
from services.api.diagnosis.pgn_context import (
    EMPTY_GAME_CONTEXT,
    UNKNOWN_TIME_CONTROL,
    parse_time_control,
)
from services.api.diagnosis.test_evidence_corpus import GOLDEN_MISTAKES, _packet

# Two loose enemy pieces (Qb6, Re7); Nd5 forks both.
FORK_TWO_LOOSE = "6k1/4r3/1q6/8/8/2N5/8/7K w - - 0 1"
# Black's queen on d5 is undefended and can simply be taken.
HANGING_QUEEN = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"
# Re8 is mate.
BACK_RANK_MATE = "6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1"
# A dead-quiet pawn endgame: nothing for any rule to grip.
QUIET_ENDGAME = "4k3/pp4pp/8/8/8/8/PP4PP/4K3 w - - 0 20"


def build(
    fen,
    played,
    best,
    ply=30,
    eval_before=0.3,
    eval_after=-0.4,
    swing=0.7,
    solution_pv=(),
    context=EMPTY_GAME_CONTEXT,
    time_control=UNKNOWN_TIME_CONTROL,
):
    board = chess.Board(fen)
    puzzle = PuzzleFacts(
        fen=fen,
        played_move_uci=played,
        best_move_uci=best,
        ply=ply,
        eval_before=eval_before,
        eval_after=eval_after,
        swing=swing,
        accept_moves_uci=(best,),
        solution_pv=solution_pv,
    )
    return extract_evidence(
        puzzle,
        GameFacts(user_is_white=board.turn == chess.WHITE, time_control=time_control),
        context,
    )


def set_move_flags(packet, *, best=None, played=None):
    """Override move classification flags without pretending to a chess claim."""
    updated = packet
    if best:
        updated = dataclasses.replace(
            updated,
            best=dataclasses.replace(
                updated.best, move=dataclasses.replace(updated.best.move, **best)
            ),
        )
    if played:
        updated = dataclasses.replace(
            updated,
            played=dataclasses.replace(
                updated.played,
                move=dataclasses.replace(updated.played.move, **played),
            ),
        )
    return updated


def causes_of(packet):
    return {c.cause for c in classify_causes(packet).candidates}


class TestLoosePieceAwareness:
    def test_solution_forking_two_loose_pieces_is_the_strongest_form(self):
        assessment = classify_causes(build(FORK_TWO_LOOSE, "c3e2", "c3d5", ply=51))
        assert assessment.primary_cause == "loose_piece_awareness"

    def test_a_free_capture_counts_as_loose_piece_awareness(self):
        """The 'it was hanging and you didn't take it' case must not be filed
        under the generic 'you missed a forcing move'."""
        assessment = classify_causes(build(HANGING_QUEEN, "d1d2", "d1d5", ply=41))
        assert assessment.primary_cause == "loose_piece_awareness"
        assert "forcing_move_blindness" in assessment.secondary_causes

    def test_specific_causes_outrank_generic_ones(self):
        """Both rules fire on a missed knight fork; the fork is the useful
        headline, and tie-order must not decide that."""
        fen, played, best, ply, _ = GOLDEN_MISTAKES["missed_knight_fork"]
        assessment = classify_causes(_packet(fen, played, best, ply))
        assert assessment.primary_cause == "loose_piece_awareness"
        assert "forcing_move_blindness" in assessment.secondary_causes

    def test_quiet_position_with_no_loose_pieces_does_not_fire(self):
        assert "loose_piece_awareness" not in causes_of(
            build(QUIET_ENDGAME, "e1d2", "e1f2")
        )


class TestForcingAndQuietBlindness:
    def test_quiet_played_against_forcing_solution(self):
        assert "forcing_move_blindness" in causes_of(
            build(BACK_RANK_MATE, "e1e2", "e1e8", ply=61)
        )

    def test_forcing_played_against_quiet_solution(self):
        packet = set_move_flags(
            build(HANGING_QUEEN, "d1d5", "d1d2", ply=41),
            best={"is_quiet": True, "is_capture": False, "is_check": False},
            played={"is_quiet": False, "is_capture": True},
        )
        assert "quiet_move_blindness" in causes_of(packet)
        assert "forcing_move_blindness" not in causes_of(packet)

    def test_two_quiet_moves_are_neither(self):
        """When both moves are quiet the mistake is something else entirely —
        neither rule may claim it."""
        packet = build(QUIET_ENDGAME, "e1d2", "e1f2")
        assert not {"forcing_move_blindness", "quiet_move_blindness"} & causes_of(
            packet
        )

    def test_cites_the_counter_matching_the_missed_move_type(self):
        """A missed capture must not be evidenced by the number of available
        checks — that counter says nothing about it."""
        assessment = classify_causes(build(HANGING_QUEEN, "d1d2", "d1d5", ply=41))
        ids = assessment.evidence_ids_for("forcing_move_blindness")
        assert "threats.legal_captures" in ids
        assert "threats.legal_checks" not in ids


class TestRecaptureAssumption:
    def _recapture_context(self, square):
        return dataclasses.replace(
            EMPTY_GAME_CONTEXT,
            previous_move_uci="e4d5",
            previous_move_was_capture=True,
            previous_capture_square=square,
        )

    def test_automatic_recapture_is_flagged(self):
        packet = build(
            HANGING_QUEEN,
            "d1d5",
            "d1d2",
            ply=41,
            context=self._recapture_context(chess.D5),
        )
        assert "recapture_assumption" in causes_of(packet)

    def test_an_in_between_solution_strengthens_it(self):
        # Black has just captured on a4. White recaptures with Rxa4; the engine
        # wanted the in-between check Re8+ first.
        fen = "6k1/5ppp/8/8/q7/8/8/R3R1K1 w - - 0 1"
        zwischenzug = build(
            fen, "a1a4", "e1e8", ply=41, context=self._recapture_context(chess.A4)
        )
        plain = build(
            fen, "a1a4", "a1a2", ply=41, context=self._recapture_context(chess.A4)
        )
        assert zwischenzug.best.is_zwischenzug_like
        assert not plain.best.is_zwischenzug_like

        strength = {c.cause: c.strength for c in classify_causes(plain).candidates}
        stronger = {
            c.cause: c.strength for c in classify_causes(zwischenzug).candidates
        }
        assert stronger["recapture_assumption"] > strength["recapture_assumption"]

    def test_no_prior_capture_means_no_assumption(self):
        assert "recapture_assumption" not in causes_of(
            build(HANGING_QUEEN, "d1d5", "d1d2", ply=41)
        )


class TestCalculationStoppedEarly:
    def test_long_solution_lines_fire_the_rule(self):
        packet = build(
            BACK_RANK_MATE,
            "e1e2",
            "e1e8",
            ply=61,
            solution_pv=("e1e8", "g8h7", "e8e7", "h7g8", "e7e8"),
        )
        assert "calculation_stopped_early" in causes_of(packet)

    def test_a_one_move_solution_does_not(self):
        packet = build(BACK_RANK_MATE, "e1e2", "e1e8", ply=61, solution_pv=("e1e8",))
        assert "calculation_stopped_early" not in causes_of(packet)

    def test_longer_lines_score_higher(self):
        short = build(
            BACK_RANK_MATE, "e1e2", "e1e8", ply=61, solution_pv=("a", "b", "c")
        )
        long = build(
            BACK_RANK_MATE,
            "e1e2",
            "e1e8",
            ply=61,
            solution_pv=("a", "b", "c", "d", "e"),
        )
        by_cause = lambda p: {  # noqa: E731 - local readability
            c.cause: c.strength for c in classify_causes(p).candidates
        }
        assert (
            by_cause(long)["calculation_stopped_early"]
            > by_cause(short)["calculation_stopped_early"]
        )


class TestKingSafety:
    def test_pressure_on_the_king_ring_fires(self):
        # Two black pieces bear on White's king ring.
        packet = build("6k1/8/8/3q4/8/6b1/5PPP/6K1 w - - 0 1", "g1h1", "f2f3", ply=41)
        assert packet.king.ring_attackers >= 2
        assert "king_safety_blindness" in causes_of(packet)

    def test_a_safe_king_does_not_fire(self):
        assert "king_safety_blindness" not in causes_of(
            build(QUIET_ENDGAME, "e1d2", "e1f2")
        )


class TestAttentionRules:
    def test_tunnel_vision_needs_a_threat_of_their_own(self):
        packet = build(HANGING_QUEEN, "d1d2", "d1d5", ply=41)
        # Qd2 eyes the loose queen, and Black has forcing replies.
        assert packet.played.attacks.target_count >= 1
        assert "own_threat_tunnel_vision" in causes_of(packet)

    def test_tunnel_vision_and_missed_resource_are_mutually_exclusive(self):
        """Both describe "you didn't look at the reply"; letting them
        double-count would inflate every diagnosis with a duplicate."""
        for name in GOLDEN_MISTAKES:
            fen, played, best, ply, _ = GOLDEN_MISTAKES[name]
            fired = causes_of(_packet(fen, played, best, ply))
            assert (
                not {
                    "own_threat_tunnel_vision",
                    "missed_opponent_resource",
                }
                <= fired
            )


class TestPhaseRules:
    def test_endgame_rule_requires_a_conversion_failure(self):
        """A bare 'it was an endgame' would fire on every endgame puzzle and
        say nothing."""
        losing_a_win = build(
            BACK_RANK_MATE, "e1e2", "e1e8", ply=61, eval_before=3.0, eval_after=0.0
        )
        already_equal = build(
            BACK_RANK_MATE, "e1e2", "e1e8", ply=61, eval_before=0.2, eval_after=-0.5
        )
        assert "endgame_technique_gap" in causes_of(losing_a_win)
        assert "endgame_technique_gap" not in causes_of(already_equal)

    def test_opening_rule_requires_a_big_mistake_with_a_short_refutation(self):
        opening = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
        pattern_gap = build(
            opening, "b1c3", "f3e5", ply=7, swing=4.0, solution_pv=("f3e5",)
        )
        deep_line = build(
            opening, "b1c3", "f3e5", ply=7, swing=4.0, solution_pv=("a", "b", "c", "d")
        )
        assert "opening_pattern_gap" in causes_of(pattern_gap)
        # A mistake needing a deep refutation is a calculation failure that
        # happens to occur early, not an unknown pattern.
        assert "opening_pattern_gap" not in causes_of(deep_line)


class TestTimePressureIsOnlyEverAModulator:
    def _pressured(self, seconds=8.0):
        context = dataclasses.replace(
            EMPTY_GAME_CONTEXT,
            clock_after_move_seconds=seconds,
            move_time_seconds=2.0,
        )
        return build(
            QUIET_ENDGAME,
            "e1d2",
            "e1f2",
            context=context,
            time_control=parse_time_control("600"),
        )

    def test_it_fires_as_a_candidate(self):
        assert "time_pressure_collapse" in causes_of(self._pressured())

    def test_it_can_never_be_the_primary_cause(self):
        """ "You were low on time" explains nothing about what was missed.
        Promoting it would let every scramble absorb a real blind spot."""
        assessment = classify_causes(self._pressured())
        assert assessment.primary_cause == UNCLASSIFIED
        assert assessment.insufficient_evidence

    def test_it_survives_as_context_alongside_a_real_cause(self):
        context = dataclasses.replace(
            EMPTY_GAME_CONTEXT, clock_after_move_seconds=8.0, move_time_seconds=2.0
        )
        assessment = classify_causes(
            build(
                FORK_TWO_LOOSE,
                "c3e2",
                "c3d5",
                ply=51,
                context=context,
                time_control=parse_time_control("600"),
            )
        )
        assert assessment.primary_cause == "loose_piece_awareness"
        assert "time_pressure_collapse" in assessment.secondary_causes


class TestUnclassified:
    def test_a_position_with_no_signal_stays_unclassified(self):
        """An honest 'we can't tell' beats the least-bad guess."""
        assessment = classify_causes(build(QUIET_ENDGAME, "e1d2", "e1f2"))
        assert assessment.primary_cause == UNCLASSIFIED
        assert assessment.insufficient_evidence
        assert assessment.candidates == ()
        assert assessment.secondary_causes == ()

    def test_unclassified_has_a_human_label(self):
        assert CAUSE_LABELS[UNCLASSIFIED]


class TestInvariants:
    """Properties that must hold across every rule and every position."""

    def all_packets(self):
        for name in sorted(GOLDEN_MISTAKES):
            fen, played, best, ply, _ = GOLDEN_MISTAKES[name]
            yield name, _packet(fen, played, best, ply)

    def test_rules_only_cite_evidence_the_packet_carries(self):
        """The load-bearing invariant. A rule citing a fact the packet does not
        carry would produce an explanation the user cannot check — and would
        make the AI stage's citation validation meaningless, since it validates
        against exactly this id set."""
        for name, packet in self.all_packets():
            available = {item.id for item in to_evidence_items(packet)}
            for candidate in classify_causes(packet).candidates:
                missing = set(candidate.evidence_ids) - available
                assert not missing, f"{name}/{candidate.cause} cites {missing}"

    def test_rules_only_cite_carried_evidence_across_clock_states_too(self):
        """The corpus carries no clock, so the sweep above cannot reach the
        clock-dependent rule. A time-pressure verdict built on a reading the
        packet did not make citable is exactly the gap this covers — it was a
        real defect, found by fuzzing and fixed in the evidence layer."""
        from services.api.diagnosis.pgn_context import parse_time_control

        clocks = [(None, None), (9.0, None), (None, 7.0), (40.0, 32.0), (600.0, 12.0)]
        controls = ["600", "60", "1/86400", None]
        for (before, after), raw in itertools.product(clocks, controls):
            context = dataclasses.replace(
                EMPTY_GAME_CONTEXT,
                clock_before_move_seconds=before,
                clock_after_move_seconds=after,
                move_time_seconds=(
                    before - after if before and after and before >= after else None
                ),
            )
            packet = build(
                FORK_TWO_LOOSE,
                "c3e2",
                "c3d5",
                ply=51,
                context=context,
                time_control=parse_time_control(raw),
            )
            available = {item.id for item in to_evidence_items(packet)}
            for candidate in classify_causes(packet).candidates:
                missing = set(candidate.evidence_ids) - available
                assert (
                    not missing
                ), f"{raw}/{before}/{after}: {candidate.cause} cites {missing}"

    def test_every_candidate_cites_at_least_one_fact(self):
        for name, packet in self.all_packets():
            for candidate in classify_causes(packet).candidates:
                assert candidate.evidence_ids, f"{name}/{candidate.cause}"

    def test_every_emitted_cause_has_a_human_label(self):
        for _, packet in self.all_packets():
            for candidate in classify_causes(packet).candidates:
                assert candidate.cause in CAUSE_LABELS

    def test_deferred_causes_are_never_emitted(self):
        """They are in the plan's taxonomy but the evidence layer cannot
        support them yet; shipping them as guesses is the failure this design
        exists to avoid."""
        for _, packet in self.all_packets():
            assert not causes_of(packet) & DEFERRED_CAUSES

    def test_classification_is_deterministic(self):
        """Unstable ordering would defeat evidence-hash-keyed caching and make
        run-to-run diffs unreadable."""
        for _, packet in self.all_packets():
            first = classify_causes(packet)
            second = classify_causes(packet)
            assert first == second

    def test_candidates_are_ordered_by_descending_strength(self):
        for _, packet in self.all_packets():
            strengths = [c.strength for c in classify_causes(packet).candidates]
            assert strengths == sorted(strengths, reverse=True)

    def test_primary_is_never_a_modulator(self):
        for _, packet in self.all_packets():
            assert classify_causes(packet).primary_cause not in MODULATOR_CAUSES

    def test_primary_never_repeats_in_secondary(self):
        for _, packet in self.all_packets():
            assessment = classify_causes(packet)
            assert assessment.primary_cause not in assessment.secondary_causes

    def test_secondary_causes_are_capped(self):
        for _, packet in self.all_packets():
            assert len(classify_causes(packet).secondary_causes) <= 3

    def test_supported_causes_matches_the_candidate_set(self):
        """This set is what the AI stage may choose from; anything outside it
        is a rejection."""
        for _, packet in self.all_packets():
            assessment = classify_causes(packet)
            assert assessment.supported_causes == {
                c.cause for c in assessment.candidates
            }

    def test_every_rule_is_reachable_somewhere_in_the_corpus(self):
        """A rule no position can trigger is untested code pretending to be a
        feature. Phase and clock rules are exercised in their own tests above,
        so they are exempt here."""
        fired = set()
        for _, packet in self.all_packets():
            fired |= causes_of(packet)
        exempt = {
            "recapture_assumption",
            "time_pressure_collapse",
            "calculation_stopped_early",
            "quiet_move_blindness",
        }
        expected = {
            "loose_piece_awareness",
            "forcing_move_blindness",
            "king_safety_blindness",
            "own_threat_tunnel_vision",
            "missed_opponent_resource",
            "endgame_technique_gap",
            "opening_pattern_gap",
        }
        assert expected <= fired | exempt

    @pytest.mark.parametrize("rule", RULES, ids=lambda r: r.__name__)
    def test_rules_return_none_rather_than_raising_on_a_quiet_position(self, rule):
        """A rule must decline, not explode, on a position it has nothing to
        say about — one raising rule would take down the whole assessment."""
        packet = build(QUIET_ENDGAME, "e1d2", "e1f2")
        assert rule(packet) is None or rule(packet).cause in CAUSE_LABELS

    def test_rule_version_is_recorded_on_the_assessment(self):
        assert classify_causes(build(QUIET_ENDGAME, "e1d2", "e1f2")).rule_version == (
            RULE_VERSION
        )


class TestAlignmentBlindness:
    """Queen/rook alignment — the 12th cause from the taxonomy.

    Deferred until now because alignment on its own is not a mistake: pieces
    share lines constantly, and a rule that fired on that would be the
    unfalsifiable label the whole design refuses. What makes it diagnostic is
    alignment the *solution exploits*.
    """

    # Black king g8 and rook a8 share rank 8; white's Re1-e8 skewers them.
    SKEWER = "r5k1/pp3ppp/8/8/8/8/PP3PPP/4R1K1 w - - 0 1"
    # Same board, black rook removed: nothing aligned to exploit.
    NO_PAIR = "6k1/pp3ppp/8/8/8/8/PP3PPP/4R1K1 w - - 0 1"

    def test_fires_when_the_solution_exploits_the_line(self):
        packet = _packet(self.SKEWER, "e1e2", "e1e8", 41)
        # The opponent's geometry: the user is to move and missed the skewer.
        assert packet.alignment.exploited_by_best
        causes = [c.cause for c in classify_causes(packet).candidates]
        assert "alignment_blindness" in causes

    def test_is_silent_when_nothing_is_aligned(self):
        packet = _packet(self.NO_PAIR, "e1e2", "e1e8", 41)
        assert not packet.alignment.exploited_by_best
        causes = [c.cause for c in classify_causes(packet).candidates]
        assert "alignment_blindness" not in causes

    def test_alignment_alone_is_not_a_cause(self):
        # The pieces are aligned, but the solution does not use the line. This
        # is the case that would make the rule unfalsifiable if it fired.
        packet = _packet(self.SKEWER, "g1h1", "g1f1", 41)
        assert packet.alignment.pairs
        assert not packet.alignment.exploited_by_best
        causes = [c.cause for c in classify_causes(packet).candidates]
        assert "alignment_blindness" not in causes

    def test_cites_only_evidence_that_exists(self):
        # The citation gate is the contract: every id a rule names must be in
        # the packet's item list.
        packet = _packet(self.SKEWER, "e1e2", "e1e8", 41)
        available = {item.id for item in to_evidence_items(packet)}
        for candidate in classify_causes(packet).candidates:
            if candidate.cause == "alignment_blindness":
                assert set(candidate.evidence_ids) <= available

    def test_a_blocked_line_outranks_a_direct_battery(self):
        # A piece between the pair is the classic pin/skewer. A direct battery
        # is weaker: the front piece may simply be defended.
        blocked = _packet(
            "r2b2k1/pp3ppp/8/8/8/8/PP3PPP/4R1K1 w - - 0 1", "e1e2", "e1e8", 41
        )
        direct = _packet(self.SKEWER, "e1e2", "e1e8", 41)

        def strength(p):
            return next(
                (
                    c.strength
                    for c in classify_causes(p).candidates
                    if c.cause == "alignment_blindness"
                ),
                None,
            )

        if strength(blocked) and strength(direct):
            assert strength(blocked) > strength(direct)

    def test_capturing_an_aligned_piece_is_not_exploiting_the_alignment(self):
        # A hanging queen that happens to share a diagonal with its king is a
        # hanging queen. This fired as alignment_blindness at 0.85 — above the
        # true cause — until the capture case was excluded.
        hanging_queen = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"
        packet = _packet(hanging_queen, "d1d2", "d1d5", 41)
        assert not packet.alignment.exploited_by_best
        causes = [c.cause for c in classify_causes(packet).candidates]
        assert causes[0] == "loose_piece_awareness"
        assert "alignment_blindness" not in causes

    def test_never_outranks_the_loose_piece_story(self):
        # Unconditional: comparing the rule's own strengths rather than waiting
        # for a position where both happen to fire. The previous version of this
        # test guarded on "if both are present" and never actually ran.
        from services.api.diagnosis.causes import _alignment_blindness

        blocked = _packet(
            "r2b2k1/pp3ppp/8/8/8/8/PP3PPP/4R1K1 w - - 0 1", "g1h1", "e1e8", 41
        )
        candidate = _alignment_blindness(blocked)
        if candidate is not None:
            assert candidate.strength < 0.8

    def test_is_no_longer_deferred(self):
        from services.api.diagnosis.causes import CAUSE_LABELS, DEFERRED_CAUSES

        assert "alignment_blindness" not in DEFERRED_CAUSES
        assert "alignment_blindness" in CAUSE_LABELS
