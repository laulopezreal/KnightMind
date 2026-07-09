"""Tests for the opening tree builder module."""

from .tree_builder import OpeningStats, OpeningTreeBuilder, build_opening_tree

# Fixture PGNs for testing
PGN_SICILIAN_WIN = """[Event "Test Game 1"]
[Site "Chess.com"]
[Date "2024.01.01"]
[White "testplayer"]
[Black "opponent1"]
[Result "1-0"]

1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 6. Be3 e5 1-0
"""

PGN_SICILIAN_LOSS = """[Event "Test Game 2"]
[Site "Chess.com"]
[Date "2024.01.02"]
[White "testplayer"]
[Black "opponent2"]
[Result "0-1"]

1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 g6 6. Be3 Bg7 0-1
"""

PGN_FRENCH_DRAW = """[Event "Test Game 3"]
[Site "Chess.com"]
[Date "2024.01.03"]
[White "testplayer"]
[Black "opponent3"]
[Result "1/2-1/2"]

1. e4 e6 2. d4 d5 3. Nc3 Bb4 4. e5 c5 5. a3 Bxc3+ 6. bxc3 Ne7 1/2-1/2
"""

PGN_AS_BLACK_WIN = """[Event "Test Game 4"]
[Site "Chess.com"]
[Date "2024.01.04"]
[White "opponent4"]
[Black "testplayer"]
[Result "0-1"]

1. d4 Nf6 2. c4 e6 3. Nc3 Bb4 4. Qc2 O-O 5. a3 Bxc3+ 6. Qxc3 b6 0-1
"""

PGN_AS_BLACK_LOSS = """[Event "Test Game 5"]
[Site "Chess.com"]
[Date "2024.01.05"]
[White "opponent5"]
[Black "testplayer"]
[Result "1-0"]

1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 4. e4 d6 5. Nf3 O-O 6. Be2 e5 1-0
"""


class TestOpeningStats:
    """Tests for OpeningStats class."""

    def test_initial_values(self):
        stats = OpeningStats()
        assert stats.games == 0
        assert stats.wins == 0
        assert stats.draws == 0
        assert stats.losses == 0

    def test_add_win(self):
        stats = OpeningStats()
        stats.add_result("win")
        assert stats.games == 1
        assert stats.wins == 1
        assert stats.draws == 0
        assert stats.losses == 0

    def test_add_draw(self):
        stats = OpeningStats()
        stats.add_result("draw")
        assert stats.games == 1
        assert stats.wins == 0
        assert stats.draws == 1
        assert stats.losses == 0

    def test_add_loss(self):
        stats = OpeningStats()
        stats.add_result("loss")
        assert stats.games == 1
        assert stats.wins == 0
        assert stats.draws == 0
        assert stats.losses == 1

    def test_win_rate_calculation(self):
        stats = OpeningStats()
        stats.add_result("win")
        stats.add_result("win")
        stats.add_result("draw")
        stats.add_result("loss")
        # 2 wins + 0.5 * 1 draw = 2.5 out of 4 = 62.5%
        assert stats.win_rate == 62.5

    def test_win_rate_zero_games(self):
        stats = OpeningStats()
        assert stats.win_rate == 0.0


