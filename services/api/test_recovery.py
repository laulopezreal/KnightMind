from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from services.api.models import Job, JobStatus
from services.api.worker import JobWorker


@pytest.fixture
def TestSessionLocal(db_engine):
    """Session factory on the shared test database.

    This module used to keep a SQLite file (``test_jobs.db``) next to the repo
    and delete it around every test, with a comment about pooled connections
    holding the removed inode. None of that is needed once the database is a
    real server the fixture truncates between tests.
    """
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


def test_startup_recovery(monkeypatch, TestSessionLocal):
    # Mock SessionLocal in worker and db modules
    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    # 1. Create a STALE running job (20 mins old)
    stale_job = Job(
        id="stale-123",
        type="puzzle_generation",
        username="testuser",
        status=JobStatus.RUNNING,
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=25),
    )

    # 2. Create a FRESH running job (2 mins old)
    fresh_job = Job(
        id="fresh-456",
        type="puzzle_generation",
        username="testuser2",
        status=JobStatus.RUNNING,
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    db.add(stale_job)
    db.add(fresh_job)
    db.commit()

    # 3. Run recovery in a thread-safe way as the worker does
    import asyncio

    asyncio.run(worker.cleanup_stuck_jobs())

    # 4. Verify results
    db.refresh(stale_job)
    db.refresh(fresh_job)

    assert stale_job.status == JobStatus.QUEUED
    assert "Recovered" in stale_job.message
    assert fresh_job.status == JobStatus.RUNNING

    assert worker.recovery_stats["recovered_count"] == 1
    assert worker.recovery_stats["last_recovery_at"] is not None

    db.close()


def test_heartbeat_advances_lease_not_updated_at(monkeypatch, TestSessionLocal):
    """The generator's progress callback bumps the heartbeat_at lease and
    returns False for a still-running job. Liveness must stay decoupled from
    status writes, so updated_at MUST NOT move.
    """
    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    old_ts = datetime.now(timezone.utc) - timedelta(minutes=20)
    job = Job(
        id="beat-1",
        type="puzzle_generation",
        username="beatuser",
        status=JobStatus.RUNNING,
        updated_at=old_ts,
        heartbeat_at=old_ts,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=25),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    updated_before = job.updated_at
    heartbeat_before = job.heartbeat_at

    canceled = worker._heartbeat_and_check_cancellation("beat-1")

    assert canceled is False
    db.refresh(job)
    assert job.heartbeat_at is not None and heartbeat_before is not None
    assert job.heartbeat_at > heartbeat_before  # lease advanced
    assert job.updated_at == updated_before  # status-write ts untouched
    db.close()


def test_heartbeat_reports_cancellation(monkeypatch, TestSessionLocal):
    """A canceled job makes the callback return True so the generator stops."""
    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    job = Job(
        id="beat-cancel",
        type="puzzle_generation",
        username="beatcancel",
        status=JobStatus.CANCELED,
        updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()

    assert worker._heartbeat_and_check_cancellation("beat-cancel") is True
    db.close()


def test_live_long_job_not_reset_but_crashed_one_is(monkeypatch, TestSessionLocal):
    """Crash recovery must NOT reset a legitimately long-running job that is
    still making progress, but MUST reset a job that has genuinely stalled.

    Regression for the bug where cleanup_stuck_jobs reset any RUNNING job older
    than 15 min purely on wall-clock time since the claim. With no heartbeat, a
    live deep-analysis job that had been running 20 min would be reset and
    re-run (duplicate generation). Now a live job bumps the heartbeat_at lease,
    so it survives; a crashed job stops heartbeating and is correctly recovered.
    """
    import asyncio

    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    # Both jobs were CLAIMED 20 min ago: claim-time heartbeat_at is old.
    claim_time = datetime.now(timezone.utc) - timedelta(minutes=20)

    live_job = Job(
        id="live-long",
        type="puzzle_generation",
        username="liveuser",
        status=JobStatus.RUNNING,
        updated_at=claim_time,
        heartbeat_at=claim_time,
        created_at=claim_time,
    )
    crashed_job = Job(
        id="crashed",
        type="puzzle_generation",
        username="crasheduser",
        status=JobStatus.RUNNING,
        updated_at=claim_time,
        heartbeat_at=claim_time,
        created_at=claim_time,
    )
    db.add_all([live_job, crashed_job])
    db.commit()

    # The live job makes progress -> its heartbeat refreshes heartbeat_at.
    # The crashed job never heartbeats (its worker died).
    worker._heartbeat_and_check_cancellation("live-long")

    asyncio.run(worker.cleanup_stuck_jobs())

    db.refresh(live_job)
    db.refresh(crashed_job)

    # Live long-running job survives; crashed job is recovered to QUEUED.
    assert live_job.status == JobStatus.RUNNING
    assert crashed_job.status == JobStatus.QUEUED
    assert "Recovered" in crashed_job.message
    db.close()


def test_null_heartbeat_falls_back_to_updated_at(monkeypatch, TestSessionLocal):
    """Pre-migration rows have heartbeat_at = NULL. Recovery must COALESCE to
    updated_at (then created_at) so those rows aren't stranded: a NULL-lease row
    with a fresh updated_at survives, one with a stale updated_at is recovered.
    """
    import asyncio

    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    old = datetime.now(timezone.utc) - timedelta(minutes=20)
    recent = datetime.now(timezone.utc) - timedelta(minutes=2)

    stale_null = Job(
        id="null-stale",
        type="puzzle_generation",
        username="nullstale",
        status=JobStatus.RUNNING,
        updated_at=old,
        heartbeat_at=None,  # pre-migration row
        created_at=old,
    )
    fresh_null = Job(
        id="null-fresh",
        type="puzzle_generation",
        username="nullfresh",
        status=JobStatus.RUNNING,
        updated_at=recent,
        heartbeat_at=None,  # pre-migration row
        created_at=old,
    )
    db.add_all([stale_null, fresh_null])
    db.commit()

    asyncio.run(worker.cleanup_stuck_jobs())

    db.refresh(stale_null)
    db.refresh(fresh_null)

    assert stale_null.status == JobStatus.QUEUED  # fell back to stale updated_at
    assert fresh_null.status == JobStatus.RUNNING  # fell back to fresh updated_at
    db.close()


# 30-ply single game: long enough that the intra-game heartbeat must fire.
_LONG_GAME_PGN = """[Event "Test Long Game"]
[White "longuser"]
[Black "opponent"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 \
8. c3 O-O 9. h3 Nb8 10. d4 Nbd7 11. Nbd2 Bb7 12. Bc2 Re8 13. Nf1 Bf8 \
14. Ng3 g6 15. a4 c5"""


def test_long_single_game_keeps_lease_fresh_and_not_reset(
    monkeypatch, TestSessionLocal
):
    """End-to-end: a legitimately long SINGLE-game run keeps heartbeat_at fresh
    (the generator heartbeats within the game) so crash recovery does NOT reset
    it, even though it was claimed long ago.
    """
    import asyncio
    from unittest.mock import Mock, patch

    from services.api.engine import EvalResult
    from services.api.models import Game
    from services.api.puzzles.generator import generate_puzzles

    # Both the generator and the worker heartbeat must hit the same test DB.
    monkeypatch.setattr("services.api.worker.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("services.api.puzzles.generator.SessionLocal", TestSessionLocal)

    db = TestSessionLocal()
    worker = JobWorker()

    db.add(
        Game(
            game_id="long-game-1",
            url="https://chess.com/game/long-1",
            username="longuser",
            white_username="longuser",
            black_username="opponent",
            white_result="win",
            black_result="loss",
            time_control="600",
            end_time=1234567890,
            rated=True,
            pgn_blob=_LONG_GAME_PGN,
        )
    )

    # Job was CLAIMED 20 min ago: without an intra-game heartbeat its lease
    # would be stale and cleanup would falsely reset it.
    claim_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    job = Job(
        id="long-job-1",
        type="puzzle_generation",
        username="longuser",
        status=JobStatus.RUNNING,
        updated_at=claim_time,
        heartbeat_at=claim_time,
        created_at=claim_time,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    heartbeat_before = job.heartbeat_at

    with (
        patch("services.api.puzzles.generator.create_engine", return_value=Mock()),
        patch("services.api.puzzles.generator.get_or_compute_eval") as mock_eval,
    ):
        mock_eval.return_value = EvalResult(best_move_uci="e2e4", eval=0.3)
        generate_puzzles(
            "longuser",
            max_games=1,
            max_puzzles=10,
            cancellation_check=lambda: worker._heartbeat_and_check_cancellation(
                "long-job-1"
            ),
        )

    # The single game's intra-game heartbeats advanced the lease.
    db.refresh(job)
    assert job.heartbeat_at is not None and heartbeat_before is not None
    assert job.heartbeat_at > heartbeat_before

    # Crash recovery must leave the still-live long job alone.
    asyncio.run(worker.cleanup_stuck_jobs())
    db.refresh(job)
    assert job.status == JobStatus.RUNNING
    db.close()


@pytest.mark.postgres
def test_the_worker_keeps_beating_while_a_job_runs(db_session, monkeypatch):
    """Busy is not dead, and the beat must keep ADVANCING to prove it.

    The first version of this asserted only that one row existed, which a beat
    loop that beats once and then sleeps for an hour also satisfies. It has to
    read beat_at twice, with the job loop genuinely blocked in a thread the way
    `process_next_job` blocks it.
    """
    import asyncio
    import threading
    import time

    from services.api import worker as worker_module
    from services.api.models import WorkerHeartbeat

    monkeypatch.setattr(worker_module, "BEAT_INTERVAL_SECONDS", 0.05)
    release = threading.Event()
    seen = []

    async def scenario():
        w = worker_module.JobWorker()
        w.worker_id = "busy-worker"

        async def blocked_loop():
            # Exactly how a real job holds the loop: awaiting a thread.
            await asyncio.to_thread(release.wait)

        # Through start(), so the wiring under test is the real one. Building
        # the tasks by hand here is what made the first version of the
        # dead-loop check pass against a start() that wired nothing.
        monkeypatch.setattr(w, "run_worker_loop", blocked_loop)
        w.start()

        for _ in range(2):
            await asyncio.sleep(0.25)
            seen.append(_read_beat("busy-worker"))
        release.set()
        assert w._task is not None and w._beat_task is not None
        await w._task
        w._beat_task.cancel()
        try:
            await w._beat_task
        except asyncio.CancelledError:
            pass

    def _read_beat(worker_id):
        from services.api.db import SessionLocal

        with SessionLocal() as db:
            return (
                db.query(WorkerHeartbeat)
                .filter(WorkerHeartbeat.worker_id == worker_id)
                .one()
                .beat_at
            )

    asyncio.run(scenario())
    assert (
        seen[1] > seen[0]
    ), f"beat did not advance while the job loop was blocked: {seen}"
    time.sleep(0)

    db_session.query(WorkerHeartbeat).filter(
        WorkerHeartbeat.worker_id == "busy-worker"
    ).delete()
    db_session.commit()


@pytest.mark.postgres
def test_a_dead_job_loop_stops_the_beat(db_session, monkeypatch):
    """A beat outliving the loop reports a dead worker as healthy.

    Worse than the in-loop beat it replaced: that one at least went stale. The
    two are linked by a done-callback, and this is what says so.
    """
    import asyncio

    from services.api import worker as worker_module
    from services.api.models import WorkerHeartbeat

    monkeypatch.setattr(worker_module, "BEAT_INTERVAL_SECONDS", 0.05)

    async def scenario():
        w = worker_module.JobWorker()
        w.worker_id = "dying-worker"

        async def doomed_loop():
            await asyncio.sleep(0.05)
            raise RuntimeError("loop died")

        monkeypatch.setattr(w, "run_worker_loop", doomed_loop)
        w.start()

        await asyncio.sleep(0.4)
        assert w._beat_task is not None
        return w.is_running, w._beat_task.cancelled() or w._beat_task.done()

    still_running, beat_stopped = asyncio.run(scenario())
    assert still_running is False, "a dead loop must clear is_running"
    assert beat_stopped is True, "a dead loop must stop the beat"

    db_session.query(WorkerHeartbeat).filter(
        WorkerHeartbeat.worker_id == "dying-worker"
    ).delete()
    db_session.commit()


@pytest.mark.postgres
def test_the_beat_survives_a_graceful_drain(db_session, monkeypatch):
    """SIGTERM during a job must not make the worker look dead while draining.

    compose sends SIGTERM on every deploy, so "shutting down with a job in
    flight" is the common case, not the rare one. Cancelling the beat before
    awaiting the job loop reported `stale` 27s into a 50s drain -- failing the
    API's healthcheck and the deploy gate for a worker doing exactly what it
    was asked to do.
    """
    import asyncio
    import threading

    from services.api import worker as worker_module
    from services.api.db import SessionLocal
    from services.api.models import WorkerHeartbeat

    monkeypatch.setattr(worker_module, "BEAT_INTERVAL_SECONDS", 0.05)
    release = threading.Event()
    during_drain = {}

    async def scenario():
        w = worker_module.JobWorker()
        w.worker_id = "draining-worker"

        async def draining_loop():
            await asyncio.to_thread(release.wait)

        monkeypatch.setattr(w, "run_worker_loop", draining_loop)
        w.start()
        await asyncio.sleep(0.2)

        stopping = asyncio.create_task(w.stop())
        # Mid-drain: the job has not finished, so the beat must still be alive.
        await asyncio.sleep(0.3)
        with SessionLocal() as db:
            first = (
                db.query(WorkerHeartbeat)
                .filter(WorkerHeartbeat.worker_id == "draining-worker")
                .one()
                .beat_at
            )
        await asyncio.sleep(0.3)
        with SessionLocal() as db:
            second = (
                db.query(WorkerHeartbeat)
                .filter(WorkerHeartbeat.worker_id == "draining-worker")
                .one()
                .beat_at
            )
        during_drain["advanced"] = second > first

        release.set()
        await stopping

    asyncio.run(scenario())
    assert during_drain[
        "advanced"
    ], "the beat stopped while the worker was still draining a job"

    db_session.query(WorkerHeartbeat).filter(
        WorkerHeartbeat.worker_id == "draining-worker"
    ).delete()
    db_session.commit()
