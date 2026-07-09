import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DEFAULT_SQLITE_URL = "sqlite:///./knightmind.db"


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL")
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

connect_args = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args=connect_args, pool_pre_ping=True
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
