#!/usr/bin/env python3
"""
Migrate local SQLite knightmind.db → Supabase (PostgreSQL).

Usage:
  1. Set DATABASE_URL in services/api/.env:
       DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres

  2. Run the script:
       python scripts/migrate_to_supabase.py

  What it does:
    Step 1 – Runs Alembic migrations against Supabase to create the schema
    Step 2 – Copies all rows from local SQLite into Supabase, respecting FK order
"""

import os
import sys
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
API_DIR = PROJECT_ROOT / "services" / "api"
SQLITE_PATH = API_DIR / "knightmind.db"

# Load config from the same .env the API uses
load_dotenv(API_DIR / ".env")

# Tables in FK-safe insertion order (parents before children)
TABLE_ORDER = [
    "jobs",
    "fen_eval_cache",
    "games",
    "puzzles",           # FK → games
    "puzzle_stats",      # FK → puzzles
    "puzzle_reviews",    # FK → puzzles
    "training_sessions",
    "rating_snapshots",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url or url.startswith("sqlite"):
        print("ERROR: Set DATABASE_URL in services/api/.env to your Supabase Postgres URL.")
        print('  DATABASE_URL=postgresql://postgres.<ref>:<pass>@aws-0-<region>.pooler.supabase.com:5432/postgres')
        sys.exit(1)
    return url


def run_alembic_migrations(pg_url: str) -> None:
    """Run alembic upgrade head against Supabase."""
    from alembic.config import Config
    from alembic import command

    alembic_ini = str(API_DIR / "alembic.ini")
    alembic_cfg = Config(alembic_ini)
    alembic_cfg.set_main_option("sqlalchemy.url", pg_url)

    print("Running Alembic migrations on Supabase...")
    command.upgrade(alembic_cfg, "head")
    print("Schema created successfully.\n")


def copy_data(pg_url: str) -> None:
    """Read all rows from SQLite and insert into PostgreSQL using psycopg v3."""
    import json as json_mod
    import psycopg
    from psycopg.types.json import Jsonb

    if not SQLITE_PATH.exists():
        print(f"WARNING: {SQLITE_PATH} not found. No data to migrate.")
        print("(Schema was still created — you can populate data later.)")
        return

    # JSON columns per table (SQLite stores these as text, Postgres needs explicit JSON)
    JSON_COLUMNS: dict[str, set[str]] = {
        "jobs": {"params", "result_json"},
        "training_sessions": {"session_data", "achievements"},
    }

    # Connect to both databases
    sqlite_conn = sqlite3.connect(str(SQLITE_PATH))
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cur = sqlite_conn.cursor()

    pg_conn = psycopg.connect(pg_url)

    total_rows = 0

    for table in TABLE_ORDER:
        # Check if table exists in SQLite
        sqlite_cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not sqlite_cur.fetchone():
            print(f"  {table}: skipped (not in SQLite)")
            continue

        # Fetch all rows
        sqlite_cur.execute(f"SELECT * FROM {table}")  # noqa: S608
        rows = sqlite_cur.fetchall()
        if not rows:
            print(f"  {table}: 0 rows (empty)")
            continue

        columns = [desc[0] for desc in sqlite_cur.description]
        json_cols = JSON_COLUMNS.get(table, set())
        col_list = ", ".join(f'"{c}"' for c in columns)
        placeholders = ", ".join([f"%({c})s" for c in columns])

        # Upsert: skip rows that already exist (idempotent re-runs)
        pk_col = columns[0]  # All our tables use the first column as PK
        insert_sql = (
            f'INSERT INTO {table} ({col_list}) VALUES ({placeholders}) '
            f'ON CONFLICT ("{pk_col}") DO NOTHING'
        )

        def make_params(row: sqlite3.Row) -> dict:
            """Convert a SQLite row to a dict, parsing JSON text columns."""
            d = dict(row)
            for col in json_cols:
                val = d.get(col)
                if isinstance(val, str):
                    try:
                        d[col] = Jsonb(json_mod.loads(val))
                    except (json_mod.JSONDecodeError, TypeError):
                        d[col] = Jsonb(val)
                elif val is not None:
                    d[col] = Jsonb(val)
            return d

        batch_size = 500
        inserted = 0
        with pg_conn.cursor() as pg_cur:
            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                for row in batch:
                    pg_cur.execute(insert_sql, make_params(row))
                inserted += len(batch)

        pg_conn.commit()
        total_rows += inserted
        print(f"  {table}: {inserted} rows copied")

    print(f"\nTotal: {total_rows} rows copied across {len(TABLE_ORDER)} tables.")

    sqlite_cur.close()
    sqlite_conn.close()
    pg_conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("KnightMind: SQLite → Supabase Migration")
    print("=" * 60)
    print()

    pg_url = get_database_url()

    # Ensure project imports work
    sys.path.insert(0, str(PROJECT_ROOT))

    # Step 1: Schema
    print("STEP 1: Create schema via Alembic")
    print("-" * 40)
    run_alembic_migrations(pg_url)

    # Step 2: Data
    print("STEP 2: Copy data from SQLite")
    print("-" * 40)
    copy_data(pg_url)

    print()
    print("=" * 60)
    print("Migration complete!")
    print()
    print("Next steps:")
    print("  1. Verify data in Supabase Dashboard → Table Editor")
    print("  2. Restart your API server (it will use DATABASE_URL from .env)")
    print("  3. Enable Row Level Security on tables if using Supabase API directly")
    print("=" * 60)


if __name__ == "__main__":
    main()
