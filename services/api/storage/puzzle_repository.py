import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.api.day_boundary import utc_today
from services.api.models import Puzzle as PuzzleModel
from services.api.models import PuzzleStats
from services.api.puzzles.identity import assign_primary_motif
from services.api.puzzles.position_names import (
    PositionFacts,
    answer_square_of,
    compose_position_name,
)
from services.api.puzzles.title_registry import is_duplicate_title, unique_title
from services.api.usernames import canonical_username

# How many times a save will re-pick a title after losing a race for it. Each
# attempt costs one round trip and the loop only runs when two concurrent saves
# want the SAME name, so a small number is plenty — and a bounded loop is the
# point: an unbounded one would turn a permanent title conflict into a hang.
TITLE_ATTEMPTS = 5


def normalized_position(fen: str) -> str:
    """Return the position-identity key for a FEN: its first four fields.

    A FEN's last two fields are the halfmove clock and fullmove number, which
    depend on the move ORDER that reached a position, not the position itself.
    Two FENs that describe the same board via different move orders (a
    transposition) therefore differ only in those counters. Keeping just the
    first four fields — piece placement, side to move, castling rights, en
    passant target — yields a key that is stable across transpositions, so the
    same position dedups to the same puzzle regardless of how it was reached.
    """

    return " ".join(fen.split()[:4])


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
    accept_moves_uci: str | None = None  # Comma-separated equivalence set
    confirmed_depth: int | None = None  # Depth of the stability confirmation pass
    # Full solution line (principal variation) as space-separated UCI moves,
    # starting with the solution move. None for legacy single-move puzzles.
    solution_pv: str | None = None


