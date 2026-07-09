"""
Production migration script: filesystem → database.

Reads games, puzzles, and import summaries from the `data/` directory
and batch-inserts them into the database.

Usage:
    python -m scripts.migrate_to_db [--data-dir data] [--batch-size 1000] [--dry-run]

Requires:
    - The database to be up-to-date with alembic migrations.
    - The `data/` directory to be present (on the production server).

Idempotency:
    Uses INSERT OR IGNORE (via IntegrityError handling) so it's safe to
    re-run without creating duplicates.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from services.api.db import SessionLocal
from services.api.models import Base, Game, Puzzle, ImportSummary


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


def _parse_date(value: str | None):
    """Parse a date string (YYYY-MM-DD) or ISO datetime to a date object."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _game_id_from_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def migrate_games(data_dir: Path, batch_size: int, dry_run: bool) -> dict:
    """Migrate games from data/metadata + data/pgn into the games table."""
    metadata_root = data_dir / "metadata"
    pgn_root = data_dir / "pgn"

    if not metadata_root.exists():
        print("  No metadata/ directory found — skipping games.")
        return {}

    results = {}

    for user_dir in sorted(metadata_root.iterdir()):
        if not user_dir.is_dir():
            continue
        username = user_dir.name.lower()
        user_pgn_dir = pgn_root / username

        files = list(user_dir.glob("*.json"))
        imported = 0
        skipped = 0
        errors = 0
        missing_pgn = 0
        batch = []

        for metadata_file in files:
            try:
                with metadata_file.open("r") as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                errors += 1
                if errors <= 3:
                    print(f"    Error reading {metadata_file}: {e}")
                continue

            url = meta.get("url", "")
            game_id = meta.get("game_id") or _game_id_from_url(url)

            # Read PGN
            pgn_file = (
                user_pgn_dir / f"{game_id}.pgn" if user_pgn_dir.exists() else None
            )
            pgn_text = None
            if pgn_file and pgn_file.exists():
                try:
                    pgn_text = pgn_file.read_text()
                except OSError:
                    pass
            if pgn_text is None:
                missing_pgn += 1

            imported_at = _parse_datetime(meta.get("imported_at"))

            batch.append(
                Game(
                    game_id=game_id,
                    url=url,
                    username=meta.get("username", username).lower(),
                    white_username=meta.get("white_username", ""),
                    black_username=meta.get("black_username", ""),
                    white_result=meta.get("white_result", ""),
                    black_result=meta.get("black_result", ""),
                    time_control=meta.get("time_control", ""),
                    end_time=meta.get("end_time", 0),
                    rated=meta.get("rated", False),
                    imported_at=imported_at or datetime.now(timezone.utc),
                    pgn_blob=pgn_text,
                    source_path=str(metadata_file),
                )
            )

            if len(batch) >= batch_size:
                if not dry_run:
                    count = _flush_batch(batch, Game)
                    imported += count
                    skipped += len(batch) - count
                else:
                    imported += len(batch)
                batch = []

                if (imported + skipped) % 5000 == 0:
                    print(
                        f"    {username}: {imported + skipped}/{len(files)} processed..."
                    )

        # Flush remaining
        if batch:
            if not dry_run:
                count = _flush_batch(batch, Game)
                imported += count
                skipped += len(batch) - count
            else:
                imported += len(batch)

        results[username] = {
            "total_files": len(files),
            "imported": imported,
            "skipped_duplicate": skipped,
            "errors": errors,
            "missing_pgn": missing_pgn,
        }
        print(
            f"  {username}: {imported} imported, {skipped} duplicates, "
            f"{errors} errors, {missing_pgn} missing PGN "
            f"(from {len(files)} files)"
        )

    return results


def migrate_puzzles(data_dir: Path, batch_size: int, dry_run: bool) -> dict:
    """Migrate puzzles from data/puzzles into the puzzles table."""
    puzzles_root = data_dir / "puzzles"

    if not puzzles_root.exists():
        print("  No puzzles/ directory found — skipping puzzles.")
        return {}

    results = {}

    for user_dir in sorted(puzzles_root.iterdir()):
        if not user_dir.is_dir():
            continue
        username = user_dir.name.lower()

        files = list(user_dir.glob("*.json"))
        imported = 0
        skipped = 0
        errors = 0
        batch = []

        for puzzle_file in files:
            try:
                with puzzle_file.open("r") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                errors += 1
                if errors <= 3:
                    print(f"    Error reading {puzzle_file}: {e}")
                continue

            created_at = _parse_datetime(data.get("created_at"))
            used_on = _parse_date(data.get("used_on"))

            batch.append(
                Puzzle(
                    id=data["id"],
                    username=data.get("username", username).lower(),
                    source_game_id=data["source_game_id"],
                    ply=data["ply"],
                    fen=data["fen"],
                    side_to_move=data["side_to_move"],
                    played_move_uci=data["played_move_uci"],
                    best_move_uci=data["best_move_uci"],
                    eval_before=data["eval_before"],
                    eval_after=data["eval_after"],
                    swing=data["swing"],
                    created_at=created_at or datetime.now(timezone.utc),
                    used_on=used_on,
                    imported_at=created_at or datetime.now(timezone.utc),
                    source_path=str(puzzle_file),
                )
            )

            if len(batch) >= batch_size:
                if not dry_run:
                    count = _flush_batch(batch, Puzzle)
                    imported += count
                    skipped += len(batch) - count
                else:
                    imported += len(batch)
                batch = []

                if (imported + skipped) % 5000 == 0:
                    print(
                        f"    {username}: {imported + skipped}/{len(files)} processed..."
                    )

        if batch:
            if not dry_run:
                count = _flush_batch(batch, Puzzle)
                imported += count
                skipped += len(batch) - count
            else:
                imported += len(batch)

        results[username] = {
            "total_files": len(files),
            "imported": imported,
            "skipped_duplicate": skipped,
            "errors": errors,
        }
        print(
            f"  {username}: {imported} imported, {skipped} duplicates, "
            f"{errors} errors (from {len(files)} files)"
        )

    return results


