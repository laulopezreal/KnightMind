import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from services.api.db import Base, get_db
from services.api.main import app
from services.api.models import Job, JobStatus

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
    from services.api.worker import worker
    from services.api.puzzles.generator import GenerationResult
    
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
