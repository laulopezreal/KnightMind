"""
Background job to auto-complete abandoned training sessions.
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
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
    
    # Find abandoned sessions
    stmt = select(TrainingSession).where(
        TrainingSession.completed_at.is_(None),
        TrainingSession.created_at < cutoff_time
    )
    abandoned_sessions = db.scalars(stmt).all()
    
    # Mark them as completed
    count = 0
    for session in abandoned_sessions:
        session.completed_at = datetime.now(timezone.utc)
        count += 1
    
    if count > 0:
        db.commit()
        print(f"Auto-completed {count} abandoned session(s)")
    
    return count
