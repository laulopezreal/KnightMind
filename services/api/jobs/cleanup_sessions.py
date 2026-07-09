from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from services.api.models import TrainingSession


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
    count = result.rowcount

    if count > 0:
        db.commit()
        print(f"Auto-completed {count} abandoned session(s)")

    return count
