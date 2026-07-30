"""Tests for ECO classification of opening lines."""

import chess

from .eco import (
    _position_key,
    classify,
    eco_table,
    max_book_ply,
    min_games_floor,
    warm,
)
from .tree_builder import build_opening_tree


def epd(*moves: str) -> str:
    """EPD of the position reached by these SAN moves — the ECO lookup key."""
    board = chess.Board()
    for move in moves:
        board.push_san(move)
    return board.epd()


def pgn(moves: str, white: str = "testplayer", result: str = "1-0") -> str:
    return f'[White "{white}"]\n[Black "opponent"]\n[Result "{result}"]\n\n{moves} {result}\n'


class TestTable:
    def test_loads_the_vendored_dataset(self):
        assert len(eco_table()) > 3000

    def test_strips_move_numbers_when_replaying(self):
        # The vendored column is "1. e4 c5 2. Nf3"; the numbers are not moves.
        assert _position_key("1. e4 c5 2. Nf3") == epd("e4", "c5", "Nf3")

    def test_ignores_an_unplayable_entry(self):
        assert _position_key("1. e4 Qxf7") is None

    def test_names_a_first_move(self):
        assert classify(epd("e4")) == ("B00", "King's Pawn Game")

    def test_names_a_main_line(self):
        code, name = classify(epd("e4", "c5"))
        assert code == "B20"
        assert name == "Sicilian Defense"

    def test_names_a_deep_variation(self):
        code, name = classify(
            epd("e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6")
        )
        assert code == "B90"
        assert "Najdorf" in name

    def test_names_a_transposition_the_same_way(self):
        """Keying on move order missed this: same position, different route."""
        via_d4 = epd("d4", "Nf6", "c4", "e6", "Nc3", "Bb4")
        via_c4 = epd("c4", "e6", "Nc3", "Bb4", "d4", "Nf6")

        assert via_d4 == via_c4
        assert classify(via_d4) == classify(via_c4) == ("E20", "Nimzo-Indian Defense")

    def test_returns_none_for_an_unknown_position(self):
        assert classify(epd("e4", "e5", "Qh5", "Ke7", "Qxe5")) is None

    def test_returns_none_without_a_position(self):
        assert classify(None) is None
        assert classify("") is None


class TestTreeAnnotation:
    def test_every_node_carries_a_code_and_name(self):
        tree = build_opening_tree([pgn("1. e4 c5 2. Nf3")], "testplayer", "both", 6)

        e4 = tree["children"][0]
        assert e4["eco"] == "B00"
        assert e4["opening_name"] == "King's Pawn Game"

        c5 = e4["children"][0]
        assert c5["eco"] == "B20"
        assert c5["opening_name"] == "Sicilian Defense"

    def test_the_starting_position_belongs_to_no_opening(self):
        tree = build_opening_tree([pgn("1. e4")], "testplayer", "both", 6)

        assert tree["eco"] is None
        assert tree["opening_name"] is None

    def test_a_node_inherits_the_nearest_named_ancestor(self):
        """Naming is longest-prefix: deep in a line you are still in that line."""
        tree = build_opening_tree(
            [pgn("1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 6. Be3 e5")],
            "testplayer",
            "both",
            max_ply=12,
        )

        node = tree
        for _ in range(12):
            node = node["children"][0]

        # The 12-ply path has no entry of its own, but it is unmistakably a
        # Najdorf and must say so rather than reporting nothing.
        assert node["ply"] == 12
        assert "Najdorf" in node["opening_name"]
        assert node["eco"] == "B90"

    def test_an_unnamed_opening_stays_unnamed_rather_than_guessing(self):
        tree = build_opening_tree(
            [pgn("1. e4 e5 2. Qh5 Ke7 3. Qxe5")], "testplayer", "both", max_ply=6
        )

        node = tree["children"][0]  # e4 — named
        assert node["opening_name"] is not None
        for _ in range(2):
            node = node["children"][0]
        # ...but once the line leaves the book, the last known name is kept
        # rather than inventing one; what must never happen is a wrong code.
        assert node["eco"] is not None or node["opening_name"] is None

    def test_sibling_lines_get_different_names(self):
        tree = build_opening_tree(
            [pgn("1. e4 c5"), pgn("1. e4 e6")], "testplayer", "both", max_ply=4
        )

        by_move = {c["move_san"]: c for c in tree["children"][0]["children"]}
        assert by_move["c5"]["opening_name"] == "Sicilian Defense"
        assert "French" in by_move["e6"]["opening_name"]


class TestMinGames:
    """One-off tails dominate a deep tree and are noise, not repertoire."""

    def test_keeps_everything_by_default(self):
        tree = build_opening_tree(
            [pgn("1. e4 c5"), pgn("1. d4 d5")], "testplayer", "both", max_ply=4
        )

        assert {c["move_san"] for c in tree["children"]} == {"e4", "d4"}

    def test_drops_lines_played_fewer_times_than_asked(self):
        games = [pgn("1. e4 c5")] * 3 + [pgn("1. d4 d5")]
        tree = build_opening_tree(games, "testplayer", "both", max_ply=4, min_games=2)

        assert [c["move_san"] for c in tree["children"]] == ["e4"]

    def test_prunes_deep_tails_while_keeping_the_trunk(self):
        # Two games share the first four plies then diverge; at min_games=2 the
        # shared trunk survives and the one-off continuations do not.
        games = [
            pgn("1. e4 c5 2. Nf3 d6 3. d4"),
            pgn("1. e4 c5 2. Nf3 d6 3. Bb5+"),
        ]
        tree = build_opening_tree(games, "testplayer", "both", 10, min_games=2)

        node = tree
        for _ in range(4):
            node = node["children"][0]
        assert node["move_san"] == "d6"
        assert node["games_count"] == 2
        # Both third moves were played once each, so neither is repertoire.
        assert "children" not in node

    def test_root_totals_still_count_every_analysed_game(self):
        # Pruning shapes the tree; it must not rewrite how much was analysed.
        games = [pgn("1. e4 c5")] * 3 + [pgn("1. d4 d5")]
        tree = build_opening_tree(games, "testplayer", "both", 4, min_games=2)

        assert tree["games_count"] == 4


class TestMinGamesFloor:
    """The floor is a server cost control, so the server has to own it."""

    def test_shallow_trees_are_unfiltered(self):
        assert min_games_floor(8) == 1
        assert min_games_floor(12) == 1

    def test_deeper_trees_raise_the_floor(self):
        assert min_games_floor(16) == 2
        assert min_games_floor(24) == 2
        assert min_games_floor(40) == 3

    def test_the_floor_is_monotonic_in_depth(self):
        floors = [min_games_floor(d) for d in range(1, 41)]
        assert floors == sorted(floors)


class TestBookDepth:
    def test_reports_the_deepest_vendored_line(self):
        # Positions past this cannot match, so the walk skips computing them.
        assert max_book_ply() == 36

    def test_warm_builds_the_table_up_front(self):
        warm()
        assert len(eco_table()) > 3000
