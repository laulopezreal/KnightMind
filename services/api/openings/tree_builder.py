"""
Opening tree builder module.

Parses PGN files and builds a tree structure representing the opening repertoire
with statistics for each position (games count, win/draw/loss).
"""

import io
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

import chess.pgn

from .eco import classify, max_book_ply


@dataclass
class OpeningStats:
    """Statistics for a position in the opening tree."""

    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0

    def add_result(self, result: Literal["win", "draw", "loss"]) -> None:
        """Add a game result to the stats."""
        self.games += 1
        if result == "win":
            self.wins += 1
        elif result == "draw":
            self.draws += 1
        else:
            self.losses += 1

    @property
    def win_rate(self) -> float:
        """Calculate win rate as percentage."""
        if self.games == 0:
            return 0.0
        return (self.wins + 0.5 * self.draws) / self.games * 100


@dataclass
class BuildReport:
    """
    Why each submitted PGN did or didn't make it into the tree.

    The endpoint knows how many games it stored for a user but the tree only
    reflects the ones it could actually use. Without this, a repertoire built
    from 12 of 500 games looks identical to one built from all 500. Splitting
    "excluded by the colour filter" (expected, user asked for it) from
    "skipped" (unexpected data loss) lets the UI stay quiet in the first case
    and warn in the second.
    """

    games_seen: int = 0
    games_analyzed: int = 0
    excluded_by_color: int = 0
    skipped_unreadable: int = 0
    skipped_not_player: int = 0
    skipped_unfinished: int = 0

    @property
    def games_skipped(self) -> int:
        """Games lost for reasons the user did not ask for."""
        return (
            self.skipped_unreadable + self.skipped_not_player + self.skipped_unfinished
        )

    def to_dict(self) -> dict:
        return {
            "games_seen": self.games_seen,
            "games_analyzed": self.games_analyzed,
            "excluded_by_color": self.excluded_by_color,
            "games_skipped": self.games_skipped,
            "skipped_unreadable": self.skipped_unreadable,
            "skipped_not_player": self.skipped_not_player,
            "skipped_unfinished": self.skipped_unfinished,
        }


@dataclass
class OpeningNode:
    """A node in the opening tree representing a position after a move."""

    move_san: str  # The move in SAN notation (e.g., "e4", "Nf3")
    ply: int  # Half-move number (1 = white's first move, 2 = black's first move, etc.)
    stats: OpeningStats = field(default_factory=OpeningStats)
    children: dict[str, "OpeningNode"] = field(default_factory=dict)
    # Position after this move, for ECO lookup. Recorded during the walk, where
    # a board already exists; None only for the synthetic root.
    epd: str | None = None

    def to_dict(
        self,
        inherited: tuple[str, str] | None = None,
        min_games: int = 1,
    ) -> dict:
        """
        Convert to dictionary for JSON serialization.

        Args:
            inherited: the nearest named ancestor's (code, name). ECO naming is
                longest-prefix, so a node with no entry of its own belongs to
                the most specific opening above it — twenty plies into the
                Najdorf you are still in the Najdorf.
            min_games: drop children played fewer than this many times. At depth
                the overwhelming majority of nodes are one-off tails — 96% of a
                measured 40-ply tree — which are noise rather than repertoire,
                and which inflate the response by orders of magnitude.
        """
        named = classify(self.epd) or inherited

        result = {
            "move_san": self.move_san,
            "ply": self.ply,
            "games_count": self.stats.games,
            "wins": self.stats.wins,
            "draws": self.stats.draws,
            "losses": self.stats.losses,
            "win_rate": round(self.stats.win_rate, 1),
            "eco": named[0] if named else None,
            "opening_name": named[1] if named else None,
        }
        kept = [
            child for child in self.children.values() if child.stats.games >= min_games
        ]
        if kept:
            result["children"] = [child.to_dict(named, min_games) for child in kept]
        return result


