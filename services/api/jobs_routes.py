"""Background job status and cancellation.

Fourth slice of the main.py split. Small on its own, but extracted first among
the remaining routes because JobStatusResponse is the single schema shared
between the puzzles routes (generate returns a job) and the routes that stay in
main.py. Leaving it behind would have meant puzzles_routes importing from
main.py, which imports the routers -- a cycle. Owning it here breaks that: both
sides import downward.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from services.api.db import get_db
from services.api.identity import assert_owns_username, require_account
from services.api.models import Account, Job, JobStatus

router = APIRouter(tags=["jobs"])


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    message: str | None = None
    progress: int = 0
    # Status-write timestamp: moves on per-game progress writes and status
    # transitions. Surfaced so a polling client can treat it as forward progress.
    updated_at: datetime | None = None
    # Liveness lease bumped by the worker's per-ply heartbeat DURING a single
    # long game (updated_at is deliberately pinned across those heartbeats, so it
    # alone cannot signal in-game liveness). A client's stall detector keys on
    # this so a single game that outlasts the stall window is not falsely failed.
    heartbeat_at: datetime | None = None
    result: dict | None = None
    error: str | None = None


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(
    job_id: str,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Get status of a specific job."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Object-level ownership: 404 (not 403) so foreign job ids aren't confirmed.
    assert_owns_username(account, job.username, db, status_code=404)

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        message=job.message,
        progress=job.progress_current,
        updated_at=job.updated_at,
        heartbeat_at=job.heartbeat_at,
        result=job.result_json,
        error=job.error_message,
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobStatusResponse)
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Cancel a running or queued job."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Object-level ownership: 404 (not 403) so foreign job ids aren't confirmed.
    assert_owns_username(account, job.username, db, status_code=404)

    # Only allow cancellation of queued or running jobs
    if job.status not in [JobStatus.QUEUED, JobStatus.RUNNING]:
        raise HTTPException(
            status_code=400, detail=f"Cannot cancel job with status '{job.status}'"
        )

    # Update job status to canceled
    job.status = JobStatus.CANCELED
    job.message = "Canceled by user"
    job.updated_at = datetime.now(timezone.utc)
    db.commit()

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        message=job.message,
        progress=job.progress_current,
        result=job.result_json,
        error=job.error_message,
    )
