"""The periodic sweep that starts diagnosis for users nobody else starts it for.

Diagnosis reaches a user by exactly two routes: it auto-chains from puzzle
generation, and an operator calls ``POST /users/{u}/diagnose``. Both need
someone to still be importing games. So a user who stops importing keeps
whatever coverage they had at that moment, forever, and a rule-version bump
never reaches them -- measured in production on 2026-08-15, where one tenant sat
at 0 diagnosis rows until the backfill was run by hand.

The sweep closes that. What these tests pin is not that it *enqueues* -- that is
the easy half and any wrong implementation does it too -- but that it **stops**:
it must add nothing while a job is already active, and nothing at all when there
is no pending work. A sweep that re-queues on every tick is worse than the gap
it fixes, because the worker claims the next job about two seconds later.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

import pytest  # noqa: E402

from services.api.diagnosis.causes import RULE_VERSION  # noqa: E402
from services.api.diagnosis.evidence import EXTRACTION_VERSION  # noqa: E402
from services.api.models import (  # noqa: E402
    DiagnosisStatus,
    Game,
    Job,
    JobStatus,
    JobType,
    PuzzleDiagnosis,
)
from services.api.models import Puzzle as PuzzleModel  # noqa: E402
from services.api.storage.diagnosis_repository import (  # noqa: E402
    DiagnosisRepository,
)
from services.api.worker import JobWorker  # noqa: E402

FEN = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"


def _game(db, game_id: str, username: str) -> None:
    if db.get(Game, (game_id, username)):
        return
    db.add(
        Game(
            game_id=game_id,
            url=f"https://chess.com/game/{game_id}",
            username=username,
            white_username=username,
            black_username="opponent",
            white_result="resigned",
            black_result="win",
            time_control="600+5",
            end_time=int(datetime.now(timezone.utc).timestamp()),
            rated=True,
            pgn_blob="1. e4 e5 *",
        )
    )
    db.commit()


def _puzzle(db, puzzle_id: str, username: str, *, ply: int = 41) -> None:
    game_id = f"g-{username}"
    _game(db, game_id, username)
    db.add(
        PuzzleModel(
            id=puzzle_id,
            username=username,
            source_game_id=game_id,
            ply=ply,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            fen=FEN,
            side_to_move="white",
            played_move_uci="d1d2",
            best_move_uci="d1d5",
            accept_moves_uci="d1d5",
            solution_pv="d1d5",
            eval_before=1.5,
            eval_after=-7.5,
            swing=9.0,
            confirmed_depth=18,
        )
    )
    db.commit()


def _diagnose(db, puzzle_id: str, username: str, *, current: bool = True) -> None:
    """Store a diagnosis row -- current by default, version-stale if asked."""
    db.add(
        PuzzleDiagnosis(
            puzzle_id=puzzle_id,
            username=username,
            status=DiagnosisStatus.OK.value,
            extraction_version=EXTRACTION_VERSION if current else "0",
            rule_version=RULE_VERSION if current else "0",
        )
    )
    db.commit()


def _active_job(
    db,
    username: str,
    status: str = JobStatus.QUEUED.value,
    *,
    age_hours: float = 0,
    job_id: str | None = None,
) -> None:
    when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=age_hours)
    db.add(
        Job(
            id=job_id or f"job-{username}-{status}",
            username=username,
            type=JobType.DIAGNOSIS.value,
            status=status,
            params={},
            created_at=when,
            updated_at=when,
            heartbeat_at=when,
        )
    )
    db.commit()


@pytest.fixture
def sweep(db_session, monkeypatch):
    """Run ``_sweep_once`` against the test session, returning users enqueued."""
    import services.api.worker as worker_module

    class _NoClose:
        def __init__(self, db):
            self._db = db

        def __enter__(self):
            return self._db

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(worker_module, "SessionLocal", lambda: _NoClose(db_session))

    def run() -> int:
        count = JobWorker()._sweep_once()
        db_session.expire_all()
        return count

    return run


def _queued(db, username: str) -> int:
    return (
        db.query(Job)
        .filter(
            Job.username == username,
            Job.type == JobType.DIAGNOSIS.value,
            Job.status == JobStatus.QUEUED.value,
        )
        .count()
    )


class TestItFindsTheUserNobodyIsAskingAbout:
    def test_a_user_with_an_undiagnosed_puzzle_is_enqueued(self, db_session, sweep):
        """The whole point: no import, no operator, still gets diagnosed."""
        _puzzle(db_session, "p1", "dormant")

        assert sweep() == 1
        assert _queued(db_session, "dormant") == 1

    def test_a_version_stale_diagnosis_counts_as_pending(self, db_session, sweep):
        """A rule-version bump must reach users who stopped importing.

        This is the case the manual route silently never covers: the puzzle
        *has* a row, so nothing looks missing, but the row was written by code
        that no longer exists.
        """
        _puzzle(db_session, "p1", "stale")
        _diagnose(db_session, "p1", "stale", current=False)

        assert sweep() == 1
        assert _queued(db_session, "stale") == 1

    def test_each_pending_user_is_enqueued_once(self, db_session, sweep):
        """Two tenants, two jobs -- and one job each, not one per puzzle."""
        _puzzle(db_session, "a1", "alice")
        _puzzle(db_session, "a2", "alice", ply=43)
        _puzzle(db_session, "b1", "bob")

        assert sweep() == 2
        assert _queued(db_session, "alice") == 1
        assert _queued(db_session, "bob") == 1


class TestItStops:
    """The half that matters. Enqueuing is easy; not enqueuing is the contract."""

    def test_a_fully_diagnosed_user_is_not_enqueued(self, db_session, sweep):
        _puzzle(db_session, "p1", "done")
        _diagnose(db_session, "p1", "done")

        assert sweep() == 0
        assert _queued(db_session, "done") == 0

    def test_nothing_is_queued_when_no_puzzles_exist(self, db_session, sweep):
        assert sweep() == 0
        assert db_session.query(Job).count() == 0

    @pytest.mark.parametrize(
        "status", [JobStatus.QUEUED.value, JobStatus.RUNNING.value]
    )
    def test_a_user_with_an_active_job_gets_no_second_one(
        self, db_session, sweep, status
    ):
        """The active-job unique index is the deduplication, and the enqueue
        treats the collision as a no-op. Without that, an hourly sweep over a
        corpus the chain is already working through would pile up a job an hour
        and the worker would claim each one about two seconds after the last."""
        _puzzle(db_session, "busy", "busy")
        _active_job(db_session, "busy", status)

        # Nothing was inserted, and the count says so: the return value is
        # rows queued, not users considered.
        assert sweep() == 0
        assert db_session.query(Job).filter(Job.username == "busy").count() == 1

    def test_repeated_sweeps_do_not_accumulate_jobs(self, db_session, sweep):
        """Three ticks with the work still pending is still one job.

        This is the spin-loop regression in miniature. The chain re-queues
        itself while it has work; the sweep must not add a parallel stream.
        """
        _puzzle(db_session, "p1", "slow")

        sweep()
        sweep()
        sweep()

        assert db_session.query(Job).filter(Job.username == "slow").count() == 1


class TestItBacksOffAfterAFailedOrCanceledRun:
    """The spend loop this guard exists to prevent.

    Pending work does not shrink when a job fails, and `execute_job` only
    enqueues follow-ups on the SUCCEEDED branch -- so a failed job stops the
    chain today and waits for a human. An unguarded sweep removes exactly that
    containment: `run_diagnosis` guards only `EvidenceUnavailable` and
    `_diagnostic_order` is deterministic, so the job would die on the same
    puzzle every hour, and with COMMIT_INTERVAL = 50 the rolled-back audit rows
    mean the model calls are spent without ever counting against the budget.
    """

    def test_a_failed_run_is_not_retried_within_the_backoff(self, db_session, sweep):
        _puzzle(db_session, "p1", "broken")
        _active_job(db_session, "broken", JobStatus.FAILED.value, age_hours=1)

        assert sweep() == 0
        assert _queued(db_session, "broken") == 0

    def test_a_canceled_run_is_not_restarted_within_the_backoff(
        self, db_session, sweep
    ):
        """A cancel is a human saying stop, usually to protect an AI budget.
        Restarting it within the hour, with nothing in the UI explaining why,
        is worse than never having swept."""
        _puzzle(db_session, "p1", "stopped")
        _active_job(db_session, "stopped", JobStatus.CANCELED.value, age_hours=1)

        assert sweep() == 0
        assert _queued(db_session, "stopped") == 0

    def test_an_old_failure_is_retried_again(self, db_session, sweep):
        """Bounded, not permanent: a transient outage still heals."""
        _puzzle(db_session, "flaky", "flaky")
        _active_job(db_session, "flaky", JobStatus.FAILED.value, age_hours=25)

        assert sweep() == 1
        assert _queued(db_session, "flaky") == 1

    def test_a_later_success_clears_an_earlier_failure(self, db_session, sweep):
        """Only the *most recent* job decides. A user who failed yesterday and
        succeeded since is working, not broken, and must not be frozen out by
        history."""
        _puzzle(db_session, "recovered", "recovered")
        _active_job(
            db_session,
            "recovered",
            JobStatus.FAILED.value,
            age_hours=3,
            job_id="old-fail",
        )
        _active_job(
            db_session,
            "recovered",
            JobStatus.SUCCEEDED.value,
            age_hours=1,
            job_id="new-ok",
        )

        assert sweep() == 1
        assert _queued(db_session, "recovered") == 1

    def test_one_users_failure_does_not_block_another(self, db_session, sweep):
        _puzzle(db_session, "a1", "broken")
        _puzzle(db_session, "b1", "healthy")
        _active_job(db_session, "broken", JobStatus.FAILED.value, age_hours=1)

        assert sweep() == 1
        assert _queued(db_session, "healthy") == 1
        assert _queued(db_session, "broken") == 0


class TestTheQueryUnderneath:
    def test_usernames_with_pending_excludes_the_finished(self, db_session):
        """``usernames_with_pending`` is new and is what the sweep trusts."""
        _puzzle(db_session, "p1", "pending")
        _puzzle(db_session, "p2", "finished")
        _diagnose(db_session, "p2", "finished")

        found = DiagnosisRepository(db_session).usernames_with_pending()

        assert found == ["pending"]

    def test_it_agrees_with_the_per_user_count(self, db_session):
        """Two predicates for "needs diagnosing" that disagree is the bug this
        repo keeps shipping. Pin them together."""
        _puzzle(db_session, "p1", "alice")
        _puzzle(db_session, "p2", "bob")
        _diagnose(db_session, "p2", "bob")

        repo = DiagnosisRepository(db_session)
        found = set(repo.usernames_with_pending())

        for username in ("alice", "bob"):
            assert (repo.pending_count(username) > 0) == (username in found)


class TestTheLoopIsWiredToTheWorker:
    def test_the_sweep_task_starts_and_stops_with_the_worker(self, monkeypatch):
        """A sweep that outlives the job loop queues work nothing will claim;
        one that never starts is the defect still open."""
        import services.api.worker as worker_module

        monkeypatch.setattr(worker_module, "DIAGNOSIS_SWEEP_INTERVAL_SECONDS", 3600)

        async def scenario():
            worker = JobWorker()

            async def _until_asked_to_stop():
                # Mirrors the real loop's contract: it winds down when
                # `is_running` clears, which is what `stop()` relies on.
                while worker.is_running:
                    await asyncio.sleep(0.01)

            monkeypatch.setattr(worker, "run_worker_loop", _until_asked_to_stop)
            worker.start()
            assert worker._sweep_task is not None
            assert not worker._sweep_task.done()
            await worker.stop()
            return worker

        worker = asyncio.run(scenario())
        assert worker._sweep_task is None

    def test_the_first_pass_waits_rather_than_firing_on_boot(
        self, db_session, monkeypatch
    ):
        """A crash-looping container must not sweep on every restart.

        The loop sleeps before its first pass, so a worker that dies and
        restarts repeatedly enqueues nothing extra.
        """
        import services.api.worker as worker_module

        monkeypatch.setattr(worker_module, "DIAGNOSIS_SWEEP_INTERVAL_SECONDS", 3600)
        calls: list[int] = []

        def _record(self) -> int:
            calls.append(1)
            return 0

        monkeypatch.setattr(JobWorker, "_sweep_once", _record)

        async def scenario():
            task = asyncio.create_task(JobWorker()._sweep_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())
        assert calls == []

    def test_a_failing_pass_does_not_kill_the_loop(self, monkeypatch):
        """Maintenance is best-effort: a database blip must not take down a
        worker that can still run the jobs it already has."""
        import services.api.worker as worker_module

        monkeypatch.setattr(worker_module, "DIAGNOSIS_SWEEP_INTERVAL_SECONDS", 0.01)
        calls: list[int] = []

        def _boom(self):
            calls.append(1)
            raise RuntimeError("database went away")

        monkeypatch.setattr(JobWorker, "_sweep_once", _boom)

        async def scenario():
            task = asyncio.create_task(JobWorker()._sweep_loop())
            # Long enough for several ticks at a 10ms interval, short enough
            # that a hung loop fails the test rather than the suite.
            await asyncio.sleep(0.2)
            still_running = not task.done()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return still_running

        assert asyncio.run(scenario()) is True
        assert len(calls) > 1  # it kept ticking after the first failure
