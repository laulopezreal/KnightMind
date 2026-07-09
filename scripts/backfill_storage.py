from datetime import date, datetime, timezone
from pathlib import Path

from services.api.storage import GameRepository, PuzzleRepository

REQUIRED_GAME_FIELDS = {
    "game_id",
    "url",
    "username",
    "white_username",
    "black_username",
    "white_result",
    "black_result",
    "time_control",
    "end_time",
    "rated",
}
REQUIRED_PUZZLE_FIELDS = {
    "id",
    "username",
    "source_game_id",
    "ply",
    "fen",
    "side_to_move",
    "played_move_uci",
    "best_move_uci",
    "eval_before",
    "eval_after",
    "swing",
}


def validate_game_metadata(metadata: dict) -> list[str]:
    errors = []
    missing = REQUIRED_GAME_FIELDS - metadata.keys()
    if missing:
        errors.append(f"missing_fields:{sorted(missing)}")

    if "end_time" in metadata and not isinstance(metadata["end_time"], int):
        errors.append("end_time_not_int")
    if "rated" in metadata and not isinstance(metadata["rated"], bool):
        errors.append("rated_not_bool")

    return errors


def validate_puzzle_data(puzzle_data: dict) -> list[str]:
    errors = []
    missing = REQUIRED_PUZZLE_FIELDS - puzzle_data.keys()
    if missing:
        errors.append(f"missing_fields:{sorted(missing)}")

    if "ply" in puzzle_data and not isinstance(puzzle_data["ply"], int):
        errors.append("ply_not_int")
    for field in ("eval_before", "eval_after", "swing"):
        if field in puzzle_data and not isinstance(puzzle_data[field], (int, float)):
            errors.append(f"{field}_not_number")

    return errors


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def backfill_games(game_repository: GameRepository, base_path: Path) -> dict:
    """Deprecated: filesystem storage has been removed. Use migrate_to_db.py instead."""
    raise NotImplementedError(
        "Filesystem storage has been removed. Use migrate_to_db.py instead."
    )


def backfill_puzzles(puzzle_repository: PuzzleRepository, base_path: Path) -> dict:
    """Deprecated: filesystem storage has been removed. Use migrate_to_db.py instead."""
    raise NotImplementedError(
        "Filesystem storage has been removed. Use migrate_to_db.py instead."
    )


def main() -> None:
    """Deprecated: filesystem storage has been removed. Use migrate_to_db.py instead."""
    raise NotImplementedError(
        "Filesystem storage has been removed. Use migrate_to_db.py instead."
    )


if __name__ == "__main__":
    main()
