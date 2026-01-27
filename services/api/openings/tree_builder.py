"""
Opening tree builder module.

Parses PGN files and builds a tree structure representing the opening repertoire
with statistics for each position (games count, win/draw/loss).
"""

import io
from dataclasses import dataclass, field
from typing import Literal
import chess.pgn


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
class OpeningNode:
    """A node in the opening tree representing a position after a move."""
    move_san: str  # The move in SAN notation (e.g., "e4", "Nf3")
    ply: int  # Half-move number (1 = white's first move, 2 = black's first move, etc.)
    stats: OpeningStats = field(default_factory=OpeningStats)
    children: dict[str, "OpeningNode"] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "move_san": self.move_san,
            "ply": self.ply,
            "games_count": self.stats.games,
            "wins": self.stats.wins,
            "draws": self.stats.draws,
            "losses": self.stats.losses,
            "win_rate": round(self.stats.win_rate, 1),
        }
        if self.children:
            result["children"] = [child.to_dict() for child in self.children.values()]
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
    
    def add_game(
        self,
        pgn_text: str,
        player_username: str,
        color_filter: Literal["white", "black", "both"] = "both"
    ) -> bool:
        """
        Add a game to the opening tree.
        
        Args:
            pgn_text: The PGN text of the game
            player_username: The username of the player we're building the tree for
            color_filter: Which games to include based on player's color
            
        Returns:
            True if the game was added, False if skipped (wrong color or parse error)
        """
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
            if game is None:
                return False
            
            # Determine player's color
            white_player = game.headers.get("White", "").lower()
            black_player = game.headers.get("Black", "").lower()
            player_lower = player_username.lower()
            
            if player_lower == white_player:
                player_color = "white"
            elif player_lower == black_player:
                player_color = "black"
            else:
                # Player not in this game
                return False
            
            # Apply color filter
            if color_filter != "both" and color_filter != player_color:
                return False
            
            # Determine result from player's perspective
            result_str = game.headers.get("Result", "*")
            if result_str == "1-0":
                result = "win" if player_color == "white" else "loss"
            elif result_str == "0-1":
                result = "win" if player_color == "black" else "loss"
            elif result_str == "1/2-1/2":
                result = "draw"
            else:
                # Ongoing or unknown result - count as draw for stats
                result = "draw"
            
            # Walk through the moves and update the tree
            self._add_moves_to_tree(game, result, player_color)
            return True
            
        except Exception:
            # Skip games that can't be parsed
            return False
    
    def _add_moves_to_tree(
        self,
        game: chess.pgn.Game,
        result: Literal["win", "draw", "loss"],
        player_color: Literal["white", "black"]
    ) -> None:
        """Add moves from a game to the tree."""
        node = game
        current_tree_node = self.root
        ply = 0
        
        # Update root stats
        current_tree_node.stats.add_result(result)
        
        while node.variations and ply < self.max_ply:
            next_node = node.variation(0)  # Main line
            move = next_node.move
            board = node.board()
            move_san = board.san(move)
            
            ply += 1
            
            # Get or create child node
            if move_san not in current_tree_node.children:
                current_tree_node.children[move_san] = OpeningNode(
                    move_san=move_san,
                    ply=ply
                )
            
            child_node = current_tree_node.children[move_san]
            child_node.stats.add_result(result)
            
            current_tree_node = child_node
            node = next_node
    
    def build_tree(self) -> dict:
        """
        Build and return the opening tree as a dictionary.
        
        Returns:
            Dictionary representation of the opening tree suitable for JSON serialization
        """
        # Return the tree starting from root's children (skip the dummy root node)
        return {
            "move_san": "Start",
            "ply": 0,
            "games_count": self.root.stats.games,
            "wins": self.root.stats.wins,
            "draws": self.root.stats.draws,
            "losses": self.root.stats.losses,
            "win_rate": round(self.root.stats.win_rate, 1),
            "children": [child.to_dict() for child in self.root.children.values()]
        }


def build_opening_tree(
    pgn_texts: list[str],
    player_username: str,
    color_filter: Literal["white", "black", "both"] = "both",
    max_ply: int = 12
) -> dict:
    """
    Convenience function to build an opening tree from a list of PGN texts.
    
    Args:
        pgn_texts: List of PGN game strings
        player_username: Username to build the tree for
        color_filter: Filter by player's color ("white", "black", or "both")
        max_ply: Maximum number of half-moves to include
        
    Returns:
        Dictionary representation of the opening tree
    """
    builder = OpeningTreeBuilder(max_ply=max_ply)
    
    for pgn in pgn_texts:
        builder.add_game(pgn, player_username, color_filter)
    
    return builder.build_tree()
