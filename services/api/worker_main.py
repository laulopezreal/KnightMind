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


async def _run() -> bool:
    """Run until stopped. Returns True if a signal asked us to stop.

    The distinction decides the exit code: a requested stop is success, a loop
    that died on its own is a failure the container should be restarted for.
    """
    stopping = asyncio.Event()
    asked_to_stop = False

    def _request_stop(signame: str) -> None:
        nonlocal asked_to_stop
        asked_to_stop = True
        # Compose sends SIGTERM on `stop`/`down` and on a rolling deploy. Without
        # handling it the process is killed outright after the grace period,
        # abandoning a running job to crash recovery -- which works, but only
        # after the lease expires, so the job sits stuck for that whole window.
        logger.info("%s received, finishing current job then exiting", signame)
        stopping.set()

    loop = asyncio.get_running_loop()
    for signame in ("SIGTERM", "SIGINT"):
        loop.add_signal_handler(getattr(signal, signame), _request_stop, signame)

    # Housekeeping belongs to whichever process runs the worker, and there is
    # exactly one of those. It was previously started by the API's lifespan; the
    # move out left it started by nobody, so abandoned sessions accumulated and
    # AI audit rows outlived their retention window without a sound.
    #
    # Started BEFORE the kill switch, because sweeping is not job claiming and
    # "stop the worker" was never meant to mean "stop collecting rubbish". With
    # it started after, KNIGHTMIND_WORKER_DISABLED=true silently halted all four
    # sweeps: abandoned sessions, the heartbeat purge, the AI-audit retention
    # sweep that OPERATIONS.md states as a data-handling commitment, and the
    # rate_limit_hits purge. That last one is the sharpest — the API keeps
    # INSERTing into that table regardless of the worker, so the switch
    # documented for a spend incident was also the switch that let it grow
    # unbounded while nobody was collecting.
    cleanup_task = asyncio.create_task(run_session_cleanup())

    # The documented kill switch has to be honoured HERE too. `.env.docker` is
    # the env_file for both services, so an operator setting it per the runbook
    # was only telling the API to stop reporting on the worker -- which kept
    # claiming jobs and beating. The flag meant "stop looking", not "stop".
    if os.environ.get("KNIGHTMIND_WORKER_DISABLED") == "true":
        logger.info(
            "KNIGHTMIND_WORKER_DISABLED=true; idling without claiming anything "
            "(housekeeping still runs)"
        )
        # Wait rather than return. `restart: unless-stopped` restarts on ANY
        # exit status, including 0, so returning here made the documented kill
        # switch a crash loop: exit clean, get restarted, log the same line,
        # forever. OPERATIONS.md promises a container that sits quiet.
        #
        # The signal handlers above are already installed, so SIGTERM still
        # ends the process promptly and `docker compose stop worker` behaves.
        await stopping.wait()
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        return asked_to_stop

    # Leave the shutdown wait if the loop dies on its own, not only on a
    # signal. Otherwise the process outlives the thing it exists to run.
    worker.on_exit(stopping.set)
    worker.start()

    await stopping.wait()

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    # `stop` clears the run flag and awaits the loop task, so an in-flight job
    # runs to completion rather than being torn out from under its transaction.
    await worker.stop()
    return asked_to_stop


def main() -> None:
    logger.info("Starting job worker (id=%s)", worker.worker_id)
    try:
        asked_to_stop = asyncio.run(_run())
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        asked_to_stop = True
    if not asked_to_stop:
        # Nobody asked: the loop died. Exit non-zero so `restart: unless-stopped`
        # actually restarts the container -- otherwise it sits Up with a dead
        # queue until a human notices.
        logger.error("Job worker exited because its loop died; restarting")
        raise SystemExit(1)
    logger.info("Job worker exited")


if __name__ == "__main__":
    main()
