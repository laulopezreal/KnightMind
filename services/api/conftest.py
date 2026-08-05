"""Shared pytest fixtures for the API test suite.

Database fixtures
-----------------
Thirty test modules each built their own engine, session and TestClient from a
near-identical twelve-line block. They had already drifted -- some dropped
tables on teardown, some did not -- and the duplication was what kept the suite
pinned to SQLite: repointing it meant thirty edits.

The backend is chosen by ``KNIGHTMIND_TEST_DATABASE_URL``:

  unset (default)  in-memory SQLite, a fresh schema per test. Identical to what
                   every module did by hand, so the default path is unchanged.
  a Postgres URL   one schema for the session, truncated between tests. This is
                   what production runs, and where SQLite quietly differs:
                   aggregate typing, partial indexes, naive-vs-aware datetime
                   comparison, integer division.

A test that passes on only one backend is describing a real portability bug,
not a fixture problem.
"""

import os
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import Base

TEST_DB_URL_ENV = "KNIGHTMIND_TEST_DATABASE_URL"
DEFAULT_TEST_DB_URL = "sqlite:///:memory:"


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


@pytest.fixture(scope="session")
def db_url() -> str:
    return os.getenv(TEST_DB_URL_ENV, DEFAULT_TEST_DB_URL)


@pytest.fixture(scope="session")
def _postgres_engine(db_url):
    """One engine and one schema for the whole session (Postgres only).

    Creating and dropping 14 tables per test is trivial on in-memory SQLite and
    ruinous over a real connection, so the schema is built once and the *rows*
    are cleared between tests instead.
    """
    if _is_sqlite(db_url):
        yield None
        return
    engine = create_engine(db_url)
    # Drop first: an aborted earlier run may have left tables behind, and
    # create_all would accept a stale schema rather than rebuild it.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_engine(db_url, _postgres_engine):
    """An engine whose schema is empty at the start of every test."""
    if not _is_sqlite(db_url):
        # One TRUNCATE so CASCADE resolves the FK graph for us; RESTART IDENTITY
        # stops sequence values leaking between tests the way a fresh SQLite
        # database never would.
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        if tables:
            with _postgres_engine.begin() as conn:
                conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        yield _postgres_engine
        return

    # StaticPool keeps every connection pointed at the same in-memory database;
    # without it each connection gets its own empty one.
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignores foreign keys unless asked, per connection, every time.
    # Left off, fixtures can insert a puzzle with no parent game or a review
    # with no parent puzzle -- states the production schema forbids and
    # Postgres rejects outright. Tests built on them look like coverage while
    # asserting against rows that cannot exist. Turning it on here is what
    # keeps the fast default run honest, instead of deferring the whole class
    # of defect to whoever next runs the suite against Postgres.
    @event.listens_for(engine, "connect")
    def _enforce_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """A Session on the test database -- the dominant fixture across the suite."""
    session_local = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session, monkeypatch):
    """TestClient with ``get_db`` overridden to the test session.

    Imported lazily because binding services.api.main constructs the app, and
    the many modules that never build a client should not pay for it.
    """
    from fastapi.testclient import TestClient

    from services.api.db import get_db
    from services.api.main import app

    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _hermetic_auto_snapshot(monkeypatch):
    """Keep automatic rating snapshots off the network in every test.

    Auto-snapshot fetches Chess.com stats on session complete, game import,
    and (throttled) rating-insights views. Default the fetch to a network
    failure — production treats it as best-effort, so callers proceed — and
    clear the per-username throttle registry so state can't leak between
    tests. Tests that need snapshot data re-patch
    ``services.api.ratings_auto.get_player_stats``.
    """
    from services.api import ratings_auto
    from services.ingest import NetworkError

    ratings_auto.reset_throttle()
    monkeypatch.setattr(
        ratings_auto,
        "get_player_stats",
        AsyncMock(side_effect=NetworkError("network disabled in tests")),
    )
    yield
    ratings_auto.reset_throttle()


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Clear the in-process rate-limit registry around every test.

    The limiters (audit gate 10) are module-global and keyed by principal, so
    without this reset hits would accumulate across tests that share a principal
    (e.g. the TestClient's ``testclient`` IP) and later tests would spuriously
    see 429s. Resetting before and after keeps each test hermetic.
    """
    from services.api.ratelimit import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


@pytest.fixture(autouse=True)
def _reset_openings_cache():
    """Clear the opening-tree cache around every test.

    It is a module-global keyed partly by game count and latest game time, so
    two tests importing the same fixture games under the same username would
    produce the same key and the second would silently be served the first
    one's tree — masking real differences in what the endpoint built.
    """
    from services.api.openings import tree_cache

    tree_cache.clear()
    yield
    tree_cache.clear()
