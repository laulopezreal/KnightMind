import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from services.api.models import Job, JobStatus, Base
from services.api.db import engine, SessionLocal
from services.api.worker import JobWorker

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import os

# Use a temporary file for tests
TEST_DB_PATH = "test_jobs.db"
TEST_DATABASE_URL = f"sqlite:///./{TEST_DB_PATH}"
test_engine = create_engine(
    TEST_DATABASE_URL, connect_args={"check_same_thread": False}
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    Base.metadata.create_all(bind=test_engine)
    yield
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
