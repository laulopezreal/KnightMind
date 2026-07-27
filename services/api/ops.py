import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from services.api.ai import config as ai_config
from services.api.auth import require_operator
from services.api.db import get_db
from services.api.engine import is_engine_available
from services.api.models import Job, JobStatus
from services.api.storage.ai_audit_repository import AIAuditRepository
from services.api.worker import worker

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/ping")
def ping():
    """Lightweight liveness probe. No DB or engine checks."""
    return {"status": "pong"}


@router.get("/health")
def get_health(db: Session = Depends(get_db)):
    """
    Full health check. Returns HTTP 503 if any critical service is down.
    Used by Docker HEALTHCHECK and uptime monitors.
    """
    # 1. Check DB
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    # 2. Check Worker
    worker_disabled = os.environ.get("KNIGHTMIND_WORKER_DISABLED") == "true"
    if worker_disabled:
        worker_status = "disabled"
    else:
        worker_status = "ok" if worker.is_running else "not_running"

    # 3. Check Stockfish
    engine_ok, _ = is_engine_available()
    stockfish_status = "ok" if engine_ok else "missing"

    # NOTE: deployment metadata (GIT_SHA / BUILD_TIME) is deliberately NOT
    # returned here. /health is public/unauthed (Docker HEALTHCHECK + uptime
    # monitors), and leaking the exact deployed commit lets anonymous callers
    # fingerprint the build to map known vulns. Version info now lives only on
    # the operator-gated /status endpoint below.
    all_ok = (
        db_status == "ok"
        and worker_status in ("ok", "disabled")
        and stockfish_status == "ok"
    )
    body = {
        "ok": all_ok,
        "db": db_status,
        "worker": worker_status,
        "stockfish": stockfish_status,
    }

    status_code = 200 if all_ok else 503
    return JSONResponse(content=body, status_code=status_code)


@router.get("/ready")
def get_ready(db: Session = Depends(get_db)):
    """
    Readiness probe. Returns 200 only when the API can serve traffic:
    DB is reachable AND Stockfish is available.
    Lighter than /health (skips worker check) — suitable for load balancer routing.
    """
    # DB check
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    # Stockfish check
    engine_ok, _ = is_engine_available()

    ready = db_ok and engine_ok
    body = {
        "ready": ready,
        "db": "ok" if db_ok else "error",
        "stockfish": "ok" if engine_ok else "missing",
    }
    status_code = 200 if ready else 503
    return JSONResponse(content=body, status_code=status_code)


@router.get("/status", dependencies=[Depends(require_operator)])
def get_ops_status(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)

    # 1. Active job
    stmt_active = (
        select(Job)
        .where(Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    active_job = db.scalars(stmt_active).first()

    # 2. Recent jobs (last 20)
    stmt_recent = select(Job).order_by(Job.created_at.desc()).limit(20)
    recent_jobs = db.scalars(stmt_recent).all()

    # 3. Metrics (last 24h)
    yesterday = now - timedelta(hours=24)

    # Basic counts
    stmt_counts = (
        select(Job.status, func.count(Job.id))
        .where(Job.created_at >= yesterday)
        .group_by(Job.status)
    )
    counts = dict(db.execute(stmt_counts).all())

    succeeded_count = counts.get(JobStatus.SUCCEEDED.value, 0)
    failed_count = counts.get(JobStatus.FAILED.value, 0)

    # Performance metrics from result_json
    # We'll calculate this in Python for simplicity with SQLite/JSON fields
    stmt_metrics = select(Job.result_json, Job.updated_at, Job.created_at).where(
        Job.status == JobStatus.SUCCEEDED, Job.created_at >= yesterday
    )
    metric_rows = db.execute(stmt_metrics).all()

    total_duration_ms = 0
    total_cache_hits = 0
    total_cache_misses = 0

    for row in metric_rows:
        result = row[0] or {}
        # Duration calculation
        duration = (row[1] - row[2]).total_seconds() * 1000
        total_duration_ms += duration

        # Cache hit rate extraction
        total_cache_hits += result.get("cache_hits", 0)
        total_cache_misses += result.get("cache_misses", 0)

    avg_duration_ms = total_duration_ms / succeeded_count if succeeded_count > 0 else 0

    # Deployment metadata — operator-only (moved off the public /health so it
    # isn't leaked to anonymous callers).
    version = {
        "sha": os.environ.get("GIT_SHA", "unknown"),
        "built_at": os.environ.get(
            "BUILD_TIME", datetime.now(timezone.utc).isoformat()
        ),
    }

    return {
        "now": now.isoformat(),
        "version": version,
        "active_job": active_job,
        "recent_jobs": recent_jobs,
        "last_recovery": worker.recovery_stats,
        # Rule/model agreement over the last week. This is the earliest signal
        # that a prompt or model change regressed — the feature ships with the
        # AI flag ON, so there was no quiet period in which to measure it.
        "ai_diagnosis": {
            "enabled": ai_config.is_enabled(),
            "model": ai_config.MODEL,
            # Reported so "the cards went bare" resolves to a missing key
            # rather than a bug hunt. The key itself is never exposed.
            "api_key_present": ai_config.api_key() is not None,
            **AIAuditRepository(db).agreement_stats(days=7),
        },
        "metrics": {
            "last_24h": {
                "jobs_succeeded": succeeded_count,
                "jobs_failed": failed_count,
                "avg_duration_ms": int(avg_duration_ms),
                "cache_hits": total_cache_hits,
                "cache_misses": total_cache_misses,
            }
        },
    }