class OpeningTreeBuilder:
    """
    Builds an opening tree from a collection of PGN games.

    The tree represents the player's opening repertoire with statistics
    for each position showing how many games reached that position and
    the results (win/draw/loss from the player's perspective).
    """

    def __init__(self, max_ply: int = 12):
        """
        Initialize the tree builder.

        Args:
            max_ply: Maximum number of half-moves to include in the tree (default 12 = 6 full moves)
        """
        self.max_ply = max_ply
        self.root = OpeningNode(move_san="root", ply=0)
        self.report = BuildReport()

    def add_game(
        self,
        pgn_text: str,
        player_username: str,
        color_filter: Literal["white", "black", "both"] = "both",
    ) -> bool:
        """
        Add a game to the opening tree.

        Args:
            pgn_text: The PGN text of the game
            player_username: The username of the player we're building the tree for
            color_filter: Which games to include based on player's color

        Returns:
            True if the game was added; False if it was excluded (wrong colour)
            or skipped (unreadable, not this player's game, or never played out
            to a result). Either way `self.report` records the reason.
        """
        self.report.games_seen += 1
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
            if game is None:
                self.report.skipped_unreadable += 1
                return False

            # Determine player's color. Headers are stripped as well as
            # lowercased: a stray space in `[White "alice "]` would otherwise
            # fail the match and drop the game from the repertoire silently.
            white_player = game.headers.get("White", "").strip().lower()
            black_player = game.headers.get("Black", "").strip().lower()
            player_lower = player_username.strip().lower()

            # python-chess is lenient: arbitrary text parses into a game whose
            # seven-tag-roster fields are filled with the PGN "unknown"
            # placeholder ("?") rather than returning None. Treat a game that
            # names neither player as malformed, not as "someone else's game" —
            # the two mean very different things to a user reading the warning.
            unknown = {"", "?"}
            if white_player in unknown and black_player in unknown:
                self.report.skipped_unreadable += 1
                return False

            if player_lower == white_player:
                player_color = "white"
            elif player_lower == black_player:
                player_color = "black"
            else:
                # Player not in this game
                self.report.skipped_not_player += 1
                return False

            # Apply color filter
            if color_filter != "both" and color_filter != player_color:
                self.report.excluded_by_color += 1
                return False

            # Determine result from player's perspective. Anything that isn't a
            # decisive result or an agreed draw ("*" for ongoing/aborted, or a
            # malformed header) is excluded rather than scored: counting it as a
            # draw would award half a point for a game that was never played out
            # and quietly skew the score every node is coloured by.
            result_str = game.headers.get("Result", "*")
            if result_str == "1-0":
                result = "win" if player_color == "white" else "loss"
            elif result_str == "0-1":
                result = "win" if player_color == "black" else "loss"
            elif result_str == "1/2-1/2":
                result = "draw"
            else:
                self.report.skipped_unfinished += 1
                return False

            # Walk through the moves and update the tree
            self._add_moves_to_tree(game, result, player_color)
            self.report.games_analyzed += 1
            return True

        except Exception:
            # Skip games that can't be parsed
            self.report.skipped_unreadable += 1
            return False

    def _add_moves_to_tree(
        self,
        game: chess.pgn.Game,
        result: Literal["win", "draw", "loss"],
        player_color: Literal["white", "black"],
    ) -> None:
        """Add moves from a game to the tree."""
        node = game
        current_tree_node = self.root
        ply = 0

        # Update root stats
        current_tree_node.stats.add_result(result)

        # Maintain a single board and advance it incrementally. Calling
        # ``node.board()`` inside the loop replays every move from the start on
        # each call, making the walk O(ply^2) per game; pushing moves onto one
        # board keeps it linear. SAN is computed on the position *before* the
        # move (identical to the previous ``node.board().san(move)``).
        board = game.board()
        book_depth = max_book_ply()

        while node.variations and ply < self.max_ply:
            next_node = node.variation(0)  # Main line
            move = next_node.move
            move_san = board.san(move)
            board.push(move)

            ply += 1

            # Get or create child node
            if move_san not in current_tree_node.children:
                current_tree_node.children[move_san] = OpeningNode(
                    move_san=move_san,
                    ply=ply,
                    # ECO is keyed by position, and the board is already here.
                    # A node's move path is fixed, so its EPD never changes —
                    # recording it once on creation is enough. Past the deepest
                    # book line it can never match, and epd() is by far the most
                    # expensive call in this walk (~45% of it), so skip it there
                    # and let those nodes inherit as they would anyway.
                    epd=board.epd() if ply <= book_depth else None,
                )

            child_node = current_tree_node.children[move_san]
            child_node.stats.add_result(result)

            current_tree_node = child_node
            node = next_node

    def build_tree(self, min_games: int = 1) -> dict:
        """
        Build and return the opening tree as a dictionary.

        Args:
            min_games: omit lines played fewer than this many times. A line you
                played once is not part of a repertoire, and at depth those
                one-off tails are the overwhelming majority of the tree.

        Returns:
            Dictionary representation of the opening tree suitable for JSON serialization
        """
        # Routed through to_dict rather than hand-built: a second writer means
        # every new node field has to be added twice (as `eco`/`opening_name`
        # just were) and the pruning predicate written twice. The root's `epd`
        # is None, so `classify` already returns the "belongs to no opening"
        # answer without a special case.
        tree = self.root.to_dict(min_games=min_games)
        tree["move_san"] = "Start"
        # The root always reports a children array, even when empty — clients
        # branch on its contents rather than its presence.
        tree.setdefault("children", [])
        return tree


def build_opening_tree(
    pgn_texts: Iterable[str],
    player_username: str,
    color_filter: Literal["white", "black", "both"] = "both",
    max_ply: int = 12,
    min_games: int = 1,
) -> dict:
    """
    Convenience function to build an opening tree from PGN texts.

    Args:
        pgn_texts: Iterable of PGN game strings
        player_username: Username to build the tree for
        color_filter: Filter by player's color ("white", "black", or "both")
        max_ply: Maximum number of half-moves to include

    Returns:
        Dictionary representation of the opening tree
    """
    builder = OpeningTreeBuilder(max_ply=max_ply)

    for pgn in pgn_texts:
        builder.add_game(pgn, player_username, color_filter)

    return builder.build_tree(min_games)
