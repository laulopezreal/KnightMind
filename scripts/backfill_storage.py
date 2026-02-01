import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from services.api.db import SessionLocal
from services.api.storage import GameRepository, PuzzleRepository, get_storage, get_puzzle_storage


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
    game_storage = get_storage(base_path)
    results = {}

    for username in game_storage.get_users():
        user_lower = username.lower()
        metadata_path = game_storage.metadata_path / user_lower
        pgn_path = game_storage.pgn_path / user_lower
        if not metadata_path.exists():
            continue

        imported = 0
        invalid = 0
        missing_pgn = 0
        invalid_samples: list[dict] = []
        for metadata_file in metadata_path.glob("*.json"):
            with metadata_file.open("r") as handle:
                metadata = json.load(handle)

            errors = validate_game_metadata(metadata)
            if errors:
                invalid += 1
                if len(invalid_samples) < 5:
                    invalid_samples.append({"path": str(metadata_file), "errors": errors})
                continue

            pgn_file = pgn_path / f"{metadata['game_id']}.pgn"
            if not pgn_file.exists():
                missing_pgn += 1
                continue

            pgn_text = pgn_file.read_text()
            imported_at = _parse_datetime(metadata.get("imported_at"))

            is_new, _ = game_repository.store_game(
                username=metadata["username"],
                url=metadata["url"],
                pgn=pgn_text,
                white_username=metadata["white_username"],
                black_username=metadata["black_username"],
                white_result=metadata["white_result"],
                black_result=metadata["black_result"],
                time_control=metadata["time_control"],
                end_time=metadata["end_time"],
                rated=metadata["rated"],
                imported_at=imported_at,
                source_path=str(metadata_file),
            )
            if is_new:
                imported += 1

        results[user_lower] = {
            "imported_games": imported,
            "files_seen": len(list(metadata_path.glob("*.json"))),
            "invalid_games": invalid,
            "missing_pgn": missing_pgn,
            "invalid_samples": invalid_samples,
        }

    return results


def backfill_puzzles(puzzle_repository: PuzzleRepository, base_path: Path) -> dict:
    puzzle_storage = get_puzzle_storage(base_path)
    results = {}

    for user_dir in puzzle_storage.puzzles_path.glob("*"):
        if not user_dir.is_dir():
            continue
        username = user_dir.name.lower()
        imported = 0
        invalid = 0
        files_seen = 0
        invalid_samples: list[dict] = []

        for puzzle_file in user_dir.glob("*.json"):
            files_seen += 1
            with puzzle_file.open("r") as handle:
                puzzle_data = json.load(handle)

            errors = validate_puzzle_data(puzzle_data)
            if errors:
                invalid += 1
                if len(invalid_samples) < 5:
                    invalid_samples.append({"path": str(puzzle_file), "errors": errors})
                continue

            created_at = _parse_datetime(puzzle_data.get("created_at"))
            used_on = _parse_date(puzzle_data.get("used_on"))

            is_new, _ = puzzle_repository.save_puzzle(
                username=puzzle_data["username"],
                source_game_id=puzzle_data["source_game_id"],
                ply=puzzle_data["ply"],
                fen=puzzle_data["fen"],
                side_to_move=puzzle_data["side_to_move"],
                played_move_uci=puzzle_data["played_move_uci"],
                best_move_uci=puzzle_data["best_move_uci"],
                eval_before=puzzle_data["eval_before"],
                eval_after=puzzle_data["eval_after"],
                swing=puzzle_data["swing"],
                puzzle_id=puzzle_data["id"],
                created_at=created_at,
                used_on=used_on,
                imported_at=created_at or datetime.now(timezone.utc),
                source_path=str(puzzle_file),
            )
            if is_new:
                imported += 1

        results[username] = {
            "imported_puzzles": imported,
            "files_seen": files_seen,
            "invalid_puzzles": invalid,
            "invalid_samples": invalid_samples,
        }

    return results


def main() -> None:
    os.environ.setdefault("KNIGHTMIND_STORAGE_MODE", "database")
    base_path = Path("data")

    with SessionLocal() as db:
        game_repository = GameRepository(db, base_path=base_path)
        puzzle_repository = PuzzleRepository(db, base_path=base_path)

        game_results = backfill_games(game_repository, base_path)
        puzzle_results = backfill_puzzles(puzzle_repository, base_path)

    users = sorted(set(game_results.keys()) | set(puzzle_results.keys()))
    print("Backfill report:")
    for user in users:
        print(f"- {user}")
        if user in game_results:
            info = game_results[user]
            print(f"  games: {info['imported_games']} imported from {info['files_seen']} files")
            if info["invalid_games"] or info["missing_pgn"]:
                print(f"  game issues: {info['invalid_games']} invalid, {info['missing_pgn']} missing PGN")
                for sample in info["invalid_samples"]:
                    print(f"    invalid: {sample['path']} -> {sample['errors']}")
        if user in puzzle_results:
            info = puzzle_results[user]
            print(f"  puzzles: {info['imported_puzzles']} imported from {info['files_seen']} files")
            if info["invalid_puzzles"]:
                print(f"  puzzle issues: {info['invalid_puzzles']} invalid")
                for sample in info["invalid_samples"]:
                    print(f"    invalid: {sample['path']} -> {sample['errors']}")


if __name__ == "__main__":
    main()
