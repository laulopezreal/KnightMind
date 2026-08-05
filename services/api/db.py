"""Engine and session factory. Postgres only.

This module used to offer a SQLite fallback behind ``KNIGHTMIND_DEV_SQLITE``,
left over from before production moved to Postgres. It was never free: because
the application supported two databases, the test suite took the one that
needed no setup, and then validated a database production does not run.

SQLite does not enforce foreign keys unless asked, so 38 tests accumulated that
inserted puzzles with no game and reviews with no puzzle -- passing while
asserting against rows the production schema forbids. The schema also had to be
authored twice, with ``sqlite_where`` variants shadowing every partial index,
and nothing checked that the two agreed.

One backend, one truth. Local development uses the Postgres in
docker-compose.yml.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from services.api.envutil import env_int


def _resolve_database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it to your Postgres connection string, "
            "e.g. postgresql+psycopg://user:pass@host:5432/knightmind. "
            "For local development, `make docker-up` starts one."
        )
    if url.startswith("sqlite"):
        raise RuntimeError(
            "DATABASE_URL points at SQLite. This application is Postgres-only: "
            "SQLite does not enforce foreign keys and does not share Postgres's "
            "aggregate, partial-index or datetime semantics, so running on it "
            "proves nothing about production. Use the Postgres in "
            "docker-compose.yml (`make docker-up`)."
        )
    return url


SQLALCHEMY_DATABASE_URL = _resolve_database_url()


# Sized against the request threadpool, not guessed. The route handlers that do
# blocking DB work are plain `def`, so Starlette runs them on anyio's threadpool
# (default limiter: 40 tokens) and up to that many can hold a Session at once.
# The ceiling sits a little above that limit rather than exactly at it, because
# a second, independent pool also reaches this engine: `asyncio.to_thread` (used
# by /engine/eval, the Chess.com import, the job worker and the hourly cleanup)
# dispatches to asyncio's default executor, NOT anyio's threadpool, so its
# threads do not consume threadpool tokens. Its DB-touching users are each
# separately bounded -- ENGINE_EVAL_MAX_CONCURRENCY (4), the import rate limit,
# and a single worker -- so ~10 is the realistic worst case on top of the 40.
#
# Getting this wrong is not subtle: SQLAlchemy's defaults (pool_size=5,
# max_overflow=10) cap the pool at 15, and a 16th concurrent handler blocks for
# pool_timeout (30s) before raising. That was invisible while every handler ran
# on the event loop and only one query could be in flight at a time.
#
# Overridable so a multi-replica deploy can divide the Postgres connection
# budget (default max_connections is 100) without a code change.
POOL_SIZE = env_int("KNIGHTMIND_DB_POOL_SIZE", 10, min_value=1)
MAX_OVERFLOW = env_int("KNIGHTMIND_DB_MAX_OVERFLOW", 40, min_value=0)

# pre-ping guards against stale pooled connections (e.g. a proxy or pgbouncer
# recycling one underneath us).
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
