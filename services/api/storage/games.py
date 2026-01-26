"""
Game storage module.

Stores chess games as PGN files with metadata in JSON sidecar files.
Uses game URL as unique identifier to prevent duplicates.
"""

import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timezone


@dataclass
class GameMetadata:
    """Metadata for a stored chess game."""
    game_id: str  # Hash of the game URL (unique identifier)
    url: str
    username: str  # The user who imported this game
    white_username: str
    black_username: str
    white_result: str
    black_result: str
    time_control: str
    end_time: int
    rated: bool
    imported_at: str


class GameStorage:
    """
    File-based storage for chess games.
    
    Directory structure:
    data/
      pgn/
        <username>/
          <game_id>.pgn
      metadata/
        <username>/
          <game_id>.json
      index/
        <username>.json  # List of all game IDs for quick dedup check
    """
    
    def __init__(self, base_path: str | Path = "data"):
        self.base_path = Path(base_path)
        self.pgn_path = self.base_path / "pgn"
        self.metadata_path = self.base_path / "metadata"
        self.index_path = self.base_path / "index"
        
        # Ensure directories exist
        self.pgn_path.mkdir(parents=True, exist_ok=True)
        self.metadata_path.mkdir(parents=True, exist_ok=True)
        self.index_path.mkdir(parents=True, exist_ok=True)
    
    def _game_id_from_url(self, url: str) -> str:
        """Generate a unique game ID from the Chess.com game URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    
    def _get_user_index(self, username: str) -> set[str]:
        """Get the set of game IDs already imported for a user."""
        index_file = self.index_path / f"{username.lower()}.json"
        if index_file.exists():
            with open(index_file, "r") as f:
                return set(json.load(f))
        return set()
    
    def _save_user_index(self, username: str, game_ids: set[str]) -> None:
        """Save the user's game index."""
        index_file = self.index_path / f"{username.lower()}.json"
        with open(index_file, "w") as f:
            json.dump(list(game_ids), f)
    
    def game_exists(self, username: str, url: str) -> bool:
        """Check if a game has already been imported."""
        game_id = self._game_id_from_url(url)
        existing_ids = self._get_user_index(username)
        return game_id in existing_ids
    
    def store_game(
        self,
        username: str,
        url: str,
        pgn: str,
        white_username: str,
        black_username: str,
        white_result: str,
        black_result: str,
        time_control: str,
        end_time: int,
        rated: bool,
    ) -> tuple[bool, str]:
        """
        Store a game's PGN and metadata.
        
        Returns:
            Tuple of (is_new, game_id) - is_new is False if game was already stored
        """
        game_id = self._game_id_from_url(url)
        username_lower = username.lower()
        
        # Check for duplicate
        existing_ids = self._get_user_index(username_lower)
        if game_id in existing_ids:
            return False, game_id
        
        # Create user directories
        user_pgn_path = self.pgn_path / username_lower
        user_metadata_path = self.metadata_path / username_lower
        user_pgn_path.mkdir(exist_ok=True)
        user_metadata_path.mkdir(exist_ok=True)
        
        # Store PGN
        pgn_file = user_pgn_path / f"{game_id}.pgn"
        with open(pgn_file, "w") as f:
            f.write(pgn)
        
        # Store metadata
        metadata = GameMetadata(
            game_id=game_id,
            url=url,
            username=username_lower,
            white_username=white_username,
            black_username=black_username,
            white_result=white_result,
            black_result=black_result,
            time_control=time_control,
            end_time=end_time,
            rated=rated,
            imported_at=datetime.now(timezone.utc).isoformat(),
        )
        metadata_file = user_metadata_path / f"{game_id}.json"
        with open(metadata_file, "w") as f:
            json.dump(asdict(metadata), f, indent=2)
        
        # Update index
        existing_ids.add(game_id)
        self._save_user_index(username_lower, existing_ids)
        
        return True, game_id
    
    def get_game_count(self, username: str) -> int:
        """Get the number of games stored for a user."""
        return len(self._get_user_index(username.lower()))
    
    def get_all_metadata(self, username: str) -> list[GameMetadata]:
        """Get metadata for all games stored for a user."""
        username_lower = username.lower()
        user_metadata_path = self.metadata_path / username_lower
        
        if not user_metadata_path.exists():
            return []
        
        games = []
        for metadata_file in user_metadata_path.glob("*.json"):
            with open(metadata_file, "r") as f:
                data = json.load(f)
                games.append(GameMetadata(**data))
        
        return sorted(games, key=lambda g: g.end_time, reverse=True)
    
    def get_pgn(self, username: str, game_id: str) -> str | None:
        """Get the PGN for a specific game."""
        pgn_file = self.pgn_path / username.lower() / f"{game_id}.pgn"
        if pgn_file.exists():
            with open(pgn_file, "r") as f:
                return f.read()
        return None


# Default storage instance
_default_storage: GameStorage | None = None


def get_storage(base_path: str | Path = "data") -> GameStorage:
    """Get or create the default storage instance."""
    global _default_storage
    if _default_storage is None:
        _default_storage = GameStorage(base_path)
    return _default_storage
