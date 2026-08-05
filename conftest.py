import os

import pytest

# The application is Postgres-only (see services/api/db.py), so the suite needs
# a real Postgres. Tests import the app -- and therefore services.api.db -- at
# module import time, and db.py fails fast without DATABASE_URL, so the URL has
# to be in the environment before collection starts.
TEST_DB_URL_ENV = "KNIGHTMIND_TEST_DATABASE_URL"


def _resolve_test_database_url() -> str:
    url = (os.getenv(TEST_DB_URL_ENV) or "").strip()
    if not url:
        raise RuntimeError(
            f"{TEST_DB_URL_ENV} is not set. The suite runs against Postgres, the "
            "database production uses. Start one with `make docker-up`, then:\n\n"
            f"  export {TEST_DB_URL_ENV}="
            "postgresql+psycopg://knightmind:knightmind@localhost:5432/knightmind_test"
        )
    # The fixtures DROP and TRUNCATE every table. Pointed at a development or
    # production database that destroys real data, and this URL comes from an
    # environment variable that is easy to have left set from something else.
    # Requiring "test" in the database name is a cheap, unmissable guard against
    # the one mistake here that cannot be undone.
    database = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "test" not in database.lower():
        raise RuntimeError(
            f"Refusing to run: {TEST_DB_URL_ENV} names a database {database!r}, "
            "and the fixtures drop and truncate every table in it. Rename the "
            "scratch database to contain 'test' (e.g. 'knightmind_test') to "
            "confirm it is disposable."
        )
    return url


TEST_DATABASE_URL = _resolve_test_database_url()

# Hand the same URL to the application under test.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

POSTGRES_URL_ENV = "KNIGHTMIND_TEST_POSTGRES_URL"


def pytest_collection_modifyitems(config, items):
    """Skip ``@pytest.mark.postgres`` tests unless a disposable Postgres is set.

    The gate lives here, once, rather than as a per-file ``skipif``. Tests only
    declare *that* they need Postgres; how that is detected is this hook's
    business. CI selects them with ``-m postgres`` across the whole suite, so a
    new Postgres test in any file is picked up without touching the workflow --
    which is the failure this replaces: the workflow named ``test_jobs.py``
    explicitly, and four concurrency tests added later in
    ``test_review_session_integration.py`` never ran in CI at all.
    """
    if os.getenv(POSTGRES_URL_ENV):
        return
    skip_postgres = pytest.mark.skip(
        reason=f"requires a disposable Postgres (set {POSTGRES_URL_ENV})"
    )
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip_postgres)
