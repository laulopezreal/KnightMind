import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


@pytest.fixture(scope="function")
def test_db_instance(monkeypatch, tmp_path):
    """
    Creates a completely isolated file-based database for each test function.
    This avoids all issues with SQLite in-memory sharing, threading, and state leakage.
    The DB file lives in pytest's tmp_path so no artifacts land in the repo.
    """
    db_path = tmp_path / f"test_ops_{uuid.uuid4()}.db"
    db_url = f"sqlite:///{db_path}"

    # Create engine for this specific test
    # Use NullPool to ensure connections are closed promptly, avoiding file locks
    engine = create_engine(
        db_url, connect_args={"check_same_thread": False}, poolclass=NullPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Disable worker for tests (so health check doesn't fail)
    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")

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
    # Ensure models are imported so they are registered in Base
    from services.api.models import Base

    Base.metadata.create_all(bind=engine)

    yield TestingSessionLocal

    # Teardown (tmp_path itself is cleaned up by pytest)
    engine.dispose()
    if db_path.exists():
        try:
            db_path.unlink()
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
    from services.api.db import get_db
    from services.api.main import app

    # Override dependency to use our test session
    app.dependency_overrides[get_db] = lambda: db_session

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def test_ping_endpoint(client):
    response = client.get("/ops/ping")
    assert response.status_code == 200
    assert response.json()["status"] == "pong"


def test_health_endpoint(client, monkeypatch):
    from services.api import ops as ops_module

    # Mock stockfish as available so health check passes
    monkeypatch.setattr(ops_module, "is_engine_available", lambda: (True, "OK"))

    response = client.get("/ops/health")
    assert response.status_code == 200
    data = response.json()
    assert data["db"] == "ok"
    assert data["stockfish"] == "ok"


def test_health_returns_version(client, monkeypatch):
    from services.api import ops as ops_module

    # Mock stockfish as available so health check passes
    monkeypatch.setattr(ops_module, "is_engine_available", lambda: (True, "OK"))

    response = client.get("/ops/health")
    data = response.json()
    assert "version" in data
    assert "sha" in data["version"]


def test_ready_endpoint(client):
    response = client.get("/ops/ready")
    # Stockfish may or may not be available in test env,
    # but the endpoint should return a valid response either way.
    assert response.status_code in (200, 503)
    data = response.json()
    assert "ready" in data
    assert "db" in data
    assert "stockfish" in data
    assert data["db"] == "ok"


def test_ops_status_basic(client, db_session):
    from datetime import datetime, timedelta, timezone

    from services.api.models import Job, JobStatus

    job1 = Job(
        type="puzzle_generation",
        username="testuser",
        status=JobStatus.SUCCEEDED,
        progress_current=100,
        result_json={"generated": 5, "cache_hits": 10, "cache_misses": 2},
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=9),
    )
    db_session.add(job1)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert len(data["recent_jobs"]) >= 1
    assert data["recent_jobs"][0]["username"] == "testuser"


def test_ops_metrics_succeeded_count(client, db_session):
    from datetime import datetime, timedelta, timezone

    from services.api.models import Job, JobStatus

    job = Job(
        type="puzzle_generation",
        username="metrics_user",
        status=JobStatus.SUCCEEDED,
        progress_current=100,
        result_json={"generated": 3},
        created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=29),
    )
    db_session.add(job)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["last_24h"]["jobs_succeeded"] == 1
    assert data["metrics"]["last_24h"]["jobs_failed"] == 0


def test_ops_metrics_failed_count(client, db_session):
    from datetime import datetime, timedelta, timezone

    from services.api.models import Job, JobStatus

    job = Job(
        type="puzzle_generation",
        username="fail_user",
        status=JobStatus.FAILED,
        error_message="Stockfish not found",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=4),
    )
    db_session.add(job)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["last_24h"]["jobs_failed"] == 1


def test_ops_metrics_excludes_old_jobs(client, db_session):
    from datetime import datetime, timedelta, timezone

    from services.api.models import Job, JobStatus

    job = Job(
        type="puzzle_generation",
        username="old_user",
        status=JobStatus.SUCCEEDED,
        progress_current=100,
        result_json={"generated": 5},
        created_at=datetime.now(timezone.utc) - timedelta(hours=25),
        updated_at=datetime.now(timezone.utc) - timedelta(hours=24, minutes=59),
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
    assert data["error"] is None


def test_ops_status_active_job(client, db_session):
    from services.api.models import Job, JobStatus

    job = Job(
        type="puzzle_generation",
        username="active_user",
        status=JobStatus.RUNNING,
        progress_current=45,
        message="Analyzing... ",
    )
    db_session.add(job)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert data["active_job"] is not None
    assert data["active_job"]["username"] == "active_user"
    assert data["active_job"]["status"] == "running"


# --- Failure path tests for /health and /ready endpoints ---


def test_health_returns_503_when_stockfish_unavailable(client, monkeypatch):
    """Test that /health returns 503 when Stockfish is not available."""
    from services.api import ops as ops_module

    monkeypatch.setattr(ops_module, "is_engine_available", lambda: (False, "Not found"))

    response = client.get("/ops/health")
    assert response.status_code == 503
    data = response.json()
    assert data["ok"] is False
    assert data["stockfish"] == "missing"


def test_health_returns_503_when_worker_not_running(client, monkeypatch):
    """Test that /health returns 503 when the worker is not running."""
    from services.api import ops as ops_module
    from services.api import worker as worker_module

    # Make stockfish available
    monkeypatch.setattr(ops_module, "is_engine_available", lambda: (True, "OK"))
    # Make worker appear stopped (and not disabled)
    monkeypatch.delenv("KNIGHTMIND_WORKER_DISABLED", raising=False)
    monkeypatch.setattr(worker_module.worker, "is_running", False)

    response = client.get("/ops/health")
    assert response.status_code == 503
    data = response.json()
    assert data["ok"] is False
    assert data["worker"] == "not_running"


def test_ready_returns_503_when_stockfish_unavailable(client, monkeypatch):
    """Test that /ready returns 503 when Stockfish is not available."""
    from services.api import ops as ops_module

    monkeypatch.setattr(ops_module, "is_engine_available", lambda: (False, "Not found"))

    response = client.get("/ops/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["ready"] is False
    assert data["stockfish"] == "missing"
    assert data["db"] == "ok"


def test_health_ok_when_worker_disabled(client, monkeypatch):
    """Test that /health returns 200 when worker is disabled (not an error state)."""
    from services.api import ops as ops_module

    # Make stockfish available
    monkeypatch.setattr(ops_module, "is_engine_available", lambda: (True, "OK"))
    # Worker is disabled via env var (set in fixture)

    response = client.get("/ops/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["worker"] == "disabled"
