import hashlib
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypedDict, cast

from sqlalchemy import CursorResult, Row, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.api.models import Game
from services.api.usernames import canonical_username

# Username convention: every public method here folds its ``username`` argument
# once with ``canonical_username`` before it touches the Game/ImportSummary
# key. See the "storage-boundary rule" section of ``services.api.usernames``
# for why a bare ``.lower()`` is not an acceptable substitute.

MANUAL_GAME_ID = "__manual__"

# Max number of game ids per IN clause when bulk-loading PGNs. Postgres copes
# with much larger IN lists, but capping the batch keeps each statement small
# and bounds memory to roughly one batch of PGN blobs at a time.
PGN_BATCH_SIZE = 1000


class ImportSummaryRow(TypedDict):
    """The last-import summary, as /import/status returns it.

    A TypedDict rather than `dict[str, str | int]`: the two values have
    different types, so the union form makes both reads `str | int | None` and
    ImportStatusResponse rejects them. Pydantic then validates on construction,
    so a drift here would surface as a 500 on GET /import/status rather than
    as a type error -- exactly what the gate is for.
    """

    last_imported_at: str | None
    last_new_games: int | None
    status: str
    operation_id: str | None
    started_at: str | None
    updated_at: str | None
    completed_at: str | None
    error: str | None


IMPORT_LEASE_DURATION = timedelta(hours=1)


