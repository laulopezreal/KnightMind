"""Hourly housekeeping run from the app lifespan.

Username note: neither sweep is user-scoped — ``cleanup_abandoned_sessions``
predicates on ``completed_at``/``created_at`` and ``purge_expired_ai_audit`` on
the retention window. There is deliberately no username fold here because there
is no username: adding one would be a scope change, not a correctness fix. If a
per-user sweep is ever added it must fold with ``canonical_username`` like every
other storage entry point (see ``services.api.usernames``).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session

from services.api.db import SessionLocal
from services.api.models import TrainingSession
from services.api.storage.ai_audit_repository import AIAuditRepository

# Hourly. Low-frequency housekeeping; the interval is not load-bearing.
CLEANUP_INTERVAL_SECONDS = 3600


def cleanup_abandoned_sessions(db: Session, hours_threshold: int = 24) -> int:
    """
    Auto-complete sessions that have been abandoned (not completed after threshold).

    Args:
        db: Database session
        hours_threshold: Number of hours after which a session is considered abandoned

    Returns:
        Number of sessions auto-completed
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours_threshold)

    # Use bulk update for efficiency
    stmt = (
        update(TrainingSession)
        .where(
            TrainingSession.completed_at.is_(None),
            TrainingSession.created_at < cutoff_time,
        )
        .values(completed_at=datetime.now(timezone.utc))
    )

    result = db.execute(stmt)
    count = cast(CursorResult, result).rowcount

    if count > 0:
        db.commit()
        print(f"Auto-completed {count} abandoned session(s)")

    return count


def purge_expired_ai_audit(db: Session) -> int:
    """Drop AI diagnosis audit rows past the retention window.

    Rides the existing cleanup loop rather than introducing a scheduler: that
    loop already runs hourly from the app lifespan, and a retention sweep is
    exactly the kind of low-frequency housekeeping it exists for.

    Retention is deliberate, not incidental — prompts and responses are kept
    long enough to investigate an incident and no longer.
    """
    removed = AIAuditRepository(db).purge_older_than()
    if removed:
        db.commit()
        print(f"Purged {removed} expired AI audit row(s)")
    return removed


async def run_session_cleanup():
    """Background task for periodic housekeeping.

    Session cleanup and AI-audit retention run in separate try blocks on
    purpose: a failure in one must not skip the other, and a stalled retention
    sweep would let prompt/response blobs accumulate past their window
    silently.
    """
    while True:
        try:
            # Run cleanup
            with SessionLocal() as db:
                await asyncio.to_thread(cleanup_abandoned_sessions, db)
        except Exception as e:
            print(f"Error in session cleanup: {e}")

        try:
            with SessionLocal() as db:
                await asyncio.to_thread(purge_expired_ai_audit, db)
        except Exception as e:
            print(f"Error in AI audit purge: {e}")

        # Sleep for defined interval
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
