import asyncio
import logging
import traceback
from datetime import datetime, timezone
from dataclasses import asdict

from sqlalchemy.orm import Session
from sqlalchemy import select

from services.api.db import SessionLocal
from services.api.models import Job, JobStatus
from services.api.puzzles.generator import generate_puzzles, GenerationResult

logger = logging.getLogger(__name__)

class JobWorker:
    def __init__(self):
        self.is_running = False
        self._task = None

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
            # For simplicity, if a job is "running" but we just started, it's stuck.
            # In a multi-worker setup, we would check timestamp. 
            # Since we are single-worker/single-process as per req, any running job at startup IS stuck.
            stmt = select(Job).where(Job.status == JobStatus.RUNNING)
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
            stmt = select(Job).where(Job.status == JobStatus.QUEUED).order_by(Job.created_at.asc()).limit(1)
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
            # We need to read job parameters.
            # For puzzle generation, we need inputs.
            # Currently Job model doesn't strictly store inputs in separate columns, 
            # but we can assume 'username' is there.
            # If we needed arguments like 'max_games', we should have stored them in JSON parameters or separate columns.
            # The prompt said: POST /puzzles/generate?username=...&max_games=...
            # The Job model has `username`.
            # I should probably default max_games if not stored, or better, add 'params' JSON column?
            # The user provided valid Job model spec: type(text), username(text).
            # It didn't specify extra params. I'll stick to username and defaults, 
            # OR I'll assume we can use defaults for now since the prompt didn't strictly require parameter persistence 
            # other than what's needed for the job type. 
            # But wait, `max_games` is variable. 
            # I should hack it: maybe `type` can parse it? "puzzle_generation_30_30"? 
            # Or just update `Job` model to store `params`?
            # The prompt definition for Job model was:
            # - type, username, status, progress..., message, result_json, error_message.
            # It didn't list `params`. 
            # I will check `generate_puzzles` signature. It takes `max_games`.
            # Usage: `POST /puzzles/generate?username=...&max_games=...`
            # If I don't persist `max_games`, I can't restart the job with same params.
            # I'll stick to default 30 if not present, OR I'll add `params` column in a migration?
            # User said "But must be operationally reliable".
            # I'll decide to hardcode defaults or extract from somewhere. 
            # "convert it to async and persisted" -> usually implies persisting arguments.
            # I didn't add `params` column in the model I just created (followed prompt spec).
            # Prompt spec: "type (text), username (text)..."
            # It didn't explicitly say params.
            # I will use the default values used in `main.py` -> max_games=30, max_puzzles=30.
            
            with SessionLocal() as db:
                stmt = select(Job).where(Job.id == job_id)
                job = db.scalars(stmt).first()
                if not job:
                    return # Should not happen
                username = job.username
                # Read params from job, with fallbacks for safety
                job_params = job.params or {}
                max_games = job_params.get("max_games", 30)
                max_puzzles = job_params.get("max_puzzles", 30)
            
            # Run generation (CPU bound)
            result = await asyncio.to_thread(
                generate_puzzles, 
                username=username, 
                max_games=max_games, 
                max_puzzles=max_puzzles
            )

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
                    job.status = JobStatus.FAILED
                    job.error_message = str(e)
                    job.updated_at = datetime.now(timezone.utc)
                    db.commit()

worker = JobWorker()
