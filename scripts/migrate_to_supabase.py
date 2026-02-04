#!/usr/bin/env python3
"""
Migrate local SQLite knightmind.db → Supabase (PostgreSQL).

Usage:
  python scripts/migrate_to_supabase.py "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"

What it does:
  Step 1 – Runs Alembic migrations against Supabase to create the schema
  Step 2 – Copies all rows from local SQLite into Supabase, respecting FK order
"""

import argparse
import sys
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
API_DIR = PROJECT_ROOT / "services" / "api"
SQLITE_PATH = API_DIR / "knightmind.db"

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

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate local SQLite knightmind.db → Supabase (PostgreSQL)."
    )
    parser.add_argument(
        "pg_url",
        help='Supabase Postgres connection string, e.g. "postgresql://postgres.<ref>:<pass>@aws-0-<region>.pooler.supabase.com:5432/postgres"',
    )
    return parser.parse_args()


def _url_with_timeouts(url: str, connect_timeout: int = 10,
                        lock_timeout_ms: int = 15000) -> str:
    """Append connect_timeout and lock_timeout options to a Postgres URL."""
    from urllib.parse import quote
    opts = quote(f"-c lock_timeout={lock_timeout_ms}", safe="")
    sep = "&" if "?" in url else "?"
    # Double any '%' so configparser doesn't treat them as interpolation
    raw = f"{url}{sep}connect_timeout={connect_timeout}&options={opts}"
    return raw.replace("%", "%%")


LOCK_TIMEOUT_HINT = (
    "\nERROR: Migration timed out waiting for a database lock.\n"
    "This usually means a previous migration was interrupted and left an\n"
    "open transaction holding a lock on the schema.\n"
    "\n"
    "To fix this, open the Supabase SQL Editor and run:\n"
    "\n"
    "  -- 1. Find the stuck connection:\n"
    "  SELECT pid, state, query, query_start\n"
    "  FROM pg_stat_activity\n"
    "  WHERE state = 'idle in transaction';\n"
    "\n"
    "  -- 2. Terminate it (replace <pid> with the actual pid):\n"
    "  SELECT pg_terminate_backend(<pid>);\n"
    "\n"
    "After that, re-run this migration script.\n"
)


def run_alembic_migrations(pg_url: str) -> None:
    """Run alembic upgrade head against Supabase."""
    from alembic.config import Config
    from alembic import command

    alembic_ini = str(API_DIR / "alembic.ini")
    alembic_cfg = Config(alembic_ini)
    alembic_cfg.set_main_option("sqlalchemy.url", _url_with_timeouts(pg_url))

    print("Running Alembic migrations on Supabase...")
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        msg = str(exc).lower()
        if "lock" in msg or "timeout" in msg or "canceling" in msg:
            print(LOCK_TIMEOUT_HINT, file=sys.stderr)
            sys.exit(1)
        raise
    print("Schema created successfully.\n")


