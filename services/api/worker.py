import asyncio
import logging
import traceback
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.api.db import SessionLocal
from services.api.diagnosis.job import DIAGNOSIS_BATCH_MAX, run_diagnosis
from services.api.models import Job, JobStatus, JobType
from services.api.puzzles.generator import generate_puzzles
from services.api.storage.diagnosis_repository import DiagnosisRepository

logger = logging.getLogger(__name__)


class UnknownJobTypeError(Exception):
    """A job names a type with no registered handler.

    Raised (and therefore surfaced as a FAILED job with a readable message)
    rather than falling back to a default handler. A job that quietly runs the
    wrong work is far worse than one that fails loudly: before the handler
    registry existed, ``execute_job`` ignored ``job.type`` entirely and ran
    puzzle generation for every job regardless of what it claimed to be.
    """


@dataclass(frozen=True)
class JobContext:
    """Everything a handler needs, and nothing about the worker.

    ``heartbeat`` returns True when the job has been canceled and the handler
    should stop; calling it also bumps the liveness lease, so a long-running
    handler must call it periodically or crash recovery will reclaim the job.
    """

    job_id: str
    username: str
    params: dict
    heartbeat: Callable[[], bool]
    progress: Callable[[int, int], None]


# A handler returns a dataclass or dict, which is persisted to
# ``Job.result_json``. Anything else is a programming error (see
# ``_result_payload``).
JobHandler = Callable[[JobContext], Any]


def _run_puzzle_generation(ctx: JobContext) -> Any:
    """Handler for :attr:`JobType.PUZZLE_GENERATION`."""
    # Job params bypass FastAPI validation; clamp to the same bounds as the
    # endpoint so a stored job can't trigger an unbounded bulk PGN load.
    max_games = min(max(int(ctx.params.get("max_games", 30)), 1), 2000)
    max_puzzles = min(max(int(ctx.params.get("max_puzzles", 30)), 1), 2000)
    return generate_puzzles(
        username=ctx.username,
        max_games=max_games,
        max_puzzles=max_puzzles,
        cancellation_check=ctx.heartbeat,
        progress_callback=ctx.progress,
    )


JOB_HANDLERS: dict[str, JobHandler] = {
    JobType.PUZZLE_GENERATION.value: _run_puzzle_generation,
    JobType.DIAGNOSIS.value: run_diagnosis,
}


def _result_payload(result: Any) -> dict | None:
    """Normalise a handler's return value for ``Job.result_json``."""
    if result is None:
        return None
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    if isinstance(result, dict):
        return result
    raise TypeError(
        f"job handler returned {type(result).__name__}; "
        "handlers must return a dataclass, a dict, or None"
    )


