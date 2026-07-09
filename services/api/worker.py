import asyncio
import logging
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
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
            # Cleanup jobs that have been RUNNING for more than 15 minutes.
            # This is a safe threshold for crash recovery.
            limit = datetime.now(timezone.utc) - timedelta(minutes=15)
            stmt = select(Job).where(
                Job.status == JobStatus.RUNNING, Job.updated_at < limit
            )
            stuck_jobs = db.scalars(stmt).all()
            count = 0
            for job in stuck_jobs:
                job.status = JobStatus.QUEUED
                job.message = "Recovered from crash"
                job.updated_at = datetime.now(timezone.utc)
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

    async def process_next_job(self) -> bool:
        """
        Fetch and process the next queued job.
        Returns True if a job was processed, False otherwise.
        """

        def _claim_job(db: Session):
            # Simple atomic claim: select for update (if supported) or just basic transaction
            # SQLite doesn't strictly support FOR UPDATE the same way, but single writer wins.
            stmt = (
                select(Job)
                .where(Job.status == JobStatus.QUEUED)
                .order_by(Job.created_at.asc())
                .limit(1)
            )
            job = db.scalars(stmt).first()
            if job:
                job.status = JobStatus.RUNNING
                job.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(job)
                return job.id
            return None

        # 1. Claim Job
        job_id = None
        with SessionLocal() as db:
            job_id = await asyncio.to_thread(_claim_job, db)

        if not job_id:
            return False

        # 2. Process Job
        logger.info(f"Processing job {job_id}")
        await self.execute_job(job_id)
        return True

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

            # Create a cancellation check function
            def check_cancellation() -> bool:
                """Check if the job has been canceled."""
                with SessionLocal() as db:
                    stmt = select(Job).where(Job.id == job_id)
                    job = db.scalars(stmt).first()
                    if job and job.status == JobStatus.CANCELED:
                        return True
                return False

            # Run generation (CPU bound) with cancellation check
            result = await asyncio.to_thread(
                generate_puzzles,
                username=username,
                max_games=max_games,
                max_puzzles=max_puzzles,
                cancellation_check=check_cancellation,
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
