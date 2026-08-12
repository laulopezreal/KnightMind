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


# --- worker running as its own service -------------------------------------
#
# The API cannot see an out-of-process worker, so /ops/health reads the
# heartbeat rows it writes. These pin the three answers, because getting this
# wrong reports a healthy system with a dead queue -- and the deploy gate probes
# this endpoint.


def _external_worker(monkeypatch):
    from services.api import ops as ops_module

    monkeypatch.setattr(ops_module, "is_engine_available", lambda: (True, "OK"))
    monkeypatch.delenv("KNIGHTMIND_WORKER_DISABLED", raising=False)
    monkeypatch.setenv("KNIGHTMIND_WORKER_EXTERNAL", "true")


def _beat(db_session, worker_id, age_seconds):
    from datetime import datetime, timedelta, timezone

    from services.api.models import WorkerHeartbeat

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.add(
        WorkerHeartbeat(
            worker_id=worker_id,
            started_at=now,
            beat_at=now - timedelta(seconds=age_seconds),
        )
    )
    db_session.commit()


def test_health_ok_when_external_worker_is_beating(client, db_session, monkeypatch):
    _external_worker(monkeypatch)
    _beat(db_session, "worker-1", age_seconds=1)

    response = client.get("/ops/health")
    assert response.status_code == 200
    assert response.json()["worker"] == "ok"


def test_health_503_when_external_worker_stops_beating(client, db_session, monkeypatch):
    """A crashed worker stops writing; its row ages out. That is the signal."""
    _external_worker(monkeypatch)
    _beat(db_session, "worker-1", age_seconds=600)

    response = client.get("/ops/health")
    assert response.status_code == 503
    assert response.json()["worker"] == "stale"


def test_health_503_when_no_external_worker_has_ever_run(client, monkeypatch):
    _external_worker(monkeypatch)

    response = client.get("/ops/health")
    assert response.status_code == 503
    assert response.json()["worker"] == "not_running"


def test_a_second_replica_keeps_the_service_healthy(client, db_session, monkeypatch):
    """Liveness is the FRESHEST beat, not every beat.

    Scaling the worker gives each replica its own row. If one dies while another
    keeps working the queue is still being served, so the endpoint must not go
    unhealthy on the stale row -- which a per-row check would.
    """
    _external_worker(monkeypatch)
    _beat(db_session, "worker-dead", age_seconds=600)
    _beat(db_session, "worker-alive", age_seconds=1)

    response = client.get("/ops/health")
    assert response.status_code == 200
    assert response.json()["worker"] == "ok"


@pytest.mark.postgres
def test_the_beat_is_stored_as_naive_utc(db_session, monkeypatch):
    """The worker's own write path, under a NON-UTC session timezone.

    The other heartbeat tests build their rows with `.replace(tzinfo=None)`, so
    they never exercise how the worker writes -- and under the default UTC
    session an aware write stores the same value, so they cannot catch this at
    all. Passing an AWARE datetime makes psycopg send a timestamptz, which
    Postgres casts to this naive column THROUGH the session TimeZone: one
    environment variable away from reporting every live worker stale, or every
    dead one healthy. The timezone here is what makes the assertion real.
    """
    from datetime import datetime, timezone

    from sqlalchemy import text

    from services.api import db as db_module
    from services.api import worker as worker_module
    from services.api.models import WorkerHeartbeat

    dbname = db_session.execute(text("SELECT current_database()")).scalar_one()
    db_session.execute(
        text(f"ALTER DATABASE \"{dbname}\" SET timezone TO 'America/New_York'")
    )
    db_session.commit()
    # New connections only; drop the pooled ones so the setting takes effect.
    db_module.engine.dispose()
    try:
        w = worker_module.JobWorker()
        w.worker_id = "tz-probe"
        w._beat()

        stored = db_session.execute(
            text("SELECT beat_at FROM worker_heartbeats WHERE worker_id = 'tz-probe'")
        ).scalar_one()
        drift = abs(
            (datetime.now(timezone.utc).replace(tzinfo=None) - stored).total_seconds()
        )
        assert drift < 60, (
            f"beat stored {drift:.0f}s from now -- written as an aware datetime "
            "and cast through the session timezone"
        )
    finally:
        db_session.execute(text(f'ALTER DATABASE "{dbname}" RESET timezone'))
        db_session.query(WorkerHeartbeat).filter(
            WorkerHeartbeat.worker_id == "tz-probe"
        ).delete()
        db_session.commit()
        db_module.engine.dispose()


@pytest.mark.postgres
def test_a_departed_worker_does_not_keep_health_green(client, db_session, monkeypatch):
    """The deploy-gate hole: health reads the freshest beat across ALL rows.

    `compose up -d` stops the old worker before the new one is serving, and the
    old worker's last beat is only seconds old — so the gate would sample it,
    see `ok`, and declare a deploy successful while the new container was still
    starting or crash-looping. A worker withdraws its row on an orderly exit for
    exactly this reason.
    """
    import asyncio

    from services.api import worker as worker_module
    from services.api.models import WorkerHeartbeat

    _external_worker(monkeypatch)

    w = worker_module.JobWorker()
    w.worker_id = "departing-worker"
    w._beat()

    # While it is up, health is green.
    assert client.get("/ops/health").json()["worker"] == "ok"

    asyncio.run(asyncio.to_thread(w._withdraw))

    # Gone: its beat must not vouch for a worker that is no longer there.
    assert (
        db_session.query(WorkerHeartbeat)
        .filter(WorkerHeartbeat.worker_id == "departing-worker")
        .count()
        == 0
    )
    response = client.get("/ops/health")
    assert response.status_code == 503
    assert response.json()["worker"] == "not_running"


