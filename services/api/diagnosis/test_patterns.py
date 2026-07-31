"""Tests for the pattern naming and prioritisation layer."""

import pytest

from services.api.diagnosis.causes import CAUSE_LABELS, MODULATOR_CAUSES, UNCLASSIFIED
from services.api.diagnosis.patterns import _PATTERNS, identify, priority_score


class TestNaming:
    def test_names_a_known_cause(self):
        identity = identify("loose_piece_awareness")
        assert identity.name == "Loose Piece Syndrome"
        assert "undefended" in identity.description

    def test_a_phase_specific_name_wins_where_one_exists(self):
        general = identify("king_safety_blindness", "middlegame")
        endgame = identify("king_safety_blindness", "endgame")
        assert general.name == "King Safety Blind Spot"
        assert endgame.name == "Back Rank Neglect"

    def test_an_unknown_phase_falls_back_to_the_general_name(self):
        """A phase the table has no entry for must not leave a cause unnamed."""
        assert identify("loose_piece_awareness", "zugzwang").name == (
            "Loose Piece Syndrome"
        )

    def test_unclassified_is_never_given_a_name(self):
        """ "We could not work out why" is an honest state, not a habit with a
        catchy name. Dressing it up as one is the overreach this avoids."""
        assert identify(UNCLASSIFIED) is None
        assert identify(UNCLASSIFIED, "middlegame") is None

    def test_an_unwritten_cause_returns_nothing_rather_than_improvising(self):
        assert identify("some_future_cause") is None

    def test_every_emittable_cause_has_a_pattern(self):
        """A cause the rules can produce but that has no written pattern would
        silently vanish from the patterns view."""
        named = {cause for cause, _ in _PATTERNS}
        missing = set(CAUSE_LABELS) - named - {UNCLASSIFIED}
        assert not missing, f"causes with no pattern written: {sorted(missing)}"

    def test_modulators_are_named_too(self):
        """time_pressure_collapse never leads a diagnosis, but it can still be
        the dominant cause across a corpus."""
        for cause in MODULATOR_CAUSES:
            assert identify(cause) is not None

    def test_descriptions_address_the_player_and_describe_a_habit(self):
        for (cause, _), (name, description) in _PATTERNS.items():
            assert name[0].isupper(), cause
            assert "you" in description.lower(), cause
            assert description.endswith("."), cause


class TestPriority:
    def test_more_mistakes_outrank_fewer(self):
        assert priority_score(10, 0.5, 0) > priority_score(3, 0.5, 0)

    def test_a_pattern_you_keep_failing_outranks_one_you_solve(self):
        """Difficulty is the point: a habit you already fix on retry needs less
        attention than one you keep repeating."""
        assert priority_score(10, 0.1, 0) > priority_score(10, 0.9, 0)

    def test_unknown_accuracy_sits_between_the_extremes(self):
        """An untested pattern must neither jump the queue nor get buried."""
        unknown = priority_score(10, None, 0)
        assert priority_score(10, 0.9, 0) < unknown < priority_score(10, 0.1, 0)

    def test_recent_occurrences_raise_priority(self):
        assert priority_score(10, 0.5, 8) > priority_score(10, 0.5, 0)

    def test_all_recent_is_capped_at_double(self):
        """Recency is a multiplier on a bounded ratio, so it cannot swamp the
        other two factors however lopsided the window is."""
        assert priority_score(10, 0.5, 10) == pytest.approx(
            priority_score(10, 0.5, 0) * 2
        )

    def test_zero_mistakes_does_not_divide_by_zero(self):
        assert priority_score(0, None, 0) == 0.0
