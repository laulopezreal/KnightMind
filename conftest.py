import os

import pytest

# Backend tests build their own SQLite engines/sessions in fixtures, but they
# import the app (and therefore services.api.db) at module import time without
# setting DATABASE_URL. Opt in to the explicit SQLite dev fallback so that
# import doesn't fail fast; the fallback engine itself is never used by tests.
os.environ.setdefault("KNIGHTMIND_DEV_SQLITE", "1")

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