@pytest.mark.postgres
def test_the_heartbeat_purge_spares_live_workers(db_session):
    """Had no test at all: `WHERE true` survived the whole suite.

    It deletes rows for workers that will never beat again. Deleting a LIVE
    worker's row flips health to not_running on a healthy system, which is the
    same false alarm the withdrawal-on-exit fix exists to avoid — in reverse.
    """
    from datetime import datetime, timedelta, timezone

    from services.api.jobs.cleanup_sessions import purge_dead_worker_heartbeats
    from services.api.models import WorkerHeartbeat

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.add(
        WorkerHeartbeat(
            worker_id="long-lived",
            # Up for over a week, still beating: must survive.
            started_at=now - timedelta(days=9),
            beat_at=now,
        )
    )
    db_session.add(
        WorkerHeartbeat(
            worker_id="long-dead",
            started_at=now - timedelta(days=20),
            beat_at=now - timedelta(days=9),
        )
    )
    db_session.commit()

    purge_dead_worker_heartbeats(db_session)

    remaining = {
        row.worker_id
        for row in db_session.query(WorkerHeartbeat).all()
        if row.worker_id in {"long-lived", "long-dead"}
    }
    assert remaining == {"long-lived"}, remaining

    db_session.query(WorkerHeartbeat).filter(
        WorkerHeartbeat.worker_id.in_(["long-lived", "long-dead"])
    ).delete(synchronize_session=False)
    db_session.commit()


# --- health must attest to the QUEUE, not just to the process ----------------
#
# The heartbeat is written 0.6s after the worker starts and never depended on
# any work succeeding. So a worker that boots, beats, claims a job and dies
# executing it -- restarted by `restart: unless-stopped`, forever -- reported a
# stable green health check with zero throughput. Same failure the deploy's
# `compose build` fix removed at the image layer, one layer down.


def _queued_job(db, *, age_seconds: int, status: str = "queued", username: str = "u"):
    from datetime import datetime, timedelta, timezone

    from services.api.models import Job

    job = Job(
        username=username,
        type="puzzle_generation",
        status=status,
        params={},
        created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(seconds=age_seconds),
    )
    db.add(job)
    db.commit()
    return job


def test_health_503_when_the_worker_beats_but_the_queue_is_stalled(
    client, db_session, monkeypatch
):
    """The finding: beating is not working."""
    _external_worker(monkeypatch)
    _beat(db_session, "worker-1", age_seconds=1)
    _queued_job(db_session, age_seconds=3600)

    response = client.get("/ops/health")
    assert response.status_code == 503
    assert response.json()["worker"] == "stalled"


def test_a_busy_worker_with_work_queued_behind_it_is_not_stalled(
    client, db_session, monkeypatch
):
    """Puzzle generation legitimately runs for minutes with jobs waiting. The
    stall check is scoped to "and nothing is RUNNING" precisely so this stays
    healthy -- otherwise the fix would be worse than the bug it replaces."""
    _external_worker(monkeypatch)
    _beat(db_session, "worker-1", age_seconds=1)
    _queued_job(db_session, age_seconds=3600, username="waiting")
    _queued_job(db_session, age_seconds=60, status="running", username="busy")

    response = client.get("/ops/health")
    assert response.status_code == 200
    assert response.json()["worker"] == "ok"


def test_a_recently_queued_job_is_not_a_stall(client, db_session, monkeypatch):
    """A job is normally claimed within one poll. Only a job that has waited
    far past that says nobody is taking them."""
    _external_worker(monkeypatch)
    _beat(db_session, "worker-1", age_seconds=1)
    _queued_job(db_session, age_seconds=5)

    response = client.get("/ops/health")
    assert response.status_code == 200
    assert response.json()["worker"] == "ok"


def test_an_empty_queue_is_not_a_stall(client, db_session, monkeypatch):
    _external_worker(monkeypatch)
    _beat(db_session, "worker-1", age_seconds=1)

    response = client.get("/ops/health")
    assert response.status_code == 200
    assert response.json()["worker"] == "ok"


def test_health_stays_structured_when_the_heartbeat_table_is_unreadable(
    client, monkeypatch
):
    """An operator part-way through `alembic downgrade` has a reachable database
    with no worker_heartbeats. The read used to raise out of the endpoint as a
    bare 500 with no body -- losing the db/worker/stockfish breakdown at exactly
    the moment it is needed. 503 with "unknown" is still unhealthy; it just says
    which part."""
    _external_worker(monkeypatch)

    from services.api import ops as ops_module

    def boom(_db):
        raise RuntimeError('relation "worker_heartbeats" does not exist')

    monkeypatch.setattr(ops_module, "_worker_status_from_heartbeat", boom)

    response = client.get("/ops/health")
    assert response.status_code == 503
    body = response.json()
    assert body["worker"] == "unknown"
    # The whole point: the other subsystems still report.
    assert body["db"] == "ok"
    assert "stockfish" in body