class TestOpeningTreeBuilder:
    """Tests for OpeningTreeBuilder class."""

    def test_single_game_as_white(self):
        """Test adding a single game as white."""
        builder = OpeningTreeBuilder(max_ply=6)
        result = builder.add_game(PGN_SICILIAN_WIN, "testplayer")

        assert result is True
        tree = builder.build_tree()

        assert tree["games_count"] == 1
        assert tree["wins"] == 1
        assert len(tree["children"]) == 1
        assert tree["children"][0]["move_san"] == "e4"

    def test_single_game_as_black(self):
        """Test adding a single game as black."""
        builder = OpeningTreeBuilder(max_ply=6)
        result = builder.add_game(PGN_AS_BLACK_WIN, "testplayer")

        assert result is True
        tree = builder.build_tree()

        # As black, first move is opponent's d4
        assert tree["games_count"] == 1
        assert tree["wins"] == 1  # Win from black's perspective
        assert tree["children"][0]["move_san"] == "d4"

    def test_color_filter_white(self):
        """Test filtering by white color."""
        builder = OpeningTreeBuilder(max_ply=6)

        # Add a game as white - should be included
        builder.add_game(PGN_SICILIAN_WIN, "testplayer", "white")
        # Add a game as black - should be excluded
        builder.add_game(PGN_AS_BLACK_WIN, "testplayer", "white")

        tree = builder.build_tree()
        assert tree["games_count"] == 1

    def test_color_filter_black(self):
        """Test filtering by black color."""
        builder = OpeningTreeBuilder(max_ply=6)

        # Add a game as white - should be excluded
        builder.add_game(PGN_SICILIAN_WIN, "testplayer", "black")
        # Add a game as black - should be included
        builder.add_game(PGN_AS_BLACK_WIN, "testplayer", "black")

        tree = builder.build_tree()
        assert tree["games_count"] == 1

    def test_multiple_games_same_opening(self):
        """Test aggregating stats for the same opening moves."""
        builder = OpeningTreeBuilder(max_ply=6)

        builder.add_game(PGN_SICILIAN_WIN, "testplayer")  # 1.e4 c5 - win
        builder.add_game(PGN_SICILIAN_LOSS, "testplayer")  # 1.e4 c5 - loss

        tree = builder.build_tree()

        assert tree["games_count"] == 2
        assert tree["wins"] == 1
        assert tree["losses"] == 1

        # Both games start with e4
        e4_node = tree["children"][0]
        assert e4_node["move_san"] == "e4"
        assert e4_node["games_count"] == 2

        # Both continue with c5
        c5_node = e4_node["children"][0]
        assert c5_node["move_san"] == "c5"
        assert c5_node["games_count"] == 2

    def test_different_openings_branch(self):
        """Test that different openings create different branches."""
        builder = OpeningTreeBuilder(max_ply=6)

        builder.add_game(PGN_SICILIAN_WIN, "testplayer")  # 1.e4 c5
        builder.add_game(PGN_FRENCH_DRAW, "testplayer")  # 1.e4 e6

        tree = builder.build_tree()

        assert tree["games_count"] == 2

        # Both games start with e4
        e4_node = tree["children"][0]
        assert e4_node["games_count"] == 2

        # e4 should have two children: c5 and e6
        assert len(e4_node["children"]) == 2
        child_moves = {c["move_san"] for c in e4_node["children"]}
        assert child_moves == {"c5", "e6"}

    def test_max_ply_limit(self):
        """Test that tree depth is limited by max_ply."""
        builder = OpeningTreeBuilder(max_ply=4)  # Only 2 full moves
        builder.add_game(PGN_SICILIAN_WIN, "testplayer")

        tree = builder.build_tree()

        # Navigate to deepest node
        node = tree
        depth = 0
        while "children" in node and node["children"]:
            node = node["children"][0]
            depth += 1

        assert depth <= 4

    def test_player_not_in_game(self):
        """Test that games where player is not participating are skipped."""
        builder = OpeningTreeBuilder(max_ply=6)
        result = builder.add_game(PGN_SICILIAN_WIN, "someotherplayer")

        assert result is False
        tree = builder.build_tree()
        assert tree["games_count"] == 0

    def test_result_from_player_perspective(self):
        """Test that results are tracked from the player's perspective."""
        builder = OpeningTreeBuilder(max_ply=6)

        # White win = player win when playing white
        builder.add_game(PGN_SICILIAN_WIN, "testplayer")
        # White loss = player loss when playing white
        builder.add_game(PGN_SICILIAN_LOSS, "testplayer")
        # Black win (0-1) = player win when playing black
        builder.add_game(PGN_AS_BLACK_WIN, "testplayer")
        # Black loss (1-0) = player loss when playing black
        builder.add_game(PGN_AS_BLACK_LOSS, "testplayer")

        tree = builder.build_tree()

        assert tree["games_count"] == 4
        assert tree["wins"] == 2  # Sicilian win + Black win
        assert tree["losses"] == 2  # Sicilian loss + Black loss


class TestBuildOpeningTree:
    """Tests for the convenience function."""

    def test_build_from_list(self):
        """Test building tree from a list of PGNs."""
        pgns = [PGN_SICILIAN_WIN, PGN_FRENCH_DRAW, PGN_AS_BLACK_WIN]

        tree = build_opening_tree(pgns, "testplayer", "both", max_ply=6)

        assert tree["games_count"] == 3
        assert tree["wins"] == 2
        assert tree["draws"] == 1

    def test_empty_list(self):
        """Test building tree from empty list."""
        tree = build_opening_tree([], "testplayer", "both", max_ply=6)

        assert tree["games_count"] == 0
        assert tree["children"] == []

    def test_invalid_pgn_skipped(self):
        """Test that invalid PGNs are skipped gracefully."""
        pgns = [PGN_SICILIAN_WIN, "not a valid pgn", PGN_FRENCH_DRAW]

        tree = build_opening_tree(pgns, "testplayer", "both", max_ply=6)

        assert tree["games_count"] == 2  # Only 2 valid games
