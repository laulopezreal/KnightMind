# Task: Migrate KnightMind from filesystem storage to database-only

## Context

KnightMind is a chess training app. It currently has a dual storage system: games and puzzles can be stored on the **filesystem** (JSON/PGN files) or in a **SQLite database**. The mode is controlled by `KNIGHTMIND_STORAGE_MODE` env var (defaults to `"filesystem"`). This dual system has caused bugs (new endpoints forgetting to check the filesystem) and adds complexity to every data-access path. The goal is to migrate all filesystem data into the database and switch to database-only mode.

## Current state

### Data on filesystem (`services/api/data/`)
```
data/
├── puzzles/<username>/<uuid>.json      — 197 puzzle JSON files (167 lauureal, 30 alfi3sr)
├── puzzle_index/<username>.json        — dedup index: "user:game_id:ply" → puzzle UUID
├── pgn/<username>/<game_id>.pgn        — 65,427 PGN text files
├── metadata/<username>/<game_id>.json  — 65,427 game metadata JSON files
├── index/<username>.json               — list of game_ids (array) per user
└── imports/<username>.json             — last import summary {last_imported_at, last_new_games}
```

### Data already in SQLite (`services/api/knightmind.db`)
- `games` table: **0 rows** (schema exists, empty)
- `puzzles` table: **0 rows** (schema exists, empty)
- `puzzle_stats`: 12 rows (review stats — always written to DB regardless of mode)
- `puzzle_reviews`: 39 rows (individual reviews — always in DB)
- `training_sessions`: 2 rows
- `rating_snapshots`, `fen_eval_cache`, `jobs`: small existing data, unrelated to migration

### Users with filesystem data
- `lauureal`: 192 games, 167 puzzles
- `alfi3sr`: ~65,200 games, 30 puzzles
- `hikaru`: games only (in game index), 0 puzzles

### File formats

**Puzzle JSON** (`data/puzzles/<user>/<uuid>.json`):
```json
{
    "id": "019c5235-8751-4ca1-9697-2d8e3946b78c",
    "username": "lauureal",
    "source_game_id": "836b4ef042ffd47706a8c6e7319b9493eec5802e36b080775cd2cc6f959df7b9",
    "ply": 41,
    "fen": "Q4b1r/p2nkppp/q3p3/8/3P1B2/1P6/P4PPP/2R3K1 w - - 3 21",
    "side_to_move": "white",
    "played_move_uci": "f4g5",
    "best_move_uci": "f4c7",
    "eval_before": 1.93,
    "eval_after": -1.2,
    "swing": 3.13,
    "created_at": "2026-02-01T15:13:29.797664+00:00",
    "used_on": null
}
```

**Game metadata JSON** (`data/metadata/<user>/<game_id>.json`):
```json
{
    "game_id": "028d2ce2aaffdd7112618fc4a244f2bcc15747cb998df1e24886d3ea8fbb4967",
    "url": "https://www.chess.com/game/live/164128416936",
    "username": "lauureal",
    "white_username": "lauureal",
    "black_username": "satvikuprari",
    "white_result": "win",
    "black_result": "checkmated",
    "time_control": "600",
    "end_time": 1770023875,
    "rated": true,
    "imported_at": "2026-02-02T10:24:33.552190+00:00"
}
```

**PGN files** (`data/pgn/<user>/<game_id>.pgn`): Raw PGN text, one file per game.

**Import summary** (`data/imports/<user>.json`):
```json
{
    "last_imported_at": "2026-02-03T12:04:48.080329+00:00",
    "last_new_games": 2
}
```

## Database schema (already exists)

```sql
CREATE TABLE games (
    game_id VARCHAR NOT NULL PRIMARY KEY,
    url TEXT NOT NULL,
    username VARCHAR NOT NULL,
    white_username VARCHAR NOT NULL,
    black_username VARCHAR NOT NULL,
    white_result VARCHAR NOT NULL,
    black_result VARCHAR NOT NULL,
    time_control VARCHAR NOT NULL,
    end_time INTEGER NOT NULL,
    rated BOOLEAN DEFAULT 0 NOT NULL,
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    source_path TEXT,
    pgn_blob TEXT
);

CREATE TABLE puzzles (
    id VARCHAR NOT NULL PRIMARY KEY,
    username VARCHAR NOT NULL,
    source_game_id VARCHAR NOT NULL REFERENCES games(game_id),
    ply INTEGER NOT NULL,
    fen TEXT NOT NULL,
    side_to_move VARCHAR NOT NULL,
    played_move_uci VARCHAR NOT NULL,
    best_move_uci VARCHAR NOT NULL,
    eval_before FLOAT NOT NULL,
    eval_after FLOAT NOT NULL,
    swing FLOAT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    used_on DATE,
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    source_path TEXT
);
-- UNIQUE INDEX on (username, source_game_id, ply)

CREATE TABLE puzzle_stats (
    puzzle_id VARCHAR NOT NULL PRIMARY KEY REFERENCES puzzles(id),
    username VARCHAR NOT NULL,
    attempts INTEGER DEFAULT 0, pass_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0, last_reviewed_at DATETIME,
    last_result VARCHAR, next_due_at DATETIME,
    interval_days INTEGER, ease_factor FLOAT DEFAULT 2.0,
    title VARCHAR, primary_motif VARCHAR
);
```

## Storage layer architecture

