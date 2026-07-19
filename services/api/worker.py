import asyncio
import logging
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from services.api.db import SessionLocal
from services.api.models import Job, JobStatus
from services.api.puzzles.generator import generate_puzzles

logger = logging.getLogger(__name__)


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
                # Job params bypass FastAPI validation; clamp to the same
                # bounds as the endpoint so a stored job can't trigger an
                # unbounded bulk PGN load.
                max_games = min(max(int(params.get("max_games", 30)), 1), 2000)
                max_puzzles = min(max(int(params.get("max_puzzles", 30)), 1), 2000)

            # Run generation (CPU bound). The generator calls the callback as it
            # makes progress; we use it both to check for cancellation AND to
            # heartbeat `updated_at`, so crash recovery can tell a live long job
            # (recent heartbeat) apart from a crashed one (stale heartbeat).
            result = await asyncio.to_thread(
                generate_puzzles,
                username=username,
                max_games=max_games,
                max_puzzles=max_puzzles,
                cancellation_check=lambda: self._heartbeat_and_check_cancellation(
                    job_id
                ),
            )

            # Check if job was canceled during execution
            with SessionLocal() as db:
                stmt = select(Job).where(Job.id == job_id)
                job = db.scalars(stmt).first()
                if job and job.status == JobStatus.CANCELED:
                    logger.info(f"Job {job_id} was canceled during execution")
                    # Job already marked as canceled, just return
                    return

            # Update success
            with SessionLocal() as db:
                stmt = select(Job).where(Job.id == job_id)
                job = db.scalars(stmt).first()
                if job:
                    job.status = JobStatus.SUCCEEDED
                    job.progress_current = 100
                    job.progress_total = 100
                    job.result_json = asdict(result)
                    job.message = "Analysis complete"
                    job.updated_at = datetime.now(timezone.utc)
                    db.commit()

            logger.info(f"Job {job_id} succeeded")

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            traceback.print_exc()
            with SessionLocal() as db:
                stmt = select(Job).where(Job.id == job_id)
                job = db.scalars(stmt).first()
                if job:
                    # Don't overwrite canceled status
                    if job.status != JobStatus.CANCELED:
                        job.status = JobStatus.FAILED
                        job.error_message = str(e)
                        job.updated_at = datetime.now(timezone.utc)
                    db.commit()


worker = JobWorker()
