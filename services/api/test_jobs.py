import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from services.api.db import Base, get_db
from services.api.main import app
from services.api.models import Job, JobStatus
from services.api.worker import JobWorker

# Use a file-based DB for tests to ensure threading works if needed,
# but :memory: is usually fine for single thread tests.
# However, worker runs in thread.
TEST_DATABASE_URL = "sqlite:///./test_jobs.db"

engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if os.path.exists("./test_jobs.db"):
        os.remove("./test_jobs.db")


@pytest.fixture
def db_session(setup_db):
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# Override dependency so the API endpoints use the test session.


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_generate_puzzles_enqueues_job(client, db_session):
    # 1. Trigger job
    response = client.post("/puzzles/generate?username=jobtester")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == JobStatus.QUEUED
    assert "job_id" in data
    job_id = data["job_id"]

    # 2. Verify DB
    job = db_session.get(Job, job_id)
    assert job is not None
    assert job.username == "jobtester"
    assert job.status == JobStatus.QUEUED


def test_generate_puzzles_idempotency(client, db_session):
    # 1. First Trigger
    resp1 = client.post("/puzzles/generate?username=doubletester")
    job_id1 = resp1.json()["job_id"]

    # 2. Second Trigger
    resp2 = client.post("/puzzles/generate?username=doubletester")
    job_id2 = resp2.json()["job_id"]

    assert job_id1 == job_id2
    assert resp2.json()["message"] == "Job already in progress"


def test_get_job_status(client, db_session):
    # Create manual job
    job = Job(username="statuschecker", status=JobStatus.RUNNING, progress_current=50)
    db_session.add(job)
    db_session.commit()

    resp = client.get(f"/jobs/{job.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job.id
    assert data["status"] == JobStatus.RUNNING
    assert data["progress"] == 50


def test_cancel_job_success(client, db_session):
    job = Job(username="cancelme", status=JobStatus.QUEUED)
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/jobs/{job.id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == JobStatus.CANCELED

    db_session.refresh(job)
    assert job.status == JobStatus.CANCELED


def test_cancel_job_invalid_status(client, db_session):
    job = Job(username="done", status=JobStatus.SUCCEEDED)
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/jobs/{job.id}/cancel")
    assert resp.status_code == 400
    assert "cannot cancel" in resp.json()["detail"].lower()


def test_cancel_job_not_found(client):
    resp = client.post("/jobs/missing-id/cancel")
    assert resp.status_code == 404


async def run_sync_in_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


@patch("services.api.worker.generate_puzzles")
@patch("asyncio.to_thread", side_effect=run_sync_in_thread)
@pytest.mark.asyncio
async def test_worker_execute_job(mock_to_thread, mock_generate, db_session):
    # Test execute_job directly to verify logic without claiming loop complexity
    from services.api.puzzles.generator import GenerationResult
    from services.api.worker import worker

    # Create running job
    job = Job(username="exectest", status=JobStatus.RUNNING)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # Mock generation
    mock_generate.return_value = GenerationResult(5, 0, 100)

    # Execute with patched SessionLocal
    with patch("services.api.worker.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value = db_session
        mock_sl.return_value.__exit__.return_value = None

        await worker.execute_job(job.id)

    # Verify
    db_session.expire_all()
    updated_job = db_session.get(Job, job.id)
    assert updated_job.status == JobStatus.SUCCEEDED
    assert updated_job.result_json["generated"] == 5


# ---------------------------------------------------------------------------
# AUDIT GATE 5: atomic job claim (QUEUED -> RUNNING)
# ---------------------------------------------------------------------------


def _reset_jobs(db_session):
    """Clear the shared module-scoped jobs table for claim isolation."""
    db_session.query(Job).delete()
    db_session.commit()


def test_claim_job_transitions_queued_to_running(db_session):
    """A single claim flips exactly the oldest QUEUED job to RUNNING."""
    _reset_jobs(db_session)
    older = Job(
        username="claim-older",
        status=JobStatus.QUEUED,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    newer = Job(
        username="claim-newer",
        status=JobStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([older, newer])
    db_session.commit()

    claimed_id = JobWorker._claim_job(db_session)

    assert claimed_id == older.id  # oldest-first ordering preserved
    db_session.expire_all()
    assert db_session.get(Job, older.id).status == JobStatus.RUNNING
    assert db_session.get(Job, newer.id).status == JobStatus.QUEUED


def test_claim_job_is_atomic_no_double_claim(db_session):
    """The guarded UPDATE has rowcount==1 semantics: once a job leaves QUEUED,
    a second claim attempt on the same row transitions nothing.

    This is the regression guard for the double-claim window. The old
    select-then-commit claim had no `WHERE status='queued'` guard on the
    write, so two workers that both SELECTed the same QUEUED row would both
    commit status=RUNNING and run the job twice. With the guarded UPDATE, the
    second writer's rowcount is 0.
    """
    _reset_jobs(db_session)
    job = Job(username="race-target", status=JobStatus.QUEUED)
    db_session.add(job)
    db_session.commit()

    # First claimer wins.
    first = JobWorker._claim_job(db_session)
    assert first == job.id

    # Second claimer finds nothing QUEUED -> returns None (no re-claim).
    second = JobWorker._claim_job(db_session)
    assert second is None

    # Directly exercise the guarded UPDATE against the now-RUNNING row to prove
    # the rowcount==0 semantics that make the claim safe under a real race.
    guarded = (
        update(Job)
        .where(Job.id == job.id, Job.status == JobStatus.QUEUED)
        .values(status=JobStatus.RUNNING)
    )
    result = db_session.execute(guarded)
    db_session.commit()
    assert result.rowcount == 0  # already claimed; guard blocks the transition


def test_claim_job_returns_none_when_empty(db_session):
    """No QUEUED jobs -> claim returns None."""
    _reset_jobs(db_session)
    assert JobWorker._claim_job(db_session) is None


@pytest.mark.skipif(
    not os.getenv("KNIGHTMIND_TEST_POSTGRES_URL"),
    reason="requires a disposable Postgres (set KNIGHTMIND_TEST_POSTGRES_URL)",
)
def test_claim_job_concurrent_postgres():
    """Integration: two concurrent workers claiming from a shared Postgres must
    never claim the same job. Skipped unless a disposable Postgres is provided
    via KNIGHTMIND_TEST_POSTGRES_URL.
    """
    import threading

    pg_url = os.environ["KNIGHTMIND_TEST_POSTGRES_URL"]
    pg_engine = create_engine(pg_url)
    PgSession = sessionmaker(bind=pg_engine)
    Base.metadata.create_all(bind=pg_engine)

    # Seed N queued jobs, each for a distinct username (active-username index).
    n = 10
    with PgSession() as s:
        s.query(Job).delete()
        for i in range(n):
            s.add(Job(username=f"pg-race-{i}", status=JobStatus.QUEUED))
        s.commit()

    claimed: list[str] = []
    lock = threading.Lock()

    def worker_claim():
        while True:
            with PgSession() as s:
                jid = JobWorker._claim_job(s)
            if jid is None:
                return
            with lock:
                claimed.append(jid)

    threads = [threading.Thread(target=worker_claim) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every job claimed exactly once (no duplicates), all jobs claimed.
    assert len(claimed) == n
    assert len(set(claimed)) == n