def _as_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


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

    def _to_metadata(self, game: Game | Row) -> GameMetadata:
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
        commit: bool = True,
    ) -> tuple[bool, str]:
        """Store a game, returning (is_new, game_id).

        When ``commit`` is False the row is only flushed (inside a savepoint)
        and the caller owns the transaction: this lets bulk importers batch
        many inserts into a single commit instead of one commit per game.
        """
        if not url:
            # game_id is derived from the url; an empty/missing url would
            # collapse every such game to one sha256 hash and silently merge
            # unrelated games into a single identity.
            raise ValueError("store_game requires a non-empty game url")

        # One fold for the ownership probe and the INSERT: a game probed under
        # one key and written under another is a game its owner cannot see.
        username_lower = canonical_username(username)
        game_id = self._game_id_from_url(url)

        # Ownership is per (game_id, username): the same canonical game may be
        # owned by every participant who imports it. Only short-circuit when
        # THIS user already owns their copy.
        existing = self.db.get(Game, (game_id, username_lower))
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
        try:
            # Savepoint so a duplicate-insert race only rolls back this row,
            # never previously flushed rows in the caller's batch.
            with self.db.begin_nested():
                self.db.add(game)
        except IntegrityError:
            return False, game_id
        if commit:
            self.db.commit()
        return True, game_id

    def get_users(self) -> list[str]:
        stmt = (
            select(Game.username)
            .where(Game.game_id != MANUAL_GAME_ID)
            .distinct()
            .order_by(Game.username.asc())
        )
        return [row[0] for row in self.db.execute(stmt).all()]

    def get_game_count(self, username: str) -> int:
        username_lower = canonical_username(username)
        stmt = (
            select(func.count())
            .select_from(Game)
            .where(Game.username == username_lower, Game.game_id != MANUAL_GAME_ID)
        )
        return self.db.scalar(stmt) or 0

    def get_all_metadata(self, username: str) -> list[GameMetadata]:
        username_lower = canonical_username(username)
        # Select only the metadata columns: full Game rows would drag every
        # PGN blob into memory alongside the metadata.
        stmt = (
            select(
                Game.game_id,
                Game.url,
                Game.username,
                Game.white_username,
                Game.black_username,
                Game.white_result,
                Game.black_result,
                Game.time_control,
                Game.end_time,
                Game.rated,
                Game.imported_at,
            )
            .where(Game.username == username_lower, Game.game_id != MANUAL_GAME_ID)
            .order_by(Game.end_time.desc())
        )
        return [self._to_metadata(row) for row in self.db.execute(stmt)]

    def get_latest_game_time(self, username: str) -> datetime | None:
        """Get the timestamp of the most recent game for a user."""
        username_lower = canonical_username(username)
        stmt = select(func.max(Game.end_time)).where(
            Game.username == username_lower, Game.game_id != MANUAL_GAME_ID
        )
        max_time = self.db.scalar(stmt)
        return datetime.fromtimestamp(max_time, tz=timezone.utc) if max_time else None

    def get_pgn(self, username: str, game_id: str) -> str | None:
        if game_id == MANUAL_GAME_ID:
            return None
        game = self.db.get(Game, (game_id, canonical_username(username)))
        if game and game.pgn_blob:
            return game.pgn_blob
        return None

    def get_pgns(self, username: str, game_ids: Sequence[str]) -> dict[str, str]:
        """Bulk-load PGNs for the given game ids.

        Runs one query per PGN_BATCH_SIZE ids instead of one query per game.
        Only games owned by ``username`` are returned; ids belonging to other
        users (or games without PGN content) are silently omitted.
        """
        username = canonical_username(username)
        pgns: dict[str, str] = {}
        for start in range(0, len(game_ids), PGN_BATCH_SIZE):
            pgns.update(
                self._fetch_pgn_batch(
                    username, game_ids[start : start + PGN_BATCH_SIZE]
                )
            )
        return pgns

    def iter_pgns(self, username: str, game_ids: Sequence[str]) -> Iterator[str]:
        """Stream PGNs for the given game ids in ``game_ids`` order.

        Loads PGN_BATCH_SIZE blobs per query, so at most one batch is held in
        memory at a time. Ids without PGN content (or owned by another user)
        are skipped.
        """
        username = canonical_username(username)
        for start in range(0, len(game_ids), PGN_BATCH_SIZE):
            batch = game_ids[start : start + PGN_BATCH_SIZE]
            pgns = self._fetch_pgn_batch(username, batch)
            for game_id in batch:
                pgn = pgns.get(game_id)
                if pgn:
                    yield pgn

    def _fetch_pgn_batch(
        self, username: str, game_ids: Sequence[str]
    ) -> dict[str, str]:
        # Private: ``username`` arrives already folded from get_pgns/iter_pgns,
        # which are the public boundary.
        stmt = select(Game.game_id, Game.pgn_blob).where(
            Game.username == username,
            Game.game_id != MANUAL_GAME_ID,
            Game.game_id.in_(game_ids),
        )
        return {game_id: pgn for game_id, pgn in self.db.execute(stmt) if pgn}

    def record_import_summary(
        self, username: str, new_games: int, imported_at: str | None = None
    ) -> None:
        """Store the last import summary for a user in the database."""
        from services.api.models import ImportSummary

        username_lower = canonical_username(username)
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
                    status="succeeded",
                    started_at=ts,
                    updated_at=ts,
                    completed_at=ts,
                )
            )
        if existing:
            existing.status = "succeeded"
            existing.updated_at = ts
            existing.completed_at = ts
            existing.error = None
        self.db.commit()

    def begin_import(
        self, username: str, started_at: datetime | None = None
    ) -> str | None:
        """Claim the durable per-username import lease, or return None if active."""
        from services.api.models import ImportSummary

        username_lower = canonical_username(username)
        now = started_at or datetime.now(timezone.utc)
        # Postgres is the only supported database. This closes the absent-row
        # race where two first imports could both observe no summary before an
        # INSERT establishes the username primary key.
        lock_key = int.from_bytes(
            hashlib.sha256(f"import:{username_lower}".encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )
        self.db.execute(select(func.pg_advisory_xact_lock(lock_key)))
        summary = self.db.get(ImportSummary, username_lower)
        if (
            summary is not None
            and summary.status == "importing"
            and summary.updated_at is not None
            and summary.updated_at.replace(tzinfo=timezone.utc)
            >= now - IMPORT_LEASE_DURATION
        ):
            self.db.commit()
            return None

        operation_id = str(uuid.uuid4())
        if summary is None:
            summary = ImportSummary(username=username_lower)
            self.db.add(summary)
        summary.status = "importing"
        summary.operation_id = operation_id
        summary.started_at = now
        summary.updated_at = now
        summary.completed_at = None
        summary.error = None
        self.db.commit()
        return operation_id

    def heartbeat_import(
        self,
        username: str,
        operation_id: str,
        updated_at: datetime | None = None,
        *,
        commit: bool = True,
    ) -> bool:
        """Renew only the currently-owned import lease."""
        from services.api.models import ImportSummary

        statement = (
            update(ImportSummary)
            .where(
                ImportSummary.username == canonical_username(username),
                ImportSummary.status == "importing",
                ImportSummary.operation_id == operation_id,
            )
            .values(updated_at=updated_at or datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )
        affected_rows = cast(CursorResult, self.db.execute(statement)).rowcount
        if commit:
            self.db.commit()
        return affected_rows == 1

    def finish_import(
        self,
        username: str,
        operation_id: str,
        new_games: int,
        completed_at: datetime | None = None,
    ) -> bool:
        """Publish a successful result only when this operation still owns it."""
        from services.api.models import ImportSummary

        now = completed_at or datetime.now(timezone.utc)
        statement = (
            update(ImportSummary)
            .where(
                ImportSummary.username == canonical_username(username),
                ImportSummary.status == "importing",
                ImportSummary.operation_id == operation_id,
            )
            .values(
                status="succeeded",
                last_imported_at=now,
                last_new_games=new_games,
                updated_at=now,
                completed_at=now,
                error=None,
            )
            .execution_options(synchronize_session=False)
        )
        affected_rows = cast(CursorResult, self.db.execute(statement)).rowcount
        self.db.commit()
        return affected_rows == 1

    def fail_import(
        self,
        username: str,
        operation_id: str,
        error: str,
        completed_at: datetime | None = None,
    ) -> bool:
        """Record a safe terminal failure without clobbering a newer owner."""
        from services.api.models import ImportSummary

        now = completed_at or datetime.now(timezone.utc)
        statement = (
            update(ImportSummary)
            .where(
                ImportSummary.username == canonical_username(username),
                ImportSummary.status == "importing",
                ImportSummary.operation_id == operation_id,
            )
            .values(
                status="failed",
                updated_at=now,
                completed_at=now,
                error=error,
            )
            .execution_options(synchronize_session=False)
        )
        affected_rows = cast(CursorResult, self.db.execute(statement)).rowcount
        self.db.commit()
        return affected_rows == 1

    def get_last_import_summary(self, username: str) -> ImportSummaryRow | None:
        """Get the last import summary for a user from the database."""
        from services.api.models import ImportSummary

        summary = self.db.get(ImportSummary, canonical_username(username))
        if not summary:
            return None
        status = summary.status
        if (
            status == "importing"
            and summary.updated_at is not None
            and summary.updated_at.replace(tzinfo=timezone.utc)
            < datetime.now(timezone.utc) - IMPORT_LEASE_DURATION
        ):
            status = "interrupted"
        return {
            "last_imported_at": _as_utc_iso(summary.last_imported_at),
            "last_new_games": summary.last_new_games,
            "status": status,
            "operation_id": summary.operation_id,
            "started_at": _as_utc_iso(summary.started_at),
            "updated_at": _as_utc_iso(summary.updated_at),
            "completed_at": _as_utc_iso(summary.completed_at),
            "error": summary.error,
        }
