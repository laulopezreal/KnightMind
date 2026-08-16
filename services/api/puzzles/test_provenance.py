"""Provenance is the label served *before* an attempt, so its contract is
mostly about what it must never contain and never omit.

Two properties carry the design (``docs/design-puzzle-identity-and-modes.md``
§3): it is answerable at insert time from date + move alone, and it never
depends on the opening, which arrives asynchronously or not at all.
"""

from datetime import datetime, timezone

import pytest

from services.api.puzzles.provenance import (
    compose_provenance,
    resolve_display_name,
)

# 2026-03-12 14:30 UTC.
MAR_12 = int(datetime(2026, 3, 12, 14, 30, tzinfo=timezone.utc).timestamp())


class TestComposeProvenance:
    def test_all_three_components(self):
        assert (
            compose_provenance(end_time=MAR_12, ply=35, opening_name="Sicilian")
            == "12 Mar · Sicilian · move 18"
        )

    def test_without_an_opening(self):
        """The common case for a freshly generated puzzle: diagnosis is
        asynchronous, so there is no opening row yet."""
        assert compose_provenance(end_time=MAR_12, ply=35) == "12 Mar · move 18"

    @pytest.mark.parametrize("missing", [None, 0])
    def test_a_manual_puzzle_is_not_labelled_1_jan_1970(self, missing):
        """`POST /puzzles/manual` inserts a synthetic game with end_time=0.
        Formatting that blindly labels every manual puzzle with the epoch."""
        result = compose_provenance(end_time=missing, ply=35)

        assert result == "move 18"
        assert "1970" not in result
        assert "Jan" not in result

    def test_a_corrupt_timestamp_degrades_instead_of_raising(self):
        """A bad row must not 500 an entire puzzle listing."""
        assert compose_provenance(end_time=10**18, ply=35) == "move 18"

    def test_it_is_never_empty(self):
        """It is the fallback for a puzzle with no nickname, so an empty string
        would render as a blank row in the library."""
        assert compose_provenance(end_time=None, ply=None) == "move 1"

    @pytest.mark.parametrize(
        ("ply", "expected"),
        [(0, "move 1"), (1, "move 1"), (2, "move 2"), (35, "move 18"), (36, "move 19")],
    )
    def test_move_number_follows_chess_convention(self, ply, expected):
        """Ply 0 and 1 are both move 1 -- White then Black. This matches
        identity.py and spaced_repetition.py, which compute it the same way."""
        assert compose_provenance(end_time=None, ply=ply) == expected

    def test_the_day_has_no_leading_zero(self):
        march_2 = int(datetime(2026, 3, 2, tzinfo=timezone.utc).timestamp())

        assert compose_provenance(end_time=march_2, ply=0) == "2 Mar · move 1"

    def test_an_opening_is_passed_through_as_stored(self):
        assert (
            compose_provenance(
                end_time=None, ply=0, opening_name="Nimzowitsch Defense B00"
            )
            == "Nimzowitsch Defense B00 · move 1"
        )

    def test_a_blank_opening_is_dropped_not_rendered(self):
        assert compose_provenance(end_time=None, ply=0, opening_name="   ") == "move 1"


class TestResolveDisplayName:
    def test_a_nickname_wins(self):
        """Step 1 must change nothing visible: every production row has a title
        today, so display_name equals it everywhere."""
        assert (
            resolve_display_name(
                title="Bishop Had Bigger Plans", end_time=MAR_12, ply=35
            )
            == "Bishop Had Bigger Plans"
        )

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_provenance_fills_in_when_there_is_no_nickname(self, empty):
        """NULL is the steady state after rollout step 6, and a whitespace-only
        title would otherwise render as an invisible name."""
        assert (
            resolve_display_name(title=empty, end_time=MAR_12, ply=35)
            == "12 Mar · move 18"
        )

    def test_it_does_not_gate_yet(self):
        """Deliberate: withholding a nickname until the puzzle is resolved is
        rollout step 3. Step 1 only routes every surface through this function
        so that change lands in one place instead of six. If this test starts
        failing, step 3 arrived and this expectation should move with it."""
        assert (
            resolve_display_name(title="Spoiler Name", end_time=MAR_12, ply=35)
            == "Spoiler Name"
        )