class PuzzleRepository:
    def __init__(self, db: Session):
        self.db = db

    def _existing_puzzle_id(
        self, username: str, source_game_id: str, ply: int
    ) -> str | None:
        """Return the existing puzzle id for the natural duplicate key, if any."""

        return self.db.scalars(
            select(PuzzleModel.id).where(
                PuzzleModel.username == username,
                PuzzleModel.source_game_id == source_game_id,
                PuzzleModel.ply == ply,
            )
        ).first()

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
            accept_moves_uci=puzzle.accept_moves_uci,
            confirmed_depth=puzzle.confirmed_depth,
            solution_pv=puzzle.solution_pv,
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
        accept_moves_uci: str | None = None,
        confirmed_depth: int | None = None,
        solution_pv: str | None = None,
        title: str | None = None,
        primary_motif: str | None = None,
    ) -> tuple[bool, str]:
        """Persist a new puzzle and its identity stats, or return an existing id."""

        # One fold for the dedup read, the Puzzle INSERT and the PuzzleStats
        # INSERT: a puzzle and its stats row MUST land under the same key or
        # the scheduler will never find the stats it just wrote.
        username_lower = canonical_username(username)
        existing = self._existing_puzzle_id(username_lower, source_game_id, ply)
        if existing:
            return False, existing

        puzzle_id = puzzle_id or str(uuid.uuid4())
        move_number = ply // 2 + 1
        # Resolved once, not per attempt: a retry is the same save, and it must
        # not drift to a later timestamp than the one this call decided on.
        created_at = created_at or datetime.now(timezone.utc)
        imported_at = imported_at or datetime.now(timezone.utc)

        def _build_puzzle() -> PuzzleModel:
            return PuzzleModel(
                id=puzzle_id,
                username=username_lower,
                source_game_id=source_game_id,
                ply=ply,
                fen=fen,
                # Position-identity key derived from the SAME fen we store, so the
                # endpoint's precheck (which queries this column) and the partial
                # unique index dedup transpositions consistently. Derived here rather
                # than passed in so callers that override fen (e.g. tests simulating a
                # concurrent save) can never store a mismatched fen/position pair.
                normalized_position=normalized_position(fen),
                side_to_move=side_to_move,
                played_move_uci=played_move_uci,
                best_move_uci=best_move_uci,
                accept_moves_uci=accept_moves_uci,
                eval_before=eval_before,
                eval_after=eval_after,
                swing=swing,
                confirmed_depth=confirmed_depth,
                solution_pv=solution_pv,
                created_at=created_at,
                used_on=used_on,
                imported_at=imported_at,
                source_path=source_path,
            )

        # Use the caller's explicit motif/title when provided (the analysis-save
        # path already knows them); otherwise derive them from the position (the
        # generation path).
        motif = (
            primary_motif
            if primary_motif is not None
            else assign_primary_motif(_build_puzzle())
        )
        # An explicit title reaches here only from the manual-save route, where
        # it is the string the user typed — so it is recorded as theirs and
        # nothing may overwrite it later.
        #
        # Otherwise the name is composed from the position, NOT from the model.
        # Naming is a bulk pass run by scripts/ai_name_puzzles.py; making the
        # write path wait on an API call would put provider latency (and
        # provider outages) inside puzzle generation, which imports whole games
        # at a time.
        if title is not None:
            stats_title, title_source = title, "user"
        else:
            stats_title = compose_position_name(
                PositionFacts(
                    fen=fen,
                    played_move_uci=played_move_uci,
                    answer_square=answer_square_of(best_move_uci),
                    primary_motif=motif,
                    move_number=move_number,
                )
            )
            title_source = "position"

        # Titles are unique per user, and position-derived names collide
        # constantly: two knight forks landing on f7 in two different games
        # compose the same string. That is a cosmetic problem right up until a
        # unique index turns it into a failed INSERT — and this INSERT is how a
        # freshly generated puzzle gets saved during a game import, so failing
        # it would lose the puzzle to a naming clash. It must not.
        #
        # So: pick a free name first (cheap, and the only thing that runs in the
        # overwhelming case), and treat a lost race for that name as a reason to
        # pick another one rather than as an error. A user-typed title is
        # uniquified the same way — "My Nemesis (2)" is a far better answer to
        # "you already used that name" than a 500 on save.
        #
        # Every attempt builds FRESH ORM objects: the rollback below expunges
        # the pending instances back to transient, and re-adding them would
        # replay a state SQLAlchemy no longer owns.
        for attempt in range(TITLE_ATTEMPTS):
            # Read the free-name set BEFORE anything is pending, so the query
            # cannot flush a half-built puzzle into the transaction on a session
            # left autoflushing.
            free_title = unique_title(self.db, username_lower, stats_title, move_number)
            self.db.add(_build_puzzle())
            self.db.add(
                PuzzleStats(
                    puzzle_id=puzzle_id,
                    username=username_lower,
                    attempts=0,
                    pass_count=0,
                    fail_count=0,
                    ease_factor=2.0,
                    primary_motif=motif,
                    title=free_title,
                    title_source=title_source,
                )
            )
            try:
                self.db.commit()
                return True, puzzle_id
            except IntegrityError as error:
                self.db.rollback()
                # Unchanged and load-bearing: the natural duplicate key wins
                # first, so a concurrent save of the SAME puzzle still resolves
                # to the winner's id instead of retrying under a new name.
                existing = self._existing_puzzle_id(username_lower, source_game_id, ply)
                if existing:
                    return False, existing
                # Anything that is not the title index — a position-index
                # violation the manual route absorbs, a missing FK — is still
                # re-raised untouched, on the first attempt, as before.
                if not is_duplicate_title(error) or attempt == TITLE_ATTEMPTS - 1:
                    raise

        # Unreachable: the loop either returns or raises on its last attempt.
        raise AssertionError("save_puzzle exhausted its title attempts")

    def get_puzzle(self, username: str, puzzle_id: str) -> Puzzle | None:
        puzzle = self.db.get(PuzzleModel, puzzle_id)
        if puzzle and puzzle.username == canonical_username(username):
            return self._to_puzzle(puzzle)
        return None

    def get_all_puzzles(self, username: str) -> list[Puzzle]:
        """Every puzzle this user owns, in a defined order.

        The ordering is load-bearing, which is easy to miss because nothing
        here reads it. This is the candidate list feeding ``/puzzles/due``, and
        in ``get_adaptive_puzzles`` every *new* puzzle produces an identical
        sort key — same tier, and ``time_factor`` is the single ``now`` value
        computed once for the whole call. Python's sort is stable, so for the
        new tier (the overwhelming majority of a fresh corpus) the session order
        is exactly this query's output order.

        Without an ORDER BY that was Postgres heap order: unspecified by the
        standard, free to change after an UPDATE or a VACUUM, and in practice
        grouped by import batch — so a session drew several puzzles from the
        same game in a row, and two identical requests could legitimately
        return different puzzles.

        ``created_at`` then ``id`` gives oldest-first with a total order:
        ``created_at`` alone is not unique, since one generation run stamps a
        whole batch, and ties would fall back to heap order again.
        """
        username_lower = canonical_username(username)
        stmt = (
            select(PuzzleModel)
            .where(PuzzleModel.username == username_lower)
            .order_by(PuzzleModel.created_at, PuzzleModel.id)
        )
        puzzles = self.db.scalars(stmt).all()
        return [self._to_puzzle(puzzle) for puzzle in puzzles]

    def get_latest_puzzle_time(self, username: str) -> datetime | None:
        """Get the timestamp of the most recently created puzzle for a user."""
        username_lower = canonical_username(username)
        stmt = select(func.max(PuzzleModel.created_at)).where(
            PuzzleModel.username == username_lower
        )
        result = self.db.scalar(stmt)
        if result is None:
            return None
        if result.tzinfo is None:
            return result.replace(tzinfo=timezone.utc)
        return result

    def get_daily_puzzles(self, username: str, n: int = 5) -> list[Puzzle]:
        all_puzzles = self.get_all_puzzles(username)
        # Daily rotation uses the one documented UTC day boundary (see
        # services.api.day_boundary) so the "used today" read matches the UTC
        # write in mark_puzzles_used on non-UTC servers.
        today_str = utc_today().isoformat()

        used_today = [p for p in all_puzzles if p.used_on == today_str]
        unused = [p for p in all_puzzles if p.used_on is None]
        used_other_days = [
            p for p in all_puzzles if p.used_on and p.used_on != today_str
        ]

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
            # UTC day boundary — consistent with get_daily_puzzles' read.
            used_date = utc_today()

        username_lower = canonical_username(username)
        stmt = (
            update(PuzzleModel)
            .where(
                PuzzleModel.username == username_lower, PuzzleModel.id.in_(puzzle_ids)
            )
            .values(used_on=used_date)
        )
        result = self.db.execute(stmt)
        self.db.commit()
        return cast(CursorResult, result).rowcount or 0

    def get_puzzle_count(self, username: str) -> int:
        username_lower = canonical_username(username)
        stmt = (
            select(func.count())
            .select_from(PuzzleModel)
            .where(PuzzleModel.username == username_lower)
        )
        return self.db.scalar(stmt) or 0

    def get_puzzle_stats(self, username: str, puzzle_id: str) -> dict:
        puzzle = self.db.get(PuzzleModel, puzzle_id)
        if puzzle and puzzle.username == canonical_username(username):
            return {
                "fen": puzzle.fen,
                "best_move": puzzle.best_move_uci,
                "side_to_move": puzzle.side_to_move,
                "swing": puzzle.swing,
            }
        return {}
