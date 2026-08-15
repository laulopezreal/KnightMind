"""The active-job index enforces one job per (user, TYPE), not per user.

Production drifted to the narrower ``(username)`` shape while Alembic recorded
the widening as applied, and nothing noticed for months. The consequence is not
cosmetic: in the narrow shape any active job blocks every OTHER kind, and
``puzzles_routes`` — which catches the IntegrityError, looks for an active job
of the type it was asked for, finds none, and returns HTTP 200 "Job completed
recently" — tells the user it worked while nothing runs.

These tests assert the shape the code relies on, so a database that drifts back
fails here rather than in production.
"""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from services.api.models import Job, JobStatus, JobType  # noqa: E402


def _job(username: str, job_type: JobType, status: JobStatus = JobStatus.QUEUED) -> Job:
    return Job(
        username=username,
        type=job_type.value,
        status=status.value,
        params={},
    )


def test_the_index_is_scoped_by_type(db_session):
    """The definition itself, not just its behaviour.

    Behaviour tests below would also pass against a UNIQUE constraint on
    something subtly different; this pins the actual shape, which is what
    drifted.
    """
    definition = db_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'ix_jobs_active_username'"
        )
    ).scalar_one_or_none()

    assert definition is not None, "the active-job index is missing entirely"
    assert "username, type" in definition, (
        "ix_jobs_active_username is not scoped by type. In this shape any "
        "active job blocks every other kind, and /puzzles/generate reports "
        f"success while doing nothing. Definition: {definition}"
    )


def test_one_user_may_hold_two_active_jobs_of_different_types(db_session):
    """The bug this repairs: a queued diagnosis blocked a generation."""
    db_session.add(_job("victim", JobType.PUZZLE_GENERATION, JobStatus.RUNNING))
    db_session.commit()

    db_session.add(_job("victim", JobType.DIAGNOSIS, JobStatus.QUEUED))
    db_session.commit()  # must not raise

    active = db_session.query(Job).filter(Job.username == "victim").count()
    assert active == 2


def test_one_user_may_not_hold_two_active_jobs_of_the_SAME_type(db_session):
    """The invariant the index is actually for. Widening must not lose it."""
    db_session.add(_job("victim", JobType.DIAGNOSIS, JobStatus.QUEUED))
    db_session.commit()

    db_session.add(_job("victim", JobType.DIAGNOSIS, JobStatus.QUEUED))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_the_index_only_covers_active_jobs(db_session):
    """Partial on (queued, running): finished jobs must not block new ones, or
    a user could never run a second job of a type they had ever run."""
    db_session.add(_job("victim", JobType.DIAGNOSIS, JobStatus.SUCCEEDED))
    db_session.add(_job("victim", JobType.DIAGNOSIS, JobStatus.FAILED))
    db_session.commit()

    db_session.add(_job("victim", JobType.DIAGNOSIS, JobStatus.QUEUED))
    db_session.commit()  # must not raise

    assert db_session.query(Job).filter(Job.username == "victim").count() == 3


def test_two_users_may_each_hold_the_same_job_type(db_session):
    db_session.add(_job("alice", JobType.DIAGNOSIS, JobStatus.RUNNING))
    db_session.add(_job("bob", JobType.DIAGNOSIS, JobStatus.RUNNING))
    db_session.commit()  # must not raise

    assert db_session.query(Job).count() == 2
