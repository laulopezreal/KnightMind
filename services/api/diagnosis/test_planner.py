"""Tests for the training planner — what it recommends, and what it refuses to."""

from services.api.diagnosis.planner import plan_focus
from services.api.storage.diagnosis_repository import CauseStat


def stat(
    cause: str = "loose_piece_awareness",
    mistakes: int = 10,
    dominant_phase: str | None = "middlegame",
    verified_attempts: int = 12,
    verified_puzzles: int = 5,
    verified_passes: int = 5,
    accuracy: float | None = 0.4,
    recent_mistakes: int = 4,
    insufficient_data: bool = False,
) -> CauseStat:
    return CauseStat(
        cause=cause,
        mistakes=mistakes,
        dominant_phase=dominant_phase,
        verified_attempts=verified_attempts,
        verified_puzzles=verified_puzzles,
        verified_passes=verified_passes,
        accuracy=accuracy,
        recent_mistakes=recent_mistakes,
        insufficient_data=insufficient_data,
    )


class TestRefusesToGuess:
    def test_recommends_nothing_when_no_pattern_is_established(self):
        # Below the threshold a count is not a tendency, so there is nothing to
        # build a plan on. Returning the least-bad option would be the exact
        # overreach the rest of this feature avoids.
        assert plan_focus([stat(mistakes=2, insufficient_data=True)]) is None

    def test_recommends_nothing_for_an_empty_corpus(self):
        assert plan_focus([]) is None

    def test_skips_unclassified_however_common_it_is(self):
        # "We could not work out why" is the most frequent bucket for many
        # users. It is coverage information, not a habit to train.
        focus = plan_focus(
            [
                stat(cause="unclassified", mistakes=50, recent_mistakes=30),
                stat(cause="loose_piece_awareness", mistakes=6),
            ]
        )
        assert focus is not None
        assert focus.cause == "loose_piece_awareness"

    def test_skips_a_cause_with_no_written_pattern(self):
        # A cause can outrank everything and still have no coaching text. The
        # planner points at named patterns only rather than inventing one.
        assert plan_focus([stat(cause="a_cause_nobody_wrote_yet", mistakes=99)]) is None

    def test_ignores_thin_causes_even_when_they_outrank(self):
        # Raw frequency is not the tiebreaker; eligibility comes first.
        focus = plan_focus(
            [
                stat(
                    cause="king_safety_blindness", mistakes=40, insufficient_data=True
                ),
                stat(cause="loose_piece_awareness", mistakes=5),
            ]
        )
        assert focus is not None
        assert focus.cause == "loose_piece_awareness"


class TestPicksTheRightOne:
    def test_prefers_the_higher_priority_pattern(self):
        focus = plan_focus(
            [
                stat(cause="loose_piece_awareness", mistakes=5, recent_mistakes=0),
                stat(cause="king_safety_blindness", mistakes=20, recent_mistakes=15),
            ]
        )
        assert focus is not None
        assert focus.cause == "king_safety_blindness"

    def test_is_stable_across_calls(self):
        # A recommendation that changed on refresh would read as arbitrary.
        stats = [
            stat(cause="loose_piece_awareness", mistakes=8, accuracy=0.5),
            stat(cause="king_safety_blindness", mistakes=8, accuracy=0.5),
        ]
        first = plan_focus(stats)
        second = plan_focus(list(reversed(stats)))
        assert first is not None and second is not None
        assert first.cause == second.cause

    def test_names_the_runner_up(self):
        focus = plan_focus(
            [
                stat(cause="king_safety_blindness", mistakes=20, recent_mistakes=15),
                stat(cause="loose_piece_awareness", mistakes=5, recent_mistakes=0),
            ]
        )
        assert focus is not None
        assert focus.runner_up == "Loose Piece Syndrome"

    def test_has_no_runner_up_when_there_is_only_one_pattern(self):
        focus = plan_focus([stat()])
        assert focus is not None
        assert focus.runner_up is None

    def test_carries_the_pattern_name_not_the_slug(self):
        focus = plan_focus([stat(cause="loose_piece_awareness")])
        assert focus is not None
        assert focus.name == "Loose Piece Syndrome"
        assert focus.cause == "loose_piece_awareness"


class TestRationale:
    def test_quotes_the_numbers_the_choice_rests_on(self):
        focus = plan_focus([stat(mistakes=11, recent_mistakes=4, accuracy=0.25)])
        assert focus is not None
        assert "11 diagnosed mistakes" in focus.rationale
        assert "4 of them recent" in focus.rationale
        assert "25% solved when retried" in focus.rationale

    def test_says_a_rate_is_unmeasured_rather_than_omitting_it(self):
        # Silence would let the user assume a rate was measured and merely left
        # out, which is the same failure the cause cards guard against.
        focus = plan_focus([stat(accuracy=None)])
        assert focus is not None
        assert "not enough verified retries" in focus.rationale
        assert "%" not in focus.rationale

    def test_never_claims_recent_activity_there_is_none_of(self):
        focus = plan_focus([stat(recent_mistakes=0)])
        assert focus is not None
        assert "recent" not in focus.rationale

    def test_omits_the_phase_when_none_dominates(self):
        focus = plan_focus([stat(dominant_phase=None)])
        assert focus is not None
        assert "mostly in the" not in focus.rationale

    def test_reports_a_measured_zero_rate_as_zero(self):
        # Distinct from an unmeasured rate: this one is a finding.
        focus = plan_focus([stat(accuracy=0.0)])
        assert focus is not None
        assert "0% solved when retried" in focus.rationale
