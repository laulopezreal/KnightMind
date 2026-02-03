import pytest
import os
import sys
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


@pytest.fixture(scope="function")
def test_db_instance(monkeypatch):
    """
    Creates a completely isolated file-based database for each test function.
    This avoids all issues with SQLite in-memory sharing, threading, and state leakage.
    """
    db_filename = f"test_ops_{uuid.uuid4()}.db"
    db_url = f"sqlite:///./{db_filename}"
    
    # Create engine for this specific test
    # Use NullPool to ensure connections are closed promptly, avoiding file locks
    engine = create_engine(db_url, connect_args={"check_same_thread": False}, poolclass=NullPool)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # CRITICAL: Monkeypatch EVERYTHING to use this specific engine/session
    from services.api import db as db_module
    from services.api import worker as worker_module
    
    monkeypatch.setattr(db_module, "SQLALCHEMY_DATABASE_URL", db_url)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(worker_module, "SessionLocal", TestingSessionLocal)
    
    try:
        from services.api import main as main_module
        monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    except (ImportError, AttributeError):
        pass

    # Create tables
    from services.api.models import Base
    # Ensure models are imported so they are registered in Base
    import services.api.models
    Base.metadata.create_all(bind=engine)
    
    yield TestingSessionLocal

    # Teardown
    engine.dispose()
    if os.path.exists(db_filename):
        try:
            os.remove(db_filename)
        except PermissionError:
            pass

@pytest.fixture(scope="function")
def db_session(test_db_instance):
    """Returns a session for the current test's isolated database."""
    session = test_db_instance()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture(scope="function")
def client(db_session):
    from services.api.main import app
    from services.api.db import get_db
    
    # Override dependency to use our test session
    app.dependency_overrides[get_db] = lambda: db_session
    
    with TestClient(app) as c:
        yield c
    
    app.dependency_overrides.clear()

def test_health_endpoint(client):
    response = client.get("/ops/health")
    assert response.status_code == 200
    data = response.json()
    assert data["db"] == "ok"

def test_ops_status_basic(client, db_session):
    from services.api.models import Job, JobStatus
    from datetime import datetime, timezone, timedelta
    
    job1 = Job(
        type="puzzle_generation",
        username="testuser",
        status=JobStatus.SUCCEEDED,
        progress_current=100,
        result_json={"generated": 5, "cache_hits": 10, "cache_misses": 2},
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=9)
    )
    db_session.add(job1)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert len(data["recent_jobs"]) >= 1
    assert data["recent_jobs"][0]["username"] == "testuser"

def test_ops_metrics_succeeded_count(client, db_session):
    from services.api.models import Job, JobStatus
    from datetime import datetime, timezone, timedelta

    job = Job(
        type="puzzle_generation",
        username="metrics_user",
        status=JobStatus.SUCCEEDED,
        progress_current=100,
        result_json={"generated": 3},
        created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=29)
    )
    db_session.add(job)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["last_24h"]["jobs_succeeded"] == 1
    assert data["metrics"]["last_24h"]["jobs_failed"] == 0

def test_ops_metrics_failed_count(client, db_session):
    from services.api.models import Job, JobStatus
    from datetime import datetime, timezone, timedelta

    job = Job(
        type="puzzle_generation",
        username="fail_user",
        status=JobStatus.FAILED,
        error_message="Stockfish not found",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=4)
    )
    db_session.add(job)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["last_24h"]["jobs_failed"] == 1

def test_ops_metrics_excludes_old_jobs(client, db_session):
    from services.api.models import Job, JobStatus
    from datetime import datetime, timezone, timedelta

    job = Job(
        type="puzzle_generation",
        username="old_user",
        status=JobStatus.SUCCEEDED,
        progress_current=100,
        result_json={"generated": 5},
        created_at=datetime.now(timezone.utc) - timedelta(hours=25),
        updated_at=datetime.now(timezone.utc) - timedelta(hours=24, minutes=59)
    )
    db_session.add(job)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["last_24h"]["jobs_succeeded"] == 0
    assert data["metrics"]["last_24h"]["jobs_failed"] == 0

def test_job_status_returns_error_field(client, db_session):
    from services.api.models import Job, JobStatus

    job = Job(
        type="puzzle_generation",
        username="error_user",
        status=JobStatus.FAILED,
        message="Processing games",
        error_message="Stockfish binary not found at /usr/bin/stockfish",
    )
    db_session.add(job)
    db_session.commit()

    response = client.get(f"/jobs/{job.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["error"] == "Stockfish binary not found at /usr/bin/stockfish"
    assert data["message"] == "Processing games"

def test_cancel_job_returns_error_field(client, db_session):
    from services.api.models import Job, JobStatus

    job = Job(
        type="puzzle_generation",
        username="cancel_error_user",
        status=JobStatus.RUNNING,
    )
    db_session.add(job)
    db_session.commit()

    response = client.post(f"/jobs/{job.id}/cancel")
    assert response.status_code == 200
    data = response.json()
    assert "error" in data

def test_ops_status_active_job(client, db_session):
    from services.api.models import Job, JobStatus
    
    job = Job(
        type="puzzle_generation",
        username="active_user",
        status=JobStatus.RUNNING,
        progress_current=45,
        message="Analyzing... "
    )
    db_session.add(job)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert data["active_job"] is not None
    assert data["active_job"]["username"] == "active_user"
    assert data["active_job"]["status"] == "running"
