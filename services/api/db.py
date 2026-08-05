import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from services.api.envutil import env_int

DEFAULT_SQLITE_URL = "sqlite:///./knightmind.db"


def _resolve_database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        return url
    # Local-dev escape hatch: opt in explicitly instead of silently falling
    # back to an ephemeral SQLite file (which loses data on redeploy in prod).
    if os.getenv("KNIGHTMIND_DEV_SQLITE", "").strip().lower() in {"1", "true", "yes"}:
        return DEFAULT_SQLITE_URL
    raise RuntimeError(
        "DATABASE_URL is not set. Set DATABASE_URL to your Postgres connection "
        "string (e.g. postgresql+psycopg://user:pass@host:5432/knightmind). "
        "For local development only, set KNIGHTMIND_DEV_SQLITE=1 to use the "
        f"local SQLite database ({DEFAULT_SQLITE_URL})."
    )


SQLALCHEMY_DATABASE_URL = _resolve_database_url()

_is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False} if _is_sqlite else {}

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

# pre-ping guards against stale pooled Postgres connections (e.g. Supabase /
# pgbouncer); local SQLite connections can't go stale, so skip the overhead.
#
# The pool arguments are Postgres-only. A `sqlite:///:memory:` URL resolves to
# SingletonThreadPool, which rejects pool_size/max_overflow outright; and the
# SQLite engine is a local dev/test throwaway, never the contended resource this
# sizing exists to manage.
pool_kwargs = (
    {} if _is_sqlite else {"pool_size": POOL_SIZE, "max_overflow": MAX_OVERFLOW}
)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=not _is_sqlite,
    **pool_kwargs,
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
