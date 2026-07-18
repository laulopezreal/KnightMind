"""Tests for the tailnet operator gate (services/api/auth.require_operator).

Covers the dependency logic directly (fast, no DB) and the HTTP wiring on the
gated endpoints (/ops/status, bare /users), which must 404 without a valid
Tailscale identity header and succeed with one.
"""

import os
import sys
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.api.auth import require_operator  # noqa: E402

HEADER = "Tailscale-User-Login"


# --- Unit tests: the dependency logic ---------------------------------------


def test_require_operator_rejects_missing_header():
    with pytest.raises(HTTPException) as exc:
        require_operator(tailscale_user_login=None)
    # 404 (not 403) so the public internet can't confirm the endpoint exists.
    assert exc.value.status_code == 404


def test_require_operator_allows_any_identity_when_unpinned(monkeypatch):
    monkeypatch.delenv("KNIGHTMIND_OPS_TAILNET_USER", raising=False)
    assert require_operator(tailscale_user_login="anyone@github") == "anyone@github"


def test_require_operator_pins_to_configured_user(monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_OPS_TAILNET_USER", "owner@github")
    # Matching identity is allowed.
    assert require_operator(tailscale_user_login="owner@github") == "owner@github"
    # A different (but still authenticated) tailnet identity is rejected.
    with pytest.raises(HTTPException) as exc:
        require_operator(tailscale_user_login="intruder@github")
    assert exc.value.status_code == 404


# --- HTTP wiring: gated endpoints -------------------------------------------


@pytest.fixture(scope="function")
def gated_client(monkeypatch, tmp_path):
    """A TestClient with a real (isolated) DB but the REAL operator gate wired.

    Only get_db is overridden — require_operator is left intact so the header
    behaviour is exercised end to end.
    """
    db_path = tmp_path / f"test_gate_{uuid.uuid4()}.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")

    from services.api import db as db_module
    from services.api import worker as worker_module

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(worker_module, "SessionLocal", TestingSessionLocal)

    from services.api.models import Base

    Base.metadata.create_all(bind=engine)

    from services.api.db import get_db
    from services.api.main import app

    session = TestingSessionLocal()
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_ops_status_blocked_without_header(gated_client):
    assert gated_client.get("/ops/status").status_code == 404


def test_ops_status_allowed_with_header(gated_client):
    resp = gated_client.get("/ops/status", headers={HEADER: "owner@github"})
    assert resp.status_code == 200
    assert "recent_jobs" in resp.json()


def test_users_list_blocked_without_header(gated_client):
    assert gated_client.get("/users").status_code == 404


def test_users_list_allowed_with_header(gated_client):
    resp = gated_client.get("/users", headers={HEADER: "owner@github"})
    assert resp.status_code == 200
    assert "users" in resp.json()


def test_liveness_probes_stay_public(gated_client):
    # Liveness/readiness must NOT be gated (uptime monitors, Docker healthcheck).
    assert gated_client.get("/ops/ping").status_code == 200
    assert gated_client.get("/ops/health").status_code in (200, 503)
    assert gated_client.get("/ops/ready").status_code in (200, 503)
