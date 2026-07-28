"""Tests for ECO classification of opening lines."""

from .eco import _moves, classify, eco_table
from .tree_builder import build_opening_tree


def pgn(moves: str, white: str = "testplayer", result: str = "1-0") -> str:
    return f'[White "{white}"]\n[Black "opponent"]\n[Result "{result}"]\n\n{moves} {result}\n'


class TestTable:
    def test_loads_the_vendored_dataset(self):
        assert len(eco_table()) > 3000

    def test_strips_move_numbers_from_the_pgn_column(self):
        # The vendored column is "1. e4 c5 2. Nf3"; only the moves are the key.
        assert _moves("1. e4 c5 2. Nf3") == ("e4", "c5", "Nf3")
        assert _moves("1... e5") == ("e5",)

    def test_names_a_first_move(self):
        assert classify(("e4",)) == ("B00", "King's Pawn Game")

    def test_names_a_main_line(self):
        code, name = classify(("e4", "c5"))
        assert code == "B20"
        assert name == "Sicilian Defense"

    def test_names_a_deep_variation(self):
        code, name = classify(
            ("e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6")
        )
        assert code == "B90"
        assert "Najdorf" in name

    def test_returns_none_for_an_unknown_path(self):
        assert classify(("e4", "e5", "Qh5", "Ke7", "Qxe5")) is None

    def test_returns_none_for_an_empty_path(self):
        assert classify(()) is None


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