### Key files
- **`services/api/storage/games.py`** — `GameStorage` class: filesystem-only game storage. Methods: `store_game()`, `get_all_metadata()`, `get_pgn()`, `get_users()`, `get_game_count()`, `game_exists()`, `get_last_import_summary()`, `record_import_summary()`
- **`services/api/storage/puzzles.py`** — `PuzzleStorage` class: filesystem-only puzzle storage. Methods: `save_puzzle()`, `get_puzzle()`, `get_all_puzzles()`, `get_daily_puzzles()`, `mark_puzzles_used()`, `get_puzzle_count()`, `get_puzzle_stats()`
- **`services/api/storage/game_repository.py`** — `GameRepository` class: dual-mode abstraction. Has `self.db` and `self.filesystem`. Every method checks `get_storage_mode()` and routes to DB or filesystem. Also defines `get_storage_mode()` which reads `KNIGHTMIND_STORAGE_MODE` env var.
- **`services/api/storage/puzzle_repository.py`** — `PuzzleRepository` class: dual-mode abstraction. Same pattern as GameRepository.
- **`services/api/storage/spaced_repetition.py`** — Database-only module for SR scheduling. No filesystem code.
- **`services/api/ops.py`** — Has `/ops/storage/report` endpoint that compares filesystem vs DB to find missing records.

### Import summary
The import summary (`last_imported_at`, `last_new_games`) is currently stored as a JSON file in `data/imports/<user>.json`. It's accessed via `GameStorage.get_last_import_summary()` / `record_import_summary()`. The `GameRepository` delegates to `self.filesystem` for these methods regardless of storage mode. This needs a new DB table or integration into an existing one.

## What needs to happen

### Step 1: Write a migration script
Create a script (e.g. `services/api/scripts/migrate_to_db.py`) that:
1. Reads all game metadata JSON + PGN files from the filesystem
2. Inserts into the `games` table (with `pgn_blob` populated from .pgn files)
3. Reads all puzzle JSON files from the filesystem
4. Inserts into the `puzzles` table
5. Handles the import summaries (either a new table or store in an existing place)
6. Uses batch inserts for performance (65K+ games)
7. Is idempotent (can be run multiple times safely — skip existing records)
8. Prints progress (e.g. "Migrated 1000/65427 games...")
9. Verifies counts match after migration

Note: `puzzle_stats` and `puzzle_reviews` already exist in the DB and have foreign keys to `puzzles.id`. The puzzle IDs in the filesystem match the IDs referenced in `puzzle_stats`, so inserting puzzles with those same IDs will satisfy the FK constraints. However, `puzzles.source_game_id` references `games.game_id`, so **games must be migrated before puzzles**.

### Step 2: Simplify the storage layer
After migration and verification:
1. Set `KNIGHTMIND_STORAGE_MODE=database` (or remove the env var logic entirely)
2. Simplify `GameRepository` and `PuzzleRepository` to only use DB (remove filesystem branches and `get_storage_mode()` checks)
3. Remove the filesystem fallback from `list_puzzles` and `get_puzzle_detail` in `main.py` (the `_list_puzzles_from_filesystem` function and related code)
4. Remove `GameStorage` and `PuzzleStorage` classes (or keep them but stop importing them)
5. Handle import summaries in the DB (add a table or column)
6. Update `ops.py` `/storage/report` endpoint (it can be simplified or removed)

### Step 3: Clean up
1. Remove `services/api/storage/games.py` and `services/api/storage/puzzles.py` (filesystem classes)
2. Remove `get_storage_mode()` function
3. Remove `data/` directory references from `.gitignore` if present
4. Update any tests that mock filesystem storage

## Important constraints
- Games MUST be migrated before puzzles (FK constraint: `puzzles.source_game_id → games.game_id`)
- Puzzle IDs must be preserved exactly (they're referenced by `puzzle_stats` and `puzzle_reviews`)
- Game IDs must be preserved exactly (SHA256 hashes, referenced by puzzles)
- The `puzzle_stats` FK constraint `fk_puzzle_stats_puzzle_id` references `puzzles.id` — all 12 puzzle_stats rows must have their corresponding puzzle inserted
- Usernames should be lowercased consistently (the app lowercases everywhere)
- The migration script should be runnable from the repo root: `python -m services.api.scripts.migrate_to_db`
- Keep the existing alembic migration history intact — this is a data migration, not a schema migration

## Files to modify
- `services/api/scripts/migrate_to_db.py` — NEW: migration script
- `services/api/storage/game_repository.py` — simplify to DB-only
- `services/api/storage/puzzle_repository.py` — simplify to DB-only
- `services/api/main.py` — remove `_list_puzzles_from_filesystem` and filesystem fallbacks in `list_puzzles` / `get_puzzle_detail`
- `services/api/ops.py` — simplify or remove `/storage/report`
- `services/api/storage/games.py` — delete or stop importing
- `services/api/storage/puzzles.py` — delete or stop importing
- `services/api/storage/__init__.py` — update exports

## Running the project
- Backend: `cd services/api && python -m uvicorn main:app --reload --port 8000`
- DB location: `services/api/knightmind.db` (SQLite)
- Data location: `services/api/data/`
- Tests: `KNIGHTMIND_WORKER_DISABLED=true python -m pytest services/api/ -x -q`
- Frontend tests: `cd apps/web && npx vitest run`
