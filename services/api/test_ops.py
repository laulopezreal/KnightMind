import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


@pytest.fixture(scope="function")
def test_db_instance(monkeypatch, db_engine, db_url):
    """Point the app's engine and session factory at the shared test database.

    This used to build its own throwaway SQLite file per test, for the reasons
    the old docstring gave: in-memory sharing, threading and state leakage. The
    shared Postgres fixture handles all three (its schema is truncated between
    tests), and using it means /ops/health and /ops/ready are exercised against
    the database production actually runs.
    """
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    # Disable worker for tests (so health check doesn't fail)
    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")

    # The ops probes read the module-level engine and SessionLocal directly
    # rather than through the request dependency, so both have to be redirected.
    from services.api import db as db_module
    from services.api import worker as worker_module

    monkeypatch.setattr(db_module, "SQLALCHEMY_DATABASE_URL", db_url)
    monkeypatch.setattr(db_module, "engine", db_engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_local)
    monkeypatch.setattr(worker_module, "SessionLocal", session_local)

    try:
        from services.api import main as main_module

        monkeypatch.setattr(main_module, "SessionLocal", session_local)
    except (ImportError, AttributeError):
        pass

    yield session_local


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
    from services.api.auth import require_operator
    from services.api.db import get_db
    from services.api.main import app

    # Override dependency to use our test session
    app.dependency_overrides[get_db] = lambda: db_session
    # Treat the default test client as an authenticated tailnet operator so the
    # operator gate on /ops/status is exercised as "allowed". The gate itself is
    # covered explicitly in test_ops_gate.py.
    app.dependency_overrides[require_operator] = lambda: "test@operator"

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


def test_health_omits_version_metadata(client, monkeypatch):
    """Public /health must NOT leak deployment metadata (git sha / build time).
    It is unauthed, so the exact deployed commit would be exposed to anonymous
    callers. Version info lives only on the operator-gated /status now."""
    from services.api import ops as ops_module

    # Mock stockfish as available so health check passes
    monkeypatch.setattr(ops_module, "is_engine_available", lambda: (True, "OK"))

    response = client.get("/ops/health")
    data = response.json()
    assert "version" not in data
    assert "sha" not in data
    assert "built_at" not in data


def test_status_still_exposes_version_metadata(client, monkeypatch):
    """The operator-gated /status may still carry deployment metadata."""
    monkeypatch.setenv("GIT_SHA", "abc1234")
    monkeypatch.setenv("BUILD_TIME", "2026-01-01T00:00:00+00:00")

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert data["version"]["sha"] == "abc1234"
    assert data["version"]["built_at"] == "2026-01-01T00:00:00+00:00"


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


def test_health_and_ready_return_503_when_db_unavailable(test_db_instance, monkeypatch):
    """Regression guard: /health and /ready must check the DB session injected
    via get_db, not some module-level engine bound at import time. Breaking the
    injected session must surface as db=error and HTTP 503. (ops.py once held a
    stale `from services.api.db import ... engine` name-binding import, removed
    in #127; this pins the dependency-injected behavior.)"""
    from services.api import ops as ops_module
    from services.api.db import get_db
    from services.api.main import app

    # Stockfish available, so the DB is the only failing component
    monkeypatch.setattr(ops_module, "is_engine_available", lambda: (True, "OK"))

    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("simulated database outage")

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        with TestClient(app) as c:
            health = c.get("/ops/health")
            ready = c.get("/ops/ready")
    finally:
        app.dependency_overrides.clear()

    assert health.status_code == 503
    health_data = health.json()
    assert health_data["ok"] is False
    assert health_data["db"] == "error"
    assert health_data["stockfish"] == "ok"

    assert ready.status_code == 503
    ready_data = ready.json()
    assert ready_data["ready"] is False
    assert ready_data["db"] == "error"
    assert ready_data["stockfish"] == "ok"


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
