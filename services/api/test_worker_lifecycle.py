"""The worker process's start, stop and exit-code contract.

These cover the paths a review found unreachable or self-defeating: the
documented kill switch, the dead-loop exit, and the API tearing down a worker
it never started. All three are process-lifecycle behaviour that no other test
exercises, and each one failed silently rather than loudly.
"""

import asyncio
import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

import pytest  # noqa: E402

from services.api import worker_main  # noqa: E402
from services.api.worker import JobWorker  # noqa: E402


class TestTheKillSwitchIdles:
    """`restart: unless-stopped` restarts on ANY exit status, including 0.

    So a disabled worker that returns immediately is not a quiet container, it
    is a crash loop that logs the same line forever. OPERATIONS.md promises the
    opposite.
    """

    def test_disabled_worker_waits_instead_of_returning(self, monkeypatch):
        monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")
        started: list[str] = []
        monkeypatch.setattr(
            worker_main.worker, "start", lambda: started.append("started")
        )

        async def scenario():
            task = asyncio.create_task(worker_main._run())
            # Give _run() a chance to reach its wait. If it returns instead,
            # the task is already done here and the assertion below fails.
            await asyncio.sleep(0.05)
            assert not task.done(), "disabled worker exited instead of idling"
            # SIGTERM still ends it promptly — the handlers are installed
            # before the disabled check, so `compose stop worker` behaves.
            worker_main.signal.raise_signal(worker_main.signal.SIGTERM)
            return await asyncio.wait_for(task, timeout=2)

        asked_to_stop = asyncio.run(scenario())
        assert asked_to_stop is True  # a signal asked; exit code 0 is correct
        assert started == []  # and nothing was ever claimed


class TestADeadLoopIsReportable:
    def test_stop_survives_a_loop_that_raised(self):
        """A loop that dies re-raises out of `await self._task`.

        Letting that propagate skipped the beat cancel and the heartbeat
        withdrawal, and escaped asyncio.run() past main()'s KeyboardInterrupt
        handler — so the "its loop died; restarting" branch could never run.
        """
        w = JobWorker()
        withdrew: list[str] = []
        w._withdraw = lambda: withdrew.append("withdrew")  # type: ignore[method-assign]

        async def scenario():
            async def dies():
                raise RuntimeError("job loop died")

            w.is_running = True
            w._task = asyncio.create_task(dies())
            await asyncio.sleep(0)
            await w.stop()

        asyncio.run(scenario())
        assert withdrew == ["withdrew"], "heartbeat was not withdrawn"

    def test_a_requested_stop_still_withdraws(self):
        w = JobWorker()
        withdrew: list[str] = []
        w._withdraw = lambda: withdrew.append("withdrew")  # type: ignore[method-assign]

        async def scenario():
            async def finishes():
                return None

            w.is_running = True
            w._task = asyncio.create_task(finishes())
            await asyncio.sleep(0)
            await w.stop()

        asyncio.run(scenario())
        assert withdrew == ["withdrew"]


class TestTheApiDoesNotStopSomeoneElsesWorker:
    @pytest.mark.parametrize(
        "env,runs_elsewhere",
        [
            ({"KNIGHTMIND_WORKER_EXTERNAL": "true"}, True),
            ({"KNIGHTMIND_WORKER_DISABLED": "true"}, True),
            ({}, False),
        ],
        ids=["external", "disabled", "in-process"],
    )
    def test_teardown_is_symmetric_with_startup(self, monkeypatch, env, runs_elsewhere):
        """stop() DELETEs this process's heartbeat row, and worker_id is
        overridable via KNIGHTMIND_WORKER_ID — which .env.docker sets for both
        services, since they share one env_file. An API restart would then
        delete the live worker's beat and /ops/health would 503 until the next
        one landed. The guard on teardown must match the guard on startup."""
        from services.api import main as api_main

        for key in ("KNIGHTMIND_WORKER_EXTERNAL", "KNIGHTMIND_WORKER_DISABLED"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        assert api_main._worker_runs_elsewhere() is runs_elsewhere