class JobWorker:
    def __init__(self):
        self.is_running = False
        self._task = None
        self.recovery_stats = {"recovered_count": 0, "last_recovery_at": None}

    def start(self):
        """Start the worker background task."""
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self.run_worker_loop())
        logger.info("Job worker started")

    async def stop(self):
        """Stop the worker."""
        self.is_running = False
        if self._task:
            await self._task
            logger.info("Job worker stopped")

    async def run_worker_loop(self):
        """Main worker loop trying to fetch and process jobs."""
        logger.info("Worker loop running")

        # Cleanup stuck jobs on startup
        await self.cleanup_stuck_jobs()

        while self.is_running:
            try:
                processed = await self.process_next_job()
                if not processed:
                    # No job found, sleep a bit
                    await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                # preventing busy loop in case of db parsing error
                await asyncio.sleep(5)

    async def cleanup_stuck_jobs(self):
        """Reset jobs that have been 'running' for too long (e.g. crash recovery)."""

        def _cleanup(db: Session):
            # Reset jobs whose liveness lease has gone stale (crash recovery).
            # Staleness is measured against heartbeat_at (the lease the running
            # worker keeps bumping), NOT updated_at — a live long job keeps its
            # heartbeat fresh and is left alone, while a crashed worker stops
            # heartbeating and gets recovered. COALESCE falls back to
            # updated_at then created_at for pre-migration rows whose
            # heartbeat_at is still NULL, so no in-flight job is stranded.
            now = datetime.now(timezone.utc)
            limit = now - timedelta(minutes=15)
            liveness = func.coalesce(Job.heartbeat_at, Job.updated_at, Job.created_at)
            stmt = select(Job).where(Job.status == JobStatus.RUNNING, liveness < limit)
            stuck_jobs = db.scalars(stmt).all()
            count = 0
            for job in stuck_jobs:
                job.status = JobStatus.QUEUED
                job.message = "Recovered from crash"
                job.updated_at = now
                count += 1
            db.commit()
            return count

        try:
            with SessionLocal() as db:
                count = await asyncio.to_thread(_cleanup, db)
                if count > 0:
                    self.recovery_stats["recovered_count"] += count
                    self.recovery_stats["last_recovery_at"] = datetime.now(
                        timezone.utc
                    ).isoformat()
                    logger.warning(f"Recovered {count} stuck jobs")
        except Exception as e:
            logger.error(f"Failed to cleanup stuck jobs: {e}")

    @staticmethod
    def _claim_job(db: Session):
        """Atomically claim the oldest QUEUED job, transitioning it to RUNNING.

        Picks the oldest QUEUED job and flips it to RUNNING with a single
        guarded UPDATE. The ``status == QUEUED`` guard means only one claimer's
        UPDATE can affect the row (rowcount == 1); a racing claimer sees
        rowcount == 0 and moves on, so two workers can never run the same job.
        On Postgres we also take a SKIP LOCKED row lock so concurrent workers
        select *different* rows instead of colliding on the same one. SQLite
        serializes writers, so the guarded UPDATE alone is sufficient there.

        Returns the claimed job id, or None if there was nothing to claim (or a
        racing worker won the claim first).
        """
        now = datetime.now(timezone.utc)
        select_stmt = (
            select(Job.id)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.created_at.asc())
            .limit(1)
        )
        if db.get_bind().dialect.name == "postgresql":
            select_stmt = select_stmt.with_for_update(skip_locked=True)

        candidate_id = db.execute(select_stmt).scalar_one_or_none()
        if candidate_id is None:
            db.commit()
            return None

        update_stmt = (
            update(Job)
            .where(Job.id == candidate_id, Job.status == JobStatus.QUEUED)
            .values(status=JobStatus.RUNNING, updated_at=now, heartbeat_at=now)
        )
        result = db.execute(update_stmt)
        db.commit()
        # rowcount == 1: we won the claim. rowcount == 0: another worker
        # claimed it between our SELECT and UPDATE; leave it to them.
        return candidate_id if result.rowcount == 1 else None

    async def process_next_job(self) -> bool:
        """
        Fetch and process the next queued job.
        Returns True if a job was processed, False otherwise.
        """
        # 1. Claim Job
        job_id = None
        with SessionLocal() as db:
            job_id = await asyncio.to_thread(self._claim_job, db)

        if not job_id:
            return False

        # 2. Process Job
        logger.info(f"Processing job {job_id}")
        await self.execute_job(job_id)
        return True

    def _heartbeat_and_check_cancellation(self, job_id: str) -> bool:
        """Liveness heartbeat + cancellation check, invoked by the generator as
        it makes progress on each game.

        Returns True if the job has been canceled (so the generator should
        stop). As a side effect, bumps the `heartbeat_at` lease on a
        still-running job so `cleanup_stuck_jobs` can distinguish a live
        long-running job (fresh heartbeat) from a crashed one (stale heartbeat)
        instead of resetting purely on wall-clock time since the claim.

        We update `heartbeat_at` via a Core UPDATE and explicitly re-set
        `updated_at` to its current value: `updated_at` has a column-level
        `onupdate` that would otherwise fire on ANY UPDATE, so pinning it in the
        SET clause suppresses that and keeps liveness decoupled from
        status-write timestamps.
        """
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if not job:
                return False
            if job.status == JobStatus.CANCELED:
                return True
            db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    heartbeat_at=datetime.now(timezone.utc),
                    updated_at=job.updated_at,  # pin: suppress onupdate
                )
            )
            db.commit()
        return False

    def _write_progress(self, job_id: str, done: int, total: int) -> None:
        """Persist per-game generation progress so /jobs/{id} reports honest
        movement during a long run (the bar previously sat frozen until the
        terminal 100% write).

        Called by the generator once per game — bounded writes (max_games is
        capped), so no throttling needed. Guarded WHERE status == RUNNING for
        the same reason as the success write: a cancel is terminal and a late
        progress write must never touch a job that has left RUNNING.
        """
        if total <= 0:
            return
        percent = min(int(done * 100 / total), 99)  # 100 is the terminal write's
        with SessionLocal() as db:
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
                .values(
                    progress_current=percent,
                    progress_total=100,
                    message=f"Analyzing game {done + 1} of {total}",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.commit()

    @staticmethod
    def _enqueue_diagnosis(
        username: str, message: str, params: dict | None = None
    ) -> None:
        """Queue one diagnosis job for a user, best-effort.

        Deliberately wrapped so a follow-up failure cannot turn already
        persisted work into a failed job. The active-job unique index still
        enforces at most one queued/running diagnosis per user; collisions are
        expected under races and are safe no-ops.
        """
        try:
            with SessionLocal() as db:
                try:
                    db.add(
                        Job(
                            username=username,
                            type=JobType.DIAGNOSIS,
                            status=JobStatus.QUEUED,
                            message=message,
                            params=params or {"limit": DIAGNOSIS_BATCH_MAX},
                        )
                    )
                    db.commit()
                except IntegrityError:
                    # A diagnosis job is already queued or running for this
                    # user — the active-job index says so. Marked automatic
                    # diagnosis jobs re-check pending work at completion, so
                    # there is nothing to do here.
                    #
                    # Rolled back explicitly rather than relying on the context
                    # manager: a failed flush leaves the session unusable, and
                    # this must be safe for a caller that supplied its own
                    # session rather than a fresh one.
                    db.rollback()
                    logger.info(
                        "Diagnosis already active for %s; not re-queuing", username
                    )
                    return
            logger.info("Queued follow-up diagnosis for %s", username)
        except Exception as exc:  # noqa: BLE001 - must never fail the generation
            logger.warning("Could not queue follow-up diagnosis: %s", exc)

    @staticmethod
    def _enqueue_followup(job_type: str, username: str) -> None:
        """Queue a diagnosis run after puzzles are generated.

        Fresh puzzles are fresh mistakes, and a mistake with no diagnosis is
        the feature not existing for that user. Chaining here means importing
        games is the only thing anyone has to do — nobody needs to discover
        POST /users/{username}/diagnose.
        """
        if job_type != JobType.PUZZLE_GENERATION.value:
            # Only generation unconditionally produces new puzzles. Diagnosis
            # requeues through _enqueue_remaining_diagnosis_if_pending, guarded
            # by the actual pending-count predicate so it cannot loop forever.
            return

        JobWorker._enqueue_diagnosis(
            username,
            "Queued after puzzle generation",
            {"limit": DIAGNOSIS_BATCH_MAX, "auto_chain": True},
        )

    @staticmethod
    def _enqueue_remaining_diagnosis_if_pending(
        job_type: str, username: str, params: dict
    ) -> None:
        """Close the generation-vs-diagnosis race after a diagnosis succeeds.

        A running diagnosis snapshots pending puzzle ids at start. If puzzle
        generation creates fresh pending puzzles during that run, generation's
        follow-up insert may collide with the active diagnosis and no queued job
        is left behind. Once the current diagnosis is marked SUCCEEDED, re-check
        the storage predicate and queue one more diagnosis only if work remains.
        """
        if job_type != JobType.DIAGNOSIS.value or not params.get("auto_chain"):
            return

        try:
            with SessionLocal() as db:
                pending = DiagnosisRepository(db).pending_count(username)
        except Exception as exc:  # noqa: BLE001 - enrichment must stay best-effort
            logger.warning("Could not check remaining diagnosis work: %s", exc)
            return

        if pending <= 0:
            return

        JobWorker._enqueue_diagnosis(
            username,
            "Queued for remaining diagnosis",
            {"limit": DIAGNOSIS_BATCH_MAX, "auto_chain": True},
        )

    async def execute_job(self, job_id: str):
        """Execute the actual job logic."""
        # Re-fetch job to update status

        try:
            with SessionLocal() as db:
                stmt = select(Job).where(Job.id == job_id)
                job = db.scalars(stmt).first()
                if not job:
                    return  # Should not happen
                username = job.username
                # Use params from job if available, otherwise defaults
                params = job.params or {}
                job_type = job.type

            handler = JOB_HANDLERS.get(job_type)
            if handler is None:
                # Raised inside the try so it takes the ordinary failure path:
                # the guarded UPDATE below records FAILED only if the job is
                # still RUNNING, so a cancel that landed first still wins.
                raise UnknownJobTypeError(
                    f"no handler registered for job type {job_type!r}; "
                    f"known types: {sorted(JOB_HANDLERS)}"
                )

            # Run the handler (CPU bound). It calls the callbacks as it makes
            # progress; those check for cancellation AND bump the heartbeat_at
            # lease, so crash recovery can tell a live long job (fresh lease)
            # apart from a crashed one (stale lease).
            result = await asyncio.to_thread(
                handler,
                JobContext(
                    job_id=job_id,
                    username=username,
                    params=params,
                    heartbeat=lambda: self._heartbeat_and_check_cancellation(job_id),
                    progress=lambda done, total: self._write_progress(
                        job_id, done, total
                    ),
                ),
            )

            # Mark success — but ONLY if the job is still RUNNING. A cancel
            # (POST /jobs/{id}/cancel) can land after generation returns and
            # before this write; that cancel must win, because CANCELED is
            # terminal and an audited job must never go canceled -> succeeded.
            # A single guarded UPDATE (WHERE status = RUNNING) closes that race
            # atomically on both Postgres and SQLite: rowcount == 0 means the
            # job left RUNNING (canceled, or otherwise no longer ours), so we
            # discard the completion instead of overwriting its terminal state.
            now = datetime.now(timezone.utc)
            with SessionLocal() as db:
                success_stmt = (
                    update(Job)
                    .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
                    .values(
                        status=JobStatus.SUCCEEDED,
                        progress_current=100,
                        progress_total=100,
                        result_json=_result_payload(result),
                        message="Analysis complete",
                        updated_at=now,
                    )
                )
                rowcount = db.execute(success_stmt).rowcount
                db.commit()

            if rowcount == 0:
                logger.info(
                    f"Job {job_id} completion discarded: job is no longer "
                    "RUNNING (likely canceled); terminal status left intact"
                )
            else:
                logger.info(f"Job {job_id} succeeded")
                self._enqueue_followup(job_type, username)
                self._enqueue_remaining_diagnosis_if_pending(job_type, username, params)

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            traceback.print_exc()
            # Same guard as the success path: only a still-RUNNING job may be
            # transitioned to FAILED. A cancel that landed during a failing run
            # is terminal too — a canceled job must never become FAILED either.
            now = datetime.now(timezone.utc)
            with SessionLocal() as db:
                failure_stmt = (
                    update(Job)
                    .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
                    .values(
                        status=JobStatus.FAILED,
                        error_message=str(e),
                        updated_at=now,
                    )
                )
                fail_rowcount = db.execute(failure_stmt).rowcount
                db.commit()

            if fail_rowcount == 0:
                logger.info(
                    f"Job {job_id} failure discarded: job is no longer "
                    "RUNNING (likely canceled); terminal status left intact"
                )


worker = JobWorker()
