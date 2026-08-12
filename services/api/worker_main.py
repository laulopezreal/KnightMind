"""Run the job worker as its own process.

The worker used to start inside the API's FastAPI lifespan, which pinned the
deployment to ``--workers 1`` (see the note this replaces in the Dockerfile) and
meant Stockfish analysis competed with request serving for the same CPU. It also
meant an API restart killed whatever job was mid-flight.

Nothing about the worker needed to be in that process. Job claiming was already
safe for concurrent claimers -- a ``SKIP LOCKED`` row lock plus a
``status == QUEUED`` guarded UPDATE, so exactly one claimer sees ``rowcount ==
1`` -- and the worker never touches the rate limiter, which is route-only. What
was missing was an entrypoint and a way for the API to see a worker it no longer
hosts; this file is the first, ``WorkerHeartbeat`` is the second.

Run with:  python -m services.api.worker_main
"""

import asyncio
import logging
import os
import signal

from services.api.jobs.cleanup_sessions import run_session_cleanup
from services.api.worker import worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("knightmind.worker")


async def _run() -> None:
    stopping = asyncio.Event()

    def _request_stop(signame: str) -> None:
        # Compose sends SIGTERM on `stop`/`down` and on a rolling deploy. Without
        # handling it the process is killed outright after the grace period,
        # abandoning a running job to crash recovery -- which works, but only
        # after the lease expires, so the job sits stuck for that whole window.
        logger.info("%s received, finishing current job then exiting", signame)
        stopping.set()

    loop = asyncio.get_running_loop()
    for signame in ("SIGTERM", "SIGINT"):
        loop.add_signal_handler(getattr(signal, signame), _request_stop, signame)

    # The documented kill switch has to be honoured HERE too. `.env.docker` is
    # the env_file for both services, so an operator setting it per the runbook
    # was only telling the API to stop reporting on the worker -- which kept
    # claiming jobs, beating and running housekeeping. The flag meant "stop
    # looking", not "stop".
    if os.environ.get("KNIGHTMIND_WORKER_DISABLED") == "true":
        logger.info("KNIGHTMIND_WORKER_DISABLED=true; not starting the worker")
        return

    worker.start()
    # Housekeeping belongs to whichever process runs the worker, and there is
    # exactly one of those. It was previously started by the API's lifespan; the
    # move out left it started by nobody, so abandoned sessions accumulated and
    # AI audit rows outlived their retention window without a sound.
    cleanup_task = asyncio.create_task(run_session_cleanup())

    await stopping.wait()

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    # `stop` clears the run flag and awaits the loop task, so an in-flight job
    # runs to completion rather than being torn out from under its transaction.
    await worker.stop()


def main() -> None:
    logger.info("Starting job worker (id=%s)", worker.worker_id)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        pass
    logger.info("Job worker exited")


if __name__ == "__main__":
    main()
