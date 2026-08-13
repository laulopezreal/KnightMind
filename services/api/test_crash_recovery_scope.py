"""Crash recovery must not reclaim the job it is running.

Recovery used to run once, at worker startup, when nothing was in flight — so
"do not reclaim the live job" was true by construction and nobody wrote it down.
Moving it into the hourly housekeeping loop broke that silently.

The damage is quiet rather than loud. The completion write is guarded on
``status == RUNNING`` (worker.py), so a reclaimed-but-still-running job:

  * loses its ``result_json`` — rowcount 0, nothing raises
  * freezes its progress bar — ``_write_progress`` no-ops for the rest of the run
  * never enqueues its follow-up — generation stops chaining diagnosis
  * logs "likely canceled", pointing at a cancel that never happened

and, because the reclaimed row is QUEUED with nothing holding a live lease,
``/ops/health`` reports ``stalled`` while the worker is working.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from services.api.models import Job, JobStatus, JobType  # noqa: E402
from services.api.worker import JobWorker  # noqa: E402


def _job(db, job_id: str, *, lease_age_minutes: int, username: str = "u") -> Job:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    job = Job(
        id=job_id,
        username=username,
        type=JobType.PUZZLE_GENERATION.value,
        status=JobStatus.RUNNING.value,
        params={},
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
        heartbeat_at=now - timedelta(minutes=lease_age_minutes),
    )
    db.add(job)
    db.commit()
    return job


def _recover(worker: JobWorker, db_session, monkeypatch) -> None:
    """Run the sweep against the test session."""
    import services.api.worker as worker_module

    class _NoClose:
        def __init__(self, db):
            self._db = db

        def __enter__(self):
            return self._db

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(worker_module, "SessionLocal", lambda: _NoClose(db_session))
    asyncio.run(worker.cleanup_stuck_jobs())
    db_session.expire_all()


def test_the_job_this_process_is_running_is_never_reclaimed(db_session, monkeypatch):
    """The regression. Its lease is stale — a diagnosis batch can go 75 minutes
    between bumps against a degraded provider — but the handler is still in a
    thread and will try to write its result."""
    worker = JobWorker()
    _job(db_session, "live-job", lease_age_minutes=60)
    worker._current_job_id = "live-job"

    _recover(worker, db_session, monkeypatch)

    assert db_session.get(Job, "live-job").status == JobStatus.RUNNING.value


def test_a_genuinely_abandoned_job_is_still_reclaimed(db_session, monkeypatch):
    """The guard must not disable recovery — that is what it is for."""
    worker = JobWorker()
    _job(db_session, "orphan", lease_age_minutes=60)
    worker._current_job_id = None

    _recover(worker, db_session, monkeypatch)

    assert db_session.get(Job, "orphan").status == JobStatus.QUEUED.value


def test_another_workers_stale_job_is_reclaimed_even_while_this_one_is_busy(
    db_session, monkeypatch
):
    """Excluded by id, not by lease age: a second replica's abandoned job is
    exactly what recovery exists for, and only this process knows which job is
    genuinely alive in it."""
    worker = JobWorker()
    _job(db_session, "live-job", lease_age_minutes=60, username="mine")
    _job(db_session, "their-orphan", lease_age_minutes=60, username="theirs")
    worker._current_job_id = "live-job"

    _recover(worker, db_session, monkeypatch)

    assert db_session.get(Job, "live-job").status == JobStatus.RUNNING.value
    assert db_session.get(Job, "their-orphan").status == JobStatus.QUEUED.value


def test_a_fresh_lease_is_left_alone(db_session, monkeypatch):
    worker = JobWorker()
    _job(db_session, "working", lease_age_minutes=1)
    worker._current_job_id = None

    _recover(worker, db_session, monkeypatch)

    assert db_session.get(Job, "working").status == JobStatus.RUNNING.value


def test_the_marker_is_cleared_even_when_the_handler_raises(db_session, monkeypatch):
    """A crashed handler must not leave this process permanently shielding a job
    it is no longer running — that would disable recovery for that row forever.
    """
    import services.api.worker as worker_module

    worker = JobWorker()
    _job(db_session, "boom", lease_age_minutes=0)

    class _NoClose:
        def __init__(self, db):
            self._db = db

        def __enter__(self):
            return self._db

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(worker_module, "SessionLocal", lambda: _NoClose(db_session))
    monkeypatch.setitem(
        worker_module.JOB_HANDLERS,
        JobType.PUZZLE_GENERATION.value,
        lambda ctx: (_ for _ in ()).throw(RuntimeError("handler died")),
    )

    asyncio.run(worker.execute_job("boom"))

    assert worker._current_job_id is None
