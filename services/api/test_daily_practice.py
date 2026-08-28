"""
Tests for the daily_practice extension to the dashboard endpoint.

Covers the five backend-truth cases from the product spec:
  1. No completed session today -> completed_today=false, completed_session_at=null.
  2. One or more completed sessions today -> completed_today=true, newest timestamp.
  3. UTC midnight boundary: a session just before UTC midnight is in the previous
     day; one just after belongs to today.
  4. An incomplete (not-yet-completed) session does not mark the day complete.
  5. Existing dashboard ownership behaviour and all existing fields are preserved.

Auth is OFF in this suite (KNIGHTMIND_REQUIRE_AUTH not set), so
assert_owns_username is a NO-OP and no Account rows are required.
"""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from services.api.day_boundary import utc_today
from services.api.db import get_db
from services.api.main import app
from services.api.models import TrainingSession


@pytest.fixture
def client_with_db(db_session, monkeypatch):
    """Provide a TestClient wired to the test db_session via dependency override."""
    def override_get_db():
        """Yield the shared test db_session in place of the real get_db dependency."""
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        "services.api.main.SessionLocal", sessionmaker(bind=db_session.get_bind())
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _add_session(db_session, username: str, completed_at=None) -> TrainingSession:
    """Create a minimal but valid TrainingSession, optionally with completed_at."""
    s = TrainingSession(
        username=username,
        requested_n=5,
        session_type="standard",
    )
    if completed_at is not None:
        s.completed_at = completed_at
    db_session.add(s)
    db_session.commit()
    return s


def _dashboard(client, username: str):
    """Call the dashboard endpoint and return the parsed JSON body."""
    resp = client.get(f"/users/{username}/dashboard")
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Case 1: No completed session today
# ---------------------------------------------------------------------------
def test_no_completion_today_returns_false(client_with_db, db_session):
    """A user with no session today gets completed_today=false."""
    data = _dashboard(client_with_db, "nocomp_dp")

    dp = data["daily_practice"]
    assert dp["completed_today"] is False
    assert dp["completed_session_at"] is None


# ---------------------------------------------------------------------------
# Case 2: Newest completion today
# ---------------------------------------------------------------------------
def test_newest_completion_today_returns_true(client_with_db, db_session):
    """A user with sessions today gets completed_today=true and the newest timestamp."""
    today = utc_today()
    earlier = datetime(today.year, today.month, today.day, 8, 0, 0)
    later = datetime(today.year, today.month, today.day, 14, 0, 0)

    _add_session(db_session, "twosess_dp", completed_at=earlier)
    _add_session(db_session, "twosess_dp", completed_at=later)

    data = _dashboard(client_with_db, "twosess_dp")
    dp = data["daily_practice"]

    assert dp["completed_today"] is True
    # The newest qualifying timestamp must be returned.
    assert dp["completed_session_at"] is not None
    # Verify it matches the later of the two (14:00).
    ts = datetime.fromisoformat(dp["completed_session_at"].replace("Z", "+00:00"))
    assert ts.hour == 14


# ---------------------------------------------------------------------------
# Case 3: UTC midnight boundary
# ---------------------------------------------------------------------------
def test_utc_midnight_boundary_yesterday(client_with_db, db_session):
    """A session one second before today's UTC midnight is in the previous day."""
    today = utc_today()
    # One second before today's start = yesterday.
    yesterday_end = datetime(today.year, today.month, today.day, 0, 0, 0) - timedelta(seconds=1)
    _add_session(db_session, "boundary_dp1", completed_at=yesterday_end)

    data = _dashboard(client_with_db, "boundary_dp1")
    dp = data["daily_practice"]

    assert dp["completed_today"] is False
    assert dp["completed_session_at"] is None


def test_utc_midnight_boundary_today_start(client_with_db, db_session):
    """A session at 00:00:00 UTC today is classified as today."""
    today = utc_today()
    today_start = datetime(today.year, today.month, today.day, 0, 0, 0)
    _add_session(db_session, "boundary_dp2", completed_at=today_start)

    data = _dashboard(client_with_db, "boundary_dp2")
    dp = data["daily_practice"]

    assert dp["completed_today"] is True
    assert dp["completed_session_at"] is not None


# ---------------------------------------------------------------------------
# Case 4: Incomplete session excluded
# ---------------------------------------------------------------------------
def test_incomplete_session_excluded(client_with_db, db_session):
    """A session without completed_at must not mark the day complete."""
    # Add an in-progress session (no completed_at).
    _add_session(db_session, "incomplete_dp", completed_at=None)

    data = _dashboard(client_with_db, "incomplete_dp")
    dp = data["daily_practice"]

    assert dp["completed_today"] is False
    assert dp["completed_session_at"] is None


# ---------------------------------------------------------------------------
# Case 5: Existing fields and ownership preserved
# ---------------------------------------------------------------------------
def test_existing_dashboard_fields_preserved(client_with_db, db_session):
    """Adding daily_practice must not drop or alter any existing dashboard field."""
    data = _dashboard(client_with_db, "preserve_dp")

    # All pre-existing top-level fields must still be present.
    assert "username" in data
    assert "last_session_at" in data
    assert "days_since_last_session" in data
    assert "total_sessions" in data
    assert "training_streak_days" in data
    assert "recent_form" in data
    assert "schedule" in data
    assert "needs_warmup" in data
    # And the new field.
    assert "daily_practice" in data


def test_ownership_boundary_sessions_isolated(client_with_db, db_session):
    """Sessions from one username must not contribute to another's daily_practice."""
    today = utc_today()
    ts = datetime(today.year, today.month, today.day, 10, 0, 0)
    _add_session(db_session, "owner_b_dp", completed_at=ts)

    # owner_a has no sessions today.
    data_a = _dashboard(client_with_db, "owner_a_dp")
    assert data_a["daily_practice"]["completed_today"] is False

    # owner_b has a session.
    data_b = _dashboard(client_with_db, "owner_b_dp")
    assert data_b["daily_practice"]["completed_today"] is True
