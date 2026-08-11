"""The deterministic namer: does it actually distinguish puzzles?

The bug this module replaces was not "the names are ugly", it was "the names do
not vary" — 150 puzzles called ``The Missed Win``. So the load-bearing test here
is the distinctness one, not the per-template ones.
"""

from typing import Any

import chess
import pytest

from services.api.puzzles.position_names import (
    PositionFacts,
    compose_position_name,
    disambiguate,
)

START = chess.STARTING_FEN
# Black just played ...Ng4?? and White can win the knight; a real position with
# a capture available, used wherever a capture needs to exist.
CAPTURE_FEN = "rnbqkb1r/pppppppp/8/8/6n1/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 3"


def facts(**kw) -> PositionFacts:
    # Annotated dict[str, Any]: a bare literal infers dict[str, object],
    # and splatting that into the dataclass is a mypy arg-type error.
    base: dict[str, Any] = {
        "fen": CAPTURE_FEN,
        "best_move_uci": "f3g5",
        "primary_motif": "blunder",
        "move_number": 12,
    }
    base.update(kw)
    return PositionFacts(**base)


def test_name_includes_the_target_square():
    """The square is what makes two puzzles of the same motif different."""
    assert "g5" in compose_position_name(facts())


@pytest.mark.parametrize(
    "motif,expected_fragment",
    [
        ("fork", "Fork"),
        ("pin", "Pin"),
        ("back_rank", "Back Rank"),
        ("mate_threat", "Mate"),
    ],
)
def test_motif_chooses_the_template(motif, expected_fragment):
    name = compose_position_name(facts(primary_motif=motif))
    assert expected_fragment in name
    assert "g5" in name


def test_fork_names_the_piece_that_forks():
    # f3 holds a knight in CAPTURE_FEN.
    assert compose_position_name(facts(primary_motif="fork")) == "The g5 Knight Fork"


def test_capture_names_the_captured_piece():
    """g4 holds the black knight, so f3g4 is a capture."""
    name = compose_position_name(facts(best_move_uci="f3g4"))
    assert name == "The Knight on g4"


def test_default_motif_still_varies_by_square():
    """The regression that started all this: 150 puzzles, one name.

    Every one of these is the fallback motif — the case that used to collapse
    onto ``The Missed Win``.
    """
    names = {
        compose_position_name(facts(best_move_uci=uci))
        for uci in ("f3g5", "f3e5", "f3d4", "f3h4", "f3g1")
    }
    assert len(names) == 5


def test_unreadable_position_falls_back_to_the_motif_title():
    """Total, not raising: one malformed row must not fail a whole backfill."""
    assert compose_position_name(facts(fen="not a fen")) == "The Missed Win"
    assert compose_position_name(facts(best_move_uci="zzzz")) == "The Missed Win"


def test_no_identity_can_reach_the_name():
    """PositionFacts has no field an opponent handle could occupy."""
    assert not {"username", "opponent", "opponent_username"} & set(
        PositionFacts.__dataclass_fields__
    )


def test_names_stay_inside_the_card_budget():
    from services.api.puzzles.position_names import MAX_NAME_CHARS

    for motif in ("fork", "pin", "hanging_piece", "back_rank", "mate_threat", None):
        for uci in ("f3g5", "f3g4"):
            name = compose_position_name(facts(primary_motif=motif, best_move_uci=uci))
            assert 0 < len(name) <= MAX_NAME_CHARS, name


class TestDisambiguate:
    def test_leaves_a_free_name_alone(self):
        assert disambiguate("The g5 Pin", set(), 12) == "The g5 Pin"

    def test_adds_the_move_number_on_a_collision(self):
        assert disambiguate("The g5 Pin", {"The g5 Pin"}, 12) == "The g5 Pin, move 12"

    def test_counts_up_when_the_move_number_is_taken_too(self):
        used = {"The g5 Pin", "The g5 Pin, move 12"}
        assert disambiguate("The g5 Pin", used, 12) == "The g5 Pin (2)"

    def test_counts_past_an_existing_counter(self):
        used = {"A", "A, move 3", "A (2)", "A (3)"}
        assert disambiguate("A", used, 3) == "A (4)"

    def test_works_without_a_move_number(self):
        assert disambiguate("A", {"A"}, None) == "A (2)"

    def test_never_exceeds_the_card_budget(self):
        """An AI name may sit exactly at the cap; a suffix must not push past."""
        from services.api.puzzles.position_names import MAX_NAME_CHARS

        at_cap = "N" * MAX_NAME_CHARS
        assert len(disambiguate(at_cap, set(), 199)) <= MAX_NAME_CHARS
        assert len(disambiguate(at_cap, {at_cap}, 199)) <= MAX_NAME_CHARS
        used = {at_cap, disambiguate(at_cap, {at_cap}, 199)}
        assert len(disambiguate(at_cap, used, 199)) <= MAX_NAME_CHARS

    def test_an_over_long_name_is_clamped_even_without_a_collision(self):
        from services.api.puzzles.position_names import MAX_NAME_CHARS

        assert len(disambiguate("N" * 100, set(), 4)) == MAX_NAME_CHARS

    def test_suffixed_names_stay_distinct_after_clamping(self):
        """Trimming the base must not collapse two names back into one."""
        from services.api.puzzles.position_names import MAX_NAME_CHARS

        at_cap = "N" * MAX_NAME_CHARS
        used: set[str] = set()
        for move in (11, 12, 13):
            name = disambiguate(at_cap, used, move)
            assert name not in used
            used.add(name)
        assert len(used) == 3
