import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.api.models import Game

from .games import GameMetadata, get_storage


StorageMode = str


def get_storage_mode() -> StorageMode:
    mode = os.environ.get("KNIGHTMIND_STORAGE_MODE", "filesystem").lower()
    if mode not in {"filesystem", "dual", "database"}:
        return "filesystem"
    return mode


class GameRepository:
    def __init__(self, db: Session, base_path: str | Path = "data"):
        self.db = db
        self.filesystem = get_storage(base_path)

    @staticmethod
    def _game_id_from_url(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def _to_metadata(self, game: Game) -> GameMetadata:
        return GameMetadata(
            game_id=game.game_id,
            url=game.url,
            username=game.username,
            white_username=game.white_username,
            black_username=game.black_username,
            white_result=game.white_result,
            black_result=game.black_result,
            time_control=game.time_control,
            end_time=game.end_time,
            rated=game.rated,
            imported_at=game.imported_at.replace(tzinfo=timezone.utc).isoformat(),
        )

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
        imported_at: datetime | None = None,
        source_path: str | None = None,
        write_pgn_blob: bool = True,
    ) -> tuple[bool, str]:
        mode = get_storage_mode()
        username_lower = username.lower()
        game_id = self._game_id_from_url(url)

        is_new = False

        if mode in {"database", "dual"}:
            existing = self.db.get(Game, game_id)
            if existing is None:
                game = Game(
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
                    imported_at=imported_at or datetime.now(timezone.utc),
                    source_path=source_path,
                    pgn_blob=pgn if write_pgn_blob else None,
                )
                self.db.add(game)
                try:
                    self.db.commit()
                    is_new = True
                except IntegrityError:
                    self.db.rollback()
                    is_new = False

        if mode in {"filesystem", "dual"}:
            fs_is_new, _ = self.filesystem.store_game(
                username=username_lower,
                url=url,
                pgn=pgn,
                white_username=white_username,
                black_username=black_username,
                white_result=white_result,
                black_result=black_result,
                time_control=time_control,
                end_time=end_time,
                rated=rated,
            )
            if mode == "filesystem":
                is_new = fs_is_new

        return is_new, game_id

    def get_users(self) -> list[str]:
        mode = get_storage_mode()
        if mode == "filesystem":
            return self.filesystem.get_users()

        stmt = select(Game.username).distinct().order_by(Game.username.asc())
        users = [row[0] for row in self.db.execute(stmt).all()]
        if mode == "dual" and not users:
            return self.filesystem.get_users()
        return users

    def get_game_count(self, username: str) -> int:
        mode = get_storage_mode()
        username_lower = username.lower()
        if mode == "filesystem":
            return self.filesystem.get_game_count(username_lower)

        stmt = select(func.count()).select_from(Game).where(Game.username == username_lower)
        count = self.db.scalar(stmt) or 0
        if mode == "dual" and count == 0:
            return self.filesystem.get_game_count(username_lower)
        return count

    def get_all_metadata(self, username: str) -> list[GameMetadata]:
        mode = get_storage_mode()
        username_lower = username.lower()
        if mode == "filesystem":
            return self.filesystem.get_all_metadata(username_lower)

        stmt = (
            select(Game)
            .where(Game.username == username_lower)
            .order_by(Game.end_time.desc())
        )
        games = self.db.scalars(stmt).all()
        if mode == "dual" and not games:
            return self.filesystem.get_all_metadata(username_lower)
        return [self._to_metadata(game) for game in games]

    def get_latest_game_time(self, username: str) -> datetime | None:
        """Get the timestamp of the most recent game for a user."""
        mode = get_storage_mode()
        username_lower = username.lower()

        if mode == "filesystem":
            metadata = self.filesystem.get_all_metadata(username_lower)
            return (
                datetime.fromtimestamp(metadata[0].end_time, tz=timezone.utc)
                if metadata
                else None
            )

        # Database mode: use SQL to get just the max end_time
        stmt = select(func.max(Game.end_time)).where(Game.username == username_lower)
        max_time = self.db.scalar(stmt)
        if mode == "dual" and max_time is None:
            metadata = self.filesystem.get_all_metadata(username_lower)
            return (
                datetime.fromtimestamp(metadata[0].end_time, tz=timezone.utc)
                if metadata
                else None
            )
        return datetime.fromtimestamp(max_time, tz=timezone.utc) if max_time else None

    def get_pgn(self, username: str, game_id: str) -> str | None:
        mode = get_storage_mode()
        if mode == "filesystem":
            return self.filesystem.get_pgn(username, game_id)

        game = self.db.get(Game, game_id)
        if game and game.username == username.lower() and game.pgn_blob:
            return game.pgn_blob

        if mode == "dual":
            return self.filesystem.get_pgn(username, game_id)
        return None
