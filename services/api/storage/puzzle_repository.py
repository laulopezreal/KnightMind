import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.api.models import Puzzle as PuzzleModel

from .puzzles import Puzzle, get_puzzle_storage
from .game_repository import get_storage_mode


class PuzzleRepository:
    def __init__(self, db: Session, base_path: str | Path = "data"):
        self.db = db
        self.filesystem = get_puzzle_storage(base_path)

    def _to_puzzle(self, puzzle: PuzzleModel) -> Puzzle:
        return Puzzle(
            id=puzzle.id,
            username=puzzle.username,
            source_game_id=puzzle.source_game_id,
            ply=puzzle.ply,
            fen=puzzle.fen,
            side_to_move=puzzle.side_to_move,
            played_move_uci=puzzle.played_move_uci,
            best_move_uci=puzzle.best_move_uci,
            eval_before=puzzle.eval_before,
            eval_after=puzzle.eval_after,
            swing=puzzle.swing,
            created_at=puzzle.created_at.replace(tzinfo=timezone.utc).isoformat(),
            used_on=puzzle.used_on.isoformat() if puzzle.used_on else None,
        )

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
        puzzle_id: str | None = None,
        created_at: datetime | None = None,
        used_on: date | None = None,
        imported_at: datetime | None = None,
        source_path: str | None = None,
    ) -> tuple[bool, str]:
        mode = get_storage_mode()
        username_lower = username.lower()
        puzzle_id = puzzle_id or str(uuid.uuid4())

        is_new = False

        if mode in {"database", "dual"}:
            puzzle = PuzzleModel(
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
                created_at=created_at or datetime.now(timezone.utc),
                used_on=used_on,
                imported_at=imported_at or datetime.now(timezone.utc),
                source_path=source_path,
            )
            self.db.add(puzzle)
            try:
                self.db.commit()
                is_new = True
            except IntegrityError:
                self.db.rollback()
                existing = self.db.scalars(
                    select(PuzzleModel.id).where(
                        PuzzleModel.username == username_lower,
                        PuzzleModel.source_game_id == source_game_id,
                        PuzzleModel.ply == ply,
                    )
                ).first()
                if existing:
                    puzzle_id = existing
                is_new = False

        if mode in {"filesystem", "dual"}:
            fs_is_new, fs_id = self.filesystem.save_puzzle(
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
            )
            if mode == "filesystem":
                is_new = fs_is_new
                puzzle_id = fs_id

        return is_new, puzzle_id

    def get_puzzle(self, username: str, puzzle_id: str) -> Puzzle | None:
        mode = get_storage_mode()
        if mode == "filesystem":
            return self.filesystem.get_puzzle(username, puzzle_id)

        puzzle = self.db.get(PuzzleModel, puzzle_id)
        if puzzle and puzzle.username == username.lower():
            return self._to_puzzle(puzzle)
        if mode == "dual":
            return self.filesystem.get_puzzle(username, puzzle_id)
        return None

    def get_all_puzzles(self, username: str) -> list[Puzzle]:
        mode = get_storage_mode()
        username_lower = username.lower()
        if mode == "filesystem":
            return self.filesystem.get_all_puzzles(username_lower)

        stmt = select(PuzzleModel).where(PuzzleModel.username == username_lower)
        puzzles = self.db.scalars(stmt).all()
        if mode == "dual" and not puzzles:
            return self.filesystem.get_all_puzzles(username_lower)
        return [self._to_puzzle(puzzle) for puzzle in puzzles]

    def get_daily_puzzles(self, username: str, n: int = 5) -> list[Puzzle]:
        mode = get_storage_mode()
        if mode == "filesystem":
            return self.filesystem.get_daily_puzzles(username, n)

        all_puzzles = self.get_all_puzzles(username)
        today_str = date.today().isoformat()

        used_today = [p for p in all_puzzles if p.used_on == today_str]
        unused = [p for p in all_puzzles if p.used_on is None]
        used_other_days = [p for p in all_puzzles if p.used_on and p.used_on != today_str]

        unused.sort(key=lambda p: p.created_at)
        used_other_days.sort(key=lambda p: p.created_at, reverse=True)

        selected = used_today[:n]
        if len(selected) < n:
            selected.extend(unused[: n - len(selected)])
        if len(selected) < n:
            selected.extend(used_other_days[: n - len(selected)])

        return selected

    def mark_puzzles_used(
        self, username: str, puzzle_ids: list[str], used_date: date | None = None
    ) -> int:
        if used_date is None:
            used_date = date.today()

        mode = get_storage_mode()
        username_lower = username.lower()
        if mode == "filesystem":
            return self.filesystem.mark_puzzles_used(username_lower, puzzle_ids, used_date)

        stmt = (
            update(PuzzleModel)
            .where(PuzzleModel.username == username_lower, PuzzleModel.id.in_(puzzle_ids))
            .values(used_on=used_date)
        )
        result = self.db.execute(stmt)
        self.db.commit()
        marked = result.rowcount or 0

        if mode == "dual":
            self.filesystem.mark_puzzles_used(username_lower, puzzle_ids, used_date)

        return marked

    def get_puzzle_count(self, username: str) -> int:
        mode = get_storage_mode()
        username_lower = username.lower()
        if mode == "filesystem":
            return self.filesystem.get_puzzle_count(username_lower)

        stmt = select(PuzzleModel.id).where(PuzzleModel.username == username_lower)
        count = len(self.db.execute(stmt).all())
        if mode == "dual" and count == 0:
            return self.filesystem.get_puzzle_count(username_lower)
        return count

    def get_puzzle_stats(self, username: str, puzzle_id: str) -> dict:
        mode = get_storage_mode()
        if mode == "filesystem":
            return self.filesystem.get_puzzle_stats(username, puzzle_id)

        puzzle = self.db.get(PuzzleModel, puzzle_id)
        if puzzle and puzzle.username == username.lower():
            return {
                "fen": puzzle.fen,
                "best_move": puzzle.best_move_uci,
                "side_to_move": puzzle.side_to_move,
                "swing": puzzle.swing,
            }
        if mode == "dual":
            return self.filesystem.get_puzzle_stats(username, puzzle_id)
        return {}
