import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.api.models import Game


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


class GameRepository:
    def __init__(self, db: Session):
        self.db = db

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
    ) -> tuple[bool, str]:
        username_lower = username.lower()
        game_id = self._game_id_from_url(url)

        existing = self.db.get(Game, game_id)
        if existing is not None:
            return False, game_id

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
            pgn_blob=pgn,
        )
        self.db.add(game)
        try:
            self.db.commit()
            return True, game_id
        except IntegrityError:
            self.db.rollback()
            return False, game_id

    def get_users(self) -> list[str]:
        stmt = select(Game.username).distinct().order_by(Game.username.asc())
        return [row[0] for row in self.db.execute(stmt).all()]

    def get_game_count(self, username: str) -> int:
        username_lower = username.lower()
        stmt = (
            select(func.count())
            .select_from(Game)
            .where(Game.username == username_lower)
        )
        return self.db.scalar(stmt) or 0

    def get_all_metadata(self, username: str) -> list[GameMetadata]:
        username_lower = username.lower()
        stmt = (
            select(Game)
            .where(Game.username == username_lower)
            .order_by(Game.end_time.desc())
        )
        games = self.db.scalars(stmt).all()
        return [self._to_metadata(game) for game in games]

    def get_latest_game_time(self, username: str) -> datetime | None:
        """Get the timestamp of the most recent game for a user."""
        username_lower = username.lower()
        stmt = select(func.max(Game.end_time)).where(Game.username == username_lower)
        max_time = self.db.scalar(stmt)
        return datetime.fromtimestamp(max_time, tz=timezone.utc) if max_time else None

    def get_pgn(self, username: str, game_id: str) -> str | None:
        game = self.db.get(Game, game_id)
        if game and game.username == username.lower() and game.pgn_blob:
            return game.pgn_blob
        return None

    def record_import_summary(
        self, username: str, new_games: int, imported_at: str | None = None
    ) -> None:
        """Store the last import summary for a user in the database."""
        from services.api.models import ImportSummary

        username_lower = username.lower()
        if imported_at is None:
            ts = datetime.now(timezone.utc)
        else:
            ts = datetime.fromisoformat(imported_at)

        existing = self.db.get(ImportSummary, username_lower)
        if existing:
            existing.last_imported_at = ts
            existing.last_new_games = new_games
        else:
            self.db.add(
                ImportSummary(
                    username=username_lower,
                    last_imported_at=ts,
                    last_new_games=new_games,
                )
            )
        self.db.commit()

    def get_last_import_summary(self, username: str) -> dict[str, str | int] | None:
        """Get the last import summary for a user from the database."""
        from services.api.models import ImportSummary

        summary = self.db.get(ImportSummary, username.lower())
        if not summary:
            return None
        return {
            "last_imported_at": summary.last_imported_at.replace(
                tzinfo=timezone.utc
            ).isoformat(),
            "last_new_games": summary.last_new_games,
        }
