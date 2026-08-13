"""The deterministic namer: does it actually distinguish puzzles?

The bug this module replaces was not "the names are ugly", it was "the names do
not vary" — 150 puzzles called ``The Missed Win``. So the load-bearing test here
is the distinctness one, not the per-template ones.
"""

from typing import Any

import chess

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
        "played_move_uci": "f3g5",
        "primary_motif": "blunder",
        "move_number": 12,
    }
    base.update(kw)
    return PositionFacts(**base)


def test_the_name_never_carries_the_answer_square():
    """The whole reason this composer changed.

    It used to build every template from ``best_move_uci``, so the fallback
    name held the winning move's destination — "The h1 Pin", "The Queen to h1"
    on real rows. That is the one thing the model's own names are gated
    against, and this is the branch that runs on EVERY puzzle at creation.
    """
    # Played g5; the engine's move (and the answer) lands on g4.
    name = compose_position_name(facts(played_move_uci="f3g5"))
    assert "g5" in name
    assert "g4" not in name


def test_the_name_describes_the_move_the_player_made():
    assert compose_position_name(facts()) == "Knight to g5"


def test_a_capture_names_what_was_taken():
    """g4 holds the black knight, so f3g4 is a capture."""
    assert compose_position_name(facts(played_move_uci="f3g4")) == (
        "Knight Takes Knight on g4"
    )


def test_the_motif_never_appears_in_the_name():
    """The motif describes the SOLUTION. Pairing it with the played move's
    square would be misleading — "The h6 Pin" when the pin is elsewhere — as
    well as a hint about a tactic the player has not found yet."""
    for motif in ("fork", "pin", "back_rank", "mate_threat", "hanging_queen"):
        name = compose_position_name(facts(primary_motif=motif))
        assert name == "Knight to g5", name


def test_names_still_vary_by_move():
    """The regression that started all this: 150 puzzles, one name."""
    names = {
        compose_position_name(facts(played_move_uci=uci))
        for uci in ("f3g5", "f3e5", "f3d4", "f3h4", "f3g1")
    }
    assert len(names) == 5


def test_unreadable_position_falls_back_to_the_motif_title():
    """Total, not raising: one malformed row must not fail a whole backfill."""
    assert compose_position_name(facts(fen="not a fen")) == "The Missed Win"
    assert compose_position_name(facts(played_move_uci="zzzz")) == "The Missed Win"


def test_no_identity_can_reach_the_name():
    """PositionFacts has no field an opponent handle could occupy."""
    assert not {"username", "opponent", "opponent_username"} & set(
        PositionFacts.__dataclass_fields__
    )


def test_the_composer_cannot_see_the_solution_at_all():
    """Structural, not a rule to remember: there is no field to leak from."""
    assert "best_move_uci" not in PositionFacts.__dataclass_fields__


def test_names_stay_inside_the_card_budget():
    from services.api.puzzles.position_names import MAX_NAME_CHARS

    for motif in ("fork", "pin", "hanging_piece", "back_rank", "mate_threat", None):
        for uci in ("f3g5", "f3g4"):
            name = compose_position_name(
                facts(primary_motif=motif, played_move_uci=uci)
            )
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


# --- the played move can land where the winning move lands -------------------


def test_a_played_move_landing_on_the_answer_square_names_the_origin():
    """Naming from the played move is not sufficient on its own.

    The two moves differ, but their DESTINATIONS coincide on 17 of 348 live
    puzzles (4.9%) — recaptures, and wrong-piece-right-square. The earlier fix
    verified whole-move inequality, which is the wrong invariant: it left
    titles like "Queen to h1" for a puzzle whose answer lands on h1, which is
    the exact title class the change was made to stop producing.
    """
    # Played Nf3xg4; the winning move also lands on g4.
    name = compose_position_name(facts(played_move_uci="f3g4", answer_square="g4"))

    assert "g4" not in name
    assert name == "Knight Left f3"


def test_the_origin_square_is_still_distinguishing():
    """The fallback must not collapse every such puzzle onto one phrase —
    that would recreate the duplicate-names bug in miniature."""
    names = {
        compose_position_name(facts(played_move_uci=uci, answer_square=uci[2:4]))
        for uci in ("f3g4", "b1c3", "d1d4")
    }
    assert len(names) == 3


def test_an_unrelated_answer_square_does_not_change_the_name():
    """The guard fires only on a genuine collision."""
    assert compose_position_name(
        facts(played_move_uci="f3g5", answer_square="a1")
    ) == compose_position_name(facts(played_move_uci="f3g5"))


def test_answer_square_of_reads_the_destination():
    from services.api.puzzles.position_names import answer_square_of

    assert answer_square_of("g1f3") == "f3"
    assert answer_square_of("e7e8q") == "e8"  # promotion carries a suffix
    assert answer_square_of(None) is None
    assert answer_square_of("xx") is None
    assert answer_square_of("zzzz") is None
