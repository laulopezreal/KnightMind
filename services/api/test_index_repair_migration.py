"""The repair migration, run against databases that are actually drifted.

The tests in ``test_active_job_index.py`` do NOT cover this and cannot: the
suite's conftest builds its schema with ``Base.metadata.create_all`` from
``models.py``, so those tests assert that SQLAlchemy created the index the model
declares. Verified by deleting this migration entirely — all five still passed.

So this file drives Alembic directly against throwaway databases it creates
itself, drifts them into each broken shape by hand, and asserts the migration
corrects them. Deleting the migration fails these.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from conftest import TEST_DATABASE_URL  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "services" / "api" / "alembic.ini"

# The revision immediately before the repair; also production's revision at the
# time the drift was found.
BEFORE_REPAIR = "399a35540403"

CORRECT = (
    "CREATE UNIQUE INDEX ix_jobs_active_username ON jobs (username, type) "
    "WHERE status IN ('queued', 'running')"
)
NARROW = (
    "CREATE UNIQUE INDEX ix_jobs_active_username ON jobs (username) "
    "WHERE status IN ('queued', 'running')"
)
NOT_UNIQUE = (
    "CREATE INDEX ix_jobs_active_username ON jobs (username, type) "
    "WHERE status IN ('queued', 'running')"
)
NOT_PARTIAL = "CREATE UNIQUE INDEX ix_jobs_active_username ON jobs (username, type)"


def _admin_url() -> str:
    return TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"


@pytest.fixture
def drifted_db():
    """A database built from the migration chain, stopped before the repair.

    Yields (url, apply_shape). ``apply_shape`` replaces the index with a given
    definition, so each test can drift it differently.
    """
    name = f"knightmind_test_repair_{uuid.uuid4().hex[:10]}"
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()

    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/" + name

    def alembic(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            # sys.executable, not "python": the suite runs from a venv
            # invoked by absolute path and "python" is not on PATH here.
            [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
            cwd=REPO_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            capture_output=True,
            text=True,
        )

    result = alembic("upgrade", BEFORE_REPAIR)
    assert result.returncode == 0, result.stderr

    engine = create_engine(url)

    def apply_shape(definition: str) -> None:
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS ix_jobs_active_username"))
            conn.execute(text(definition))

    try:
        yield url, apply_shape, alembic, engine
    finally:
        engine.dispose()
        admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _definition(engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_jobs_active_username'"
            )
        ).scalar_one_or_none()


def _is_unique(engine) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT i.indisunique FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = 'ix_jobs_active_username'"
                )
            ).scalar_one()
        )


@pytest.mark.parametrize(
    "shape,label",
    [
        (NARROW, "production's drifted shape"),
        (NOT_UNIQUE, "present but not UNIQUE — enforces nothing"),
        (NOT_PARTIAL, "present but not partial — blocks finished jobs too"),
    ],
)
def test_the_migration_repairs_a_drifted_index(drifted_db, shape, label):
    url, apply_shape, alembic, engine = drifted_db
    apply_shape(shape)

    result = alembic("upgrade", "head")
    assert result.returncode == 0, f"{label}: {result.stderr}"

    definition = _definition(engine)
    assert definition is not None, label
    assert "username, type" in definition, f"{label}: {definition}"
    assert _is_unique(engine), f"{label}: index is not UNIQUE — {definition}"
    assert "WHERE" in definition, f"{label}: index is not partial — {definition}"


def test_the_migration_recreates_a_missing_index(drifted_db):
    url, apply_shape, alembic, engine = drifted_db
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_jobs_active_username"))

    assert alembic("upgrade", "head").returncode == 0
    assert _is_unique(engine)


def test_the_migration_is_a_no_op_on_a_correct_index(drifted_db):
    """It must not drop and rebuild a uniqueness guarantee for nothing."""
    url, apply_shape, alembic, engine = drifted_db
    apply_shape(CORRECT)
    before = _definition(engine)

    assert alembic("upgrade", "head").returncode == 0

    assert _definition(engine) == before


def test_the_repaired_index_still_enforces_one_job_per_user_per_type(drifted_db):
    """Widening must not lose the invariant the index exists for."""
    url, apply_shape, alembic, engine = drifted_db
    apply_shape(NARROW)
    assert alembic("upgrade", "head").returncode == 0

    insert = text(
        "INSERT INTO jobs (id, username, type, status, progress_current, "
        "progress_total, created_at, updated_at) VALUES (:id, 'u', :t, "
        "'queued', 0, 0, now(), now())"
    )
    with engine.begin() as conn:
        conn.execute(insert, {"id": "a", "t": "diagnosis"})
        # A different TYPE is allowed -- the bug being repaired.
        conn.execute(insert, {"id": "b", "t": "puzzle_generation"})

    with pytest.raises(Exception):  # noqa: B017 - the driver's IntegrityError
        with engine.begin() as conn:
            conn.execute(insert, {"id": "c", "t": "diagnosis"})
