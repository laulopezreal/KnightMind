import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.api.models import Base, Job, JobStatus
from services.api.worker import JobWorker

# Use a temporary file for tests
TEST_DB_PATH = "test_jobs.db"
TEST_DATABASE_URL = f"sqlite:///./{TEST_DB_PATH}"
test_engine = create_engine(
    TEST_DATABASE_URL, connect_args={"check_same_thread": False}
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    # Dispose first so no pooled connection from a prior test still references
    # the (about-to-be-removed) DB file inode, which otherwise surfaces as
    # "attempt to write a readonly database" on the freshly recreated file.
    test_engine.dispose()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    Base.metadata.create_all(bind=test_engine)
    yield
    test_engine.dispose()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


def test_startup_recovery(monkeypatch):
    # Mock SessionLocal in worker and db modules
    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    # 1. Create a STALE running job (20 mins old)
    stale_job = Job(
        id="stale-123",
        type="puzzle_generation",
        username="testuser",
        status=JobStatus.RUNNING,
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=25),
    )

    # 2. Create a FRESH running job (2 mins old)
    fresh_job = Job(
        id="fresh-456",
        type="puzzle_generation",
        username="testuser2",
        status=JobStatus.RUNNING,
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    db.add(stale_job)
    db.add(fresh_job)
    db.commit()

    # 3. Run recovery in a thread-safe way as the worker does
    import asyncio

    asyncio.run(worker.cleanup_stuck_jobs())

    # 4. Verify results
    db.refresh(stale_job)
    db.refresh(fresh_job)

    assert stale_job.status == JobStatus.QUEUED
    assert "Recovered" in stale_job.message
    assert fresh_job.status == JobStatus.RUNNING

    assert worker.recovery_stats["recovered_count"] == 1
    assert worker.recovery_stats["last_recovery_at"] is not None

    db.close()


def test_heartbeat_advances_updated_at(monkeypatch):
    """The generator's progress callback bumps updated_at (liveness heartbeat)
    and returns False for a still-running job.
    """
    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    old_ts = datetime.now(timezone.utc) - timedelta(minutes=20)
    job = Job(
        id="beat-1",
        type="puzzle_generation",
        username="beatuser",
        status=JobStatus.RUNNING,
        updated_at=old_ts,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=25),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    before = job.updated_at  # read back as stored (SQLite -> naive)

    canceled = worker._heartbeat_and_check_cancellation("beat-1")

    assert canceled is False
    db.refresh(job)
    assert job.updated_at > before  # heartbeat advanced liveness timestamp
    db.close()


def test_heartbeat_reports_cancellation(monkeypatch):
    """A canceled job makes the callback return True so the generator stops."""
    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    job = Job(
        id="beat-cancel",
        type="puzzle_generation",
        username="beatcancel",
        status=JobStatus.CANCELED,
        updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()

    assert worker._heartbeat_and_check_cancellation("beat-cancel") is True
    db.close()


def test_live_long_job_not_reset_but_crashed_one_is(monkeypatch):
    """Crash recovery must NOT reset a legitimately long-running job that is
    still making progress, but MUST reset a job that has genuinely stalled.

    Regression for the bug where cleanup_stuck_jobs reset any RUNNING job older
    than 15 min purely on wall-clock time since the claim. With no heartbeat, a
    live deep-analysis job that had been running 20 min would be reset and
    re-run (duplicate generation). Now a live job heartbeats updated_at, so it
    survives; a crashed job stops heartbeating and is correctly recovered.
    """
    import asyncio

    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    # Both jobs were CLAIMED 20 min ago (created/claim time is old).
    claim_time = datetime.now(timezone.utc) - timedelta(minutes=20)

    live_job = Job(
        id="live-long",
        type="puzzle_generation",
        username="liveuser",
        status=JobStatus.RUNNING,
        updated_at=claim_time,
        created_at=claim_time,
    )
    crashed_job = Job(
        id="crashed",
        type="puzzle_generation",
        username="crasheduser",
        status=JobStatus.RUNNING,
        updated_at=claim_time,
        created_at=claim_time,
    )
    db.add_all([live_job, crashed_job])
    db.commit()

    # The live job makes progress -> its heartbeat refreshes updated_at.
    # The crashed job never heartbeats (its worker died).
    worker._heartbeat_and_check_cancellation("live-long")

    asyncio.run(worker.cleanup_stuck_jobs())

    db.refresh(live_job)
    db.refresh(crashed_job)

    # Live long-running job survives; crashed job is recovered to QUEUED.
    assert live_job.status == JobStatus.RUNNING
    assert crashed_job.status == JobStatus.QUEUED
    assert "Recovered" in crashed_job.message
    db.close()
