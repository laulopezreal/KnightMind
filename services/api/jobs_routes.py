"""Background job status and cancellation.

Fourth slice of the main.py split. Small on its own, but extracted first among
the remaining routes because JobStatusResponse is the single schema shared
between the puzzles routes (generate returns a job) and the routes that stay in
main.py. Leaving it behind would have meant puzzles_routes importing from
main.py, which imports the routers -- a cycle. Owning it here breaks that: both
sides import downward.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from services.api.db import get_db
from services.api.identity import assert_owns_username, require_account
from services.api.models import Account, Job, JobStatus

router = APIRouter(tags=["jobs"])

# Minimum gap between ``client_last_seen_at`` writes. The frontend polls every
# 1 second; writing on every poll is wasteful. The first sighting always writes
# regardless of this threshold.
_CLIENT_SEEN_THROTTLE = timedelta(seconds=5)


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
    # Client-observability fields. All nullable: pre-migration rows and jobs never
    # observed by a tab-aware client come through as null.
    client_id: str | None = None
    client_last_seen_at: datetime | None = None
    stall_reported_at: datetime | None = None


def _build_response(job: Job) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        message=job.message,
        progress=job.progress_current,
        updated_at=job.updated_at,
        heartbeat_at=job.heartbeat_at,
        result=job.result_json,
        error=job.error_message,
        client_id=job.client_id,
        client_last_seen_at=job.client_last_seen_at,
        stall_reported_at=job.stall_reported_at,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(
    job_id: str,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
    x_client_id: str | None = Header(default=None),
):
    """Get status of a specific job.

    When ``X-Client-Id`` is present and the job is still active (queued or
    running), the server records which tab is observing it and when it was last
    seen. Writes are throttled to at most once every 5 s (the frontend polls
    every 1 s) so the overhead is low; the first sighting always writes.
    """
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Object-level ownership: 404 (not 403) so foreign job ids aren't confirmed.
    assert_owns_username(account, job.username, db, status_code=404)

    # Client-observation tracking: record which tab is watching and when it last
    # polled, but only while the job is still active (no point after terminal).
    if x_client_id and job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        now = datetime.now(timezone.utc)
        # A new tab observing this job: update the client-id unconditionally.
        if job.client_id != x_client_id:
            job.client_id = x_client_id
            job.client_last_seen_at = now
            db.commit()
        else:
            # Same tab: throttle writes to avoid a DB write every 1-second poll.
            last = job.client_last_seen_at
            if last is None or (now - last.replace(tzinfo=timezone.utc)) >= _CLIENT_SEEN_THROTTLE:
                job.client_last_seen_at = now
                db.commit()

    return _build_response(job)


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

    return _build_response(job)


@router.post("/jobs/{job_id}/stall-report", response_model=JobStatusResponse)
def report_job_stall(
    job_id: str,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
    x_client_id: str | None = Header(default=None),
):
    """Record that a client's stall detector fired for this job.

    This is a pure observability marker: it sets ``stall_reported_at`` on the
    job row (idempotent — re-posting just refreshes the timestamp) and returns
    the current job status. It does NOT change the job's lifecycle status, cancel
    it, fail it, or affect the worker in any way.

    Same auth + ownership pattern as ``cancel_job``: 404 for unknown or foreign
    job ids.
    """
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Object-level ownership: 404 (not 403) so foreign job ids aren't confirmed.
    assert_owns_username(account, job.username, db, status_code=404)

    now = datetime.now(timezone.utc)
    job.stall_reported_at = now
    # Also update client_id if provided — the stall report comes from a specific tab.
    if x_client_id:
        job.client_id = x_client_id
    db.commit()

    return _build_response(job)
