"""Shared pytest fixtures for the API test suite.

Database fixtures
-----------------
One backend: Postgres, the database production runs. The suite used to default
to in-memory SQLite because the application offered a SQLite escape hatch and
it needed no setup -- and so it validated a database production does not use.
SQLite does not enforce foreign keys unless asked, which is how 38 tests
accumulated that inserted puzzles with no game and reviews with no puzzle,
passing while asserting against rows the schema forbids.

``KNIGHTMIND_TEST_DATABASE_URL`` selects the scratch database (see the root
conftest, which validates it before anything imports the app). The schema is
built once for the session and the rows are cleared between tests, because
create_all per test is ruinous over a real connection.
"""

import os
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Importing models is what registers the 14 tables on Base.metadata -- `db` only
# defines the empty declarative Base. Without it create_all() silently creates
# nothing and the first TRUNCATE fails on a table that was never made. The same
# omission in alembic/env.py made `alembic check` report every table as removed.
import services.api.models  # noqa: F401
from services.api.db import Base


@pytest.fixture(scope="session")
def db_url() -> str:
    return os.environ["KNIGHTMIND_TEST_DATABASE_URL"]


@pytest.fixture(scope="session")
def _schema_engine(db_url):
    """One engine and one schema for the whole session."""
    engine = create_engine(db_url)
    # Drop first: an aborted earlier run may have left tables behind, and
    # create_all would accept a stale schema rather than rebuild it.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_engine(_schema_engine):
    """An engine whose tables are empty at the start of every test.

    One TRUNCATE so CASCADE resolves the FK graph for us; RESTART IDENTITY stops
    sequence values leaking between tests.
    """
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    if tables:
        with _schema_engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return _schema_engine


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
