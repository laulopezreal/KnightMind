from datetime import datetime, timedelta, timezone
import os
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, func, text

from services.api.db import get_db, engine
from services.api.models import Job, JobStatus, Game, Puzzle
from services.api.storage import get_storage, get_puzzle_storage
from services.api.worker import worker
from services.api.engine import is_engine_available

router = APIRouter(prefix="/ops", tags=["ops"])

@router.get("/health")
def get_health(db: Session = Depends(get_db)):
    # 1. Check DB
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    # 2. Check Worker
    worker_status = "ok" if worker.is_running else "not_running"

    # 3. Check Stockfish
    engine_ok, _ = is_engine_available()
    stockfish_status = "ok" if engine_ok else "missing"

    # 4. Version Info
    version = {
        "sha": os.environ.get("GIT_SHA", "unknown"),
        "built_at": os.environ.get("BUILD_TIME", datetime.now(timezone.utc).isoformat())
    }

    return {
        "ok": db_status == "ok" and worker_status == "ok",
        "db": db_status,
        "worker": worker_status,
        "stockfish": stockfish_status,
        "version": version
    }

@router.get("/status")
def get_ops_status(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    
    # 1. Active job
    stmt_active = select(Job).where(Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value])).order_by(Job.created_at.desc()).limit(1)
    active_job = db.scalars(stmt_active).first()

    # 2. Recent jobs (last 20)
    stmt_recent = select(Job).order_by(Job.created_at.desc()).limit(20)
    recent_jobs = db.scalars(stmt_recent).all()

    # 3. Metrics (last 24h)
    yesterday = now - timedelta(hours=24)
    
    # Basic counts
    stmt_counts = select(
        Job.status,
        func.count(Job.id)
    ).where(Job.created_at >= yesterday).group_by(Job.status)
    counts = dict(db.execute(stmt_counts).all())
    
    succeeded_count = counts.get(JobStatus.SUCCEEDED.value, 0)
    failed_count = counts.get(JobStatus.FAILED.value, 0)

    # Performance metrics from result_json
    # We'll calculate this in Python for simplicity with SQLite/JSON fields
    stmt_metrics = select(Job.result_json, Job.updated_at, Job.created_at).where(
        Job.status == JobStatus.SUCCEEDED,
        Job.created_at >= yesterday
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

    return {
        "now": now.isoformat(),
        "active_job": active_job,
        "recent_jobs": recent_jobs,
        "last_recovery": worker.recovery_stats,
        "metrics": {
            "last_24h": {
                "jobs_succeeded": succeeded_count,
                "jobs_failed": failed_count,
                "avg_duration_ms": int(avg_duration_ms),
                "cache_hits": total_cache_hits,
                "cache_misses": total_cache_misses
            }
        }
    }


@router.get("/storage/report")
def get_storage_report(username: str | None = None, db: Session = Depends(get_db)):
    """Identify filesystem records not yet present in the database."""
    game_storage = get_storage()
    puzzle_storage = get_puzzle_storage()

    fs_users = set(game_storage.get_users()) | {
        user.name.lower() for user in puzzle_storage.puzzles_path.glob("*") if user.is_dir()
    }

    if username:
        usernames = {username.lower()}
    else:
        usernames = fs_users

    report = {}
    for user in sorted(usernames):
        fs_games = {meta.game_id for meta in game_storage.get_all_metadata(user)}
        db_games = set(
            row[0]
            for row in db.execute(select(Game.game_id).where(Game.username == user)).all()
        )
        missing_games = sorted(fs_games - db_games)

        fs_puzzles = {p.id for p in puzzle_storage.get_all_puzzles(user)}
        db_puzzles = set(
            row[0]
            for row in db.execute(select(Puzzle.id).where(Puzzle.username == user)).all()
        )
        missing_puzzles = sorted(fs_puzzles - db_puzzles)

        report[user] = {
            "missing_games_count": len(missing_games),
            "missing_puzzles_count": len(missing_puzzles),
            "missing_games_sample": missing_games[:20],
            "missing_puzzles_sample": missing_puzzles[:20],
        }

    return {
        "user_count": len(report),
        "report": report,
    }