def copy_data(pg_url: str) -> None:
    """Read all rows from SQLite and insert into PostgreSQL using psycopg v3."""
    import json as json_mod
    from urllib.parse import urlparse, unquote
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

    # Boolean columns per table (SQLite stores as 0/1, Postgres needs real booleans)
    BOOL_COLUMNS: dict[str, set[str]] = {
        "games": {"rated"},
    }

    # Connect to both databases
    sqlite_conn = sqlite3.connect(str(SQLITE_PATH))
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cur = sqlite_conn.cursor()

    # Build a libpq conninfo string from the URL components.
    # We can't use urlparse because passwords with '#' break it
    # (Python treats '#' as a fragment delimiter).
    # Instead, use psycopg's own conninfo parser via make_conninfo
    # with the raw URL converted to keyword args by regex.
    import re as _re
    m = _re.match(
        r'postgresql(?:\+psycopg)?://([^:]+):(.+)@([^:]+):(\d+)/(.+)',
        pg_url,
    )
    if not m:
        print(f"ERROR: Could not parse connection URL.")
        sys.exit(1)
    pg_user, pg_pass, pg_host, pg_port, pg_dbname = m.groups()
    try:
        pg_conn = psycopg.connect(
            host=pg_host,
            port=int(pg_port),
            user=unquote(pg_user),
            password=unquote(pg_pass),
            dbname=pg_dbname,
            connect_timeout=10,
            options="-c lock_timeout=15000 -c statement_timeout=30000",
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg or "could not connect" in msg:
            print(
                f"\nERROR: Could not connect to PostgreSQL at {pg_host}:{pg_port}.\n"
                "Possible causes:\n"
                "  - The database host is unreachable (check your network/VPN)\n"
                "  - The connection string is incorrect\n"
                "  - Supabase project is paused (check the dashboard)\n",
                file=sys.stderr,
            )
            sys.exit(1)
        if "lock" in msg or "canceling" in msg:
            print(LOCK_TIMEOUT_HINT, file=sys.stderr)
            sys.exit(1)
        raise

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
        bool_cols = BOOL_COLUMNS.get(table, set())
        col_list = ", ".join(f'"{c}"' for c in columns)
        placeholders = ", ".join([f"%({c})s" for c in columns])

        # Upsert: skip rows that already exist (idempotent re-runs)
        pk_col = columns[0]  # All our tables use the first column as PK
        insert_sql = (
            f'INSERT INTO {table} ({col_list}) VALUES ({placeholders}) '
            f'ON CONFLICT ("{pk_col}") DO NOTHING'
        )

        def make_params(row: sqlite3.Row) -> dict:
            """Convert a SQLite row to a dict, handling type mismatches."""
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
            for col in bool_cols:
                if col in d:
                    d[col] = bool(d[col])
            return d

        batch_size = 100
        inserted = 0
        try:
            with pg_conn.cursor() as pg_cur:
                for i in range(0, len(rows), batch_size):
                    batch = rows[i : i + batch_size]
                    for row in batch:
                        pg_cur.execute(insert_sql, make_params(row))
                    pg_conn.commit()
                    inserted += len(batch)
                    print(f"    {table}: {inserted}/{len(rows)} rows...",
                          flush=True)
        except Exception as exc:
            pg_conn.rollback()
            msg = str(exc).lower()
            if "lock" in msg or "timeout" in msg or "canceling" in msg:
                print(
                    f"\n\nERROR: Timed out inserting into '{table}' "
                    f"(after {inserted}/{len(rows)} rows).\n"
                    "A previous interrupted migration likely left row locks.\n"
                    "\n"
                    "To fix this, open the Supabase SQL Editor and run:\n"
                    "\n"
                    "  SELECT pid, state, query, query_start\n"
                    "  FROM pg_stat_activity\n"
                    "  WHERE state = 'idle in transaction';\n"
                    "\n"
                    "  -- Then terminate the stuck connection(s):\n"
                    "  SELECT pg_terminate_backend(<pid>);\n"
                    "\n"
                    "After clearing it, re-run this script. Already-copied\n"
                    "rows will be skipped (ON CONFLICT DO NOTHING).\n",
                    file=sys.stderr,
                )
                sys.exit(1)
            raise

        total_rows += inserted
        print(f"  {table}: {inserted} rows copied (done)")

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

    args = parse_args()
    pg_url = args.pg_url

    # Ensure SQLAlchemy uses psycopg v3 (not psycopg2) for the Alembic step
    alembic_url = pg_url.replace("postgresql://", "postgresql+psycopg://", 1)

    # Ensure project imports work
    sys.path.insert(0, str(PROJECT_ROOT))

    # Step 1: Schema
    print("STEP 1: Create schema via Alembic")
    print("-" * 40)
    run_alembic_migrations(alembic_url)

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
    print("  2. Set DATABASE_URL in services/api/.env to point at Supabase")
    print("  3. Restart your API server")
    print("  4. Enable Row Level Security on tables if using Supabase API directly")
    print("=" * 60)


if __name__ == "__main__":
    main()
