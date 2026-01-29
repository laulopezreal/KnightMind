"""
Puzzle storage module.

Stores chess puzzles generated from user's blunders with metadata.
Uses composite key (username, source_game_id, ply) to prevent duplicates.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path


@dataclass
class Puzzle:
    """A chess puzzle generated from a user's blunder."""

    id: str  # UUID
    username: str
    source_game_id: str  # ID of the game this puzzle came from
    ply: int  # Half-move number where the blunder occurred
    fen: str  # Position BEFORE the user's move
    side_to_move: str  # "white" or "black"
    played_move_uci: str  # The move the user actually played (the blunder)
    best_move_uci: str  # The best move according to engine
    eval_before: float  # Evaluation before the blunder (in pawns)
    eval_after: float  # Evaluation after the blunder (in pawns)
    swing: float  # eval_before - eval_after (magnitude of blunder)
    created_at: str  # ISO timestamp
    used_on: str | None  # Date when puzzle was used (YYYY-MM-DD), None if unused


class PuzzleStorage:
    """
    File-based storage for chess puzzles.

    Directory structure:
    data/
      puzzles/
        <username>/
          <puzzle_id>.json
      puzzle_index/
        <username>.json  # Composite key index for deduplication
    """

    def __init__(self, base_path: str | Path = "data"):
        self.base_path = Path(base_path)
        self.puzzles_path = self.base_path / "puzzles"
        self.index_path = self.base_path / "puzzle_index"

        # Ensure directories exist
        self.puzzles_path.mkdir(parents=True, exist_ok=True)
        self.index_path.mkdir(parents=True, exist_ok=True)

    def _get_composite_key(self, username: str, source_game_id: str, ply: int) -> str:
        """Generate composite key for deduplication."""
        return f"{username.lower()}:{source_game_id}:{ply}"

    def _get_user_index(self, username: str) -> dict[str, str]:
        """
        Get the index mapping composite keys to puzzle IDs.

        Returns:
            Dict mapping composite_key -> puzzle_id
        """
        index_file = self.index_path / f"{username.lower()}.json"
        if index_file.exists():
            with open(index_file, "r") as f:
                return json.load(f)
        return {}

    def _save_user_index(self, username: str, index: dict[str, str]) -> None:
        """Save the user's puzzle index."""
        index_file = self.index_path / f"{username.lower()}.json"
        with open(index_file, "w") as f:
            json.dump(index, f, indent=2)

    def save_puzzle(
        self,
        username: str,
        source_game_id: str,
        ply: int,
        fen: str,
        side_to_move: str,
        played_move_uci: str,
        best_move_uci: str,
        eval_before: float,
        eval_after: float,
        swing: float,
    ) -> tuple[bool, str]:
        """
        Save a puzzle with automatic deduplication.

        Args:
            username: Username who played the game
            source_game_id: ID of the game this puzzle came from
            ply: Half-move number
            fen: Position before the user's move
            side_to_move: "white" or "black"
            played_move_uci: Move the user actually played
            best_move_uci: Best move according to engine
            eval_before: Evaluation before the move
            eval_after: Evaluation after the move
            swing: Magnitude of evaluation swing

        Returns:
            Tuple of (is_new, puzzle_id)
            - is_new is False if puzzle already exists (dedupe)
        """
        username_lower = username.lower()
        composite_key = self._get_composite_key(username_lower, source_game_id, ply)

        # Check for duplicate
        index = self._get_user_index(username_lower)
        if composite_key in index:
            return False, index[composite_key]

        # Generate new puzzle ID
        puzzle_id = str(uuid.uuid4())

        # Create user directory
        user_puzzles_path = self.puzzles_path / username_lower
        user_puzzles_path.mkdir(exist_ok=True)

        # Create puzzle
        puzzle = Puzzle(
            id=puzzle_id,
            username=username_lower,
            source_game_id=source_game_id,
            ply=ply,
            fen=fen,
            side_to_move=side_to_move,
            played_move_uci=played_move_uci,
            best_move_uci=best_move_uci,
            eval_before=eval_before,
            eval_after=eval_after,
            swing=swing,
            created_at=datetime.now(timezone.utc).isoformat(),
            used_on=None,
        )

        # Save puzzle
        puzzle_file = user_puzzles_path / f"{puzzle_id}.json"
        with open(puzzle_file, "w") as f:
            json.dump(asdict(puzzle), f, indent=2)

        # Update index
        index[composite_key] = puzzle_id
        self._save_user_index(username_lower, index)

        return True, puzzle_id

    def get_puzzle(self, username: str, puzzle_id: str) -> Puzzle | None:
        """Get a specific puzzle by ID."""
        puzzle_file = self.puzzles_path / username.lower() / f"{puzzle_id}.json"
        if puzzle_file.exists():
            with open(puzzle_file, "r") as f:
                data = json.load(f)
                return Puzzle(**data)
        return None

    def get_all_puzzles(self, username: str) -> list[Puzzle]:
        """Get all puzzles for a user."""
        username_lower = username.lower()
        user_puzzles_path = self.puzzles_path / username_lower

        if not user_puzzles_path.exists():
            return []

        puzzles = []
        for puzzle_file in user_puzzles_path.glob("*.json"):
            with open(puzzle_file, "r") as f:
                data = json.load(f)
                puzzles.append(Puzzle(**data))

        return puzzles

    def get_daily_puzzles(self, username: str, n: int = 5) -> list[Puzzle]:
        """
        Get n puzzles for daily practice.

        Selection strategy:
        1. Prefer puzzles already used today (for idempotency)
        2. Then prefer puzzles that haven't been used (used_on is None)
        3. Fall back to puzzles that have been used on other days

        Args:
            username: Username to get puzzles for
            n: Number of puzzles to return

        Returns:
            List of up to n puzzles
        """
        from datetime import date
        
        all_puzzles = self.get_all_puzzles(username)
        today_str = date.today().isoformat()

        # Split into three categories
        used_today = [p for p in all_puzzles if p.used_on == today_str]
        unused = [p for p in all_puzzles if p.used_on is None]
        used_other_days = [p for p in all_puzzles if p.used_on is not None and p.used_on != today_str]

        # Sort unused by creation time (oldest first for variety)
        unused.sort(key=lambda p: p.created_at)

        # Sort used_other_days by creation time (newest first)
        used_other_days.sort(key=lambda p: p.created_at, reverse=True)

        # Take from used_today first (idempotency), then unused, then used_other_days
        selected = used_today[:n]
        if len(selected) < n:
            selected.extend(unused[: n - len(selected)])
        if len(selected) < n:
            selected.extend(used_other_days[: n - len(selected)])

        return selected

    def mark_puzzles_used(
        self, username: str, puzzle_ids: list[str], used_date: date | None = None
    ) -> int:
        """
        Mark puzzles as used on a specific date.

        Args:
            username: Username who owns the puzzles
            puzzle_ids: List of puzzle IDs to mark as used
            used_date: Date to mark (defaults to today)

        Returns:
            Number of puzzles successfully marked
        """
        if used_date is None:
            used_date = date.today()

        date_str = used_date.isoformat()
        username_lower = username.lower()
        user_puzzles_path = self.puzzles_path / username_lower

        if not user_puzzles_path.exists():
            return 0

        marked_count = 0
        for puzzle_id in puzzle_ids:
            puzzle_file = user_puzzles_path / f"{puzzle_id}.json"
            if puzzle_file.exists():
                with open(puzzle_file, "r") as f:
                    data = json.load(f)

                # Update used_on field
                data["used_on"] = date_str

                with open(puzzle_file, "w") as f:
                    json.dump(data, f, indent=2)

                marked_count += 1

        return marked_count

    def get_puzzle_count(self, username: str) -> int:
        """Get the total number of puzzles for a user."""
        return len(self.get_all_puzzles(username))


# Default storage instance
_default_puzzle_storage: PuzzleStorage | None = None


def get_puzzle_storage(base_path: str | Path = "data") -> PuzzleStorage:
    """Get or create the default puzzle storage instance."""
    global _default_puzzle_storage
    if _default_puzzle_storage is None:
        _default_puzzle_storage = PuzzleStorage(base_path)
    return _default_puzzle_storage