def migrate_import_summaries(data_dir: Path, dry_run: bool) -> int:
    """Migrate import summaries from data/imports/<user>.json."""
    imports_dir = data_dir / "imports"

    if not imports_dir.exists():
        print("  No imports/ directory found — skipping import summaries.")
        return 0

    count = 0
    with SessionLocal() as db:
        for summary_file in sorted(imports_dir.glob("*.json")):
            username = summary_file.stem.lower()
            try:
                with summary_file.open("r") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"    Error reading {summary_file}: {e}")
                continue

            last_imported_at = _parse_datetime(data.get("last_imported_at"))
            last_new_games = data.get("last_new_games", 0)

            if not dry_run:
                existing = db.get(ImportSummary, username)
                if existing:
                    existing.last_imported_at = last_imported_at or datetime.now(
                        timezone.utc
                    )
                    existing.last_new_games = last_new_games
                else:
                    db.add(
                        ImportSummary(
                            username=username,
                            last_imported_at=last_imported_at
                            or datetime.now(timezone.utc),
                            last_new_games=last_new_games,
                        )
                    )
            count += 1

        if not dry_run:
            db.commit()

    print(f"  {count} import summaries migrated.")
    return count


def _flush_batch(batch: list, model_class) -> int:
    """Insert a batch of records one at a time, skipping duplicates. Returns count of new records."""
    inserted = 0
    with SessionLocal() as db:
        for record in batch:
            db.add(record)
            try:
                db.commit()
                inserted += 1
            except IntegrityError:
                db.rollback()
    return inserted


def verify_counts(data_dir: Path):
    """Print DB counts vs filesystem counts for verification."""
    print("\n--- Verification ---")
    with SessionLocal() as db:
        game_count = db.scalar(select(func.count()).select_from(Game))
        puzzle_count = db.scalar(select(func.count()).select_from(Puzzle))
        summary_count = db.scalar(select(func.count()).select_from(ImportSummary))

    metadata_root = data_dir / "metadata"
    puzzles_root = data_dir / "puzzles"
    imports_dir = data_dir / "imports"

    fs_games = (
        sum(1 for _ in metadata_root.rglob("*.json")) if metadata_root.exists() else 0
    )
    fs_puzzles = (
        sum(1 for _ in puzzles_root.rglob("*.json")) if puzzles_root.exists() else 0
    )
    fs_summaries = (
        sum(1 for _ in imports_dir.glob("*.json")) if imports_dir.exists() else 0
    )

    print(f"  Games:            DB={game_count}, filesystem={fs_games}")
    print(f"  Puzzles:          DB={puzzle_count}, filesystem={fs_puzzles}")
    print(f"  Import summaries: DB={summary_count}, filesystem={fs_summaries}")

    if game_count >= fs_games and puzzle_count >= fs_puzzles:
        print("\n  Migration looks complete.")
    else:
        print(
            "\n  WARNING: DB counts are lower than filesystem. Check for errors above."
        )


def main():
    parser = argparse.ArgumentParser(description="Migrate filesystem data to database")
    parser.add_argument(
        "--data-dir", default="data", help="Path to data directory (default: data)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Batch size for inserts (default: 1000)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Count files without inserting"
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"Error: data directory '{data_dir}' does not exist.")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"=== Filesystem → Database Migration ({mode}) ===")
    print(f"Data directory: {data_dir.resolve()}")
    print(f"Batch size: {args.batch_size}")
    print()

    # Games first (puzzles have FK to games)
    print("1. Migrating games...")
    game_results = migrate_games(data_dir, args.batch_size, args.dry_run)
    print()

    print("2. Migrating puzzles...")
    puzzle_results = migrate_puzzles(data_dir, args.batch_size, args.dry_run)
    print()

    print("3. Migrating import summaries...")
    migrate_import_summaries(data_dir, args.dry_run)
    print()

    if not args.dry_run:
        verify_counts(data_dir)


if __name__ == "__main__":
    main()
