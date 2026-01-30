"""
Spaced repetition storage module.
Handles database operations for puzzle reviews and statistics.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select
from services.api.models import PuzzleStats, PuzzleReview, PuzzleResult


def get_puzzle_stats(db: Session, puzzle_id: str, username: str) -> PuzzleStats | None:
    """Get statistics for a specific puzzle and user."""
    stmt = select(PuzzleStats).where(
        PuzzleStats.puzzle_id == puzzle_id,
        PuzzleStats.username == username
    )
    return db.scalars(stmt).first()


def insert_puzzle_review(
    db: Session,
    puzzle_id: str,
    username: str,
    result: PuzzleResult | str,
    time_spent_ms: int | None = None,
    reviewed_at: datetime | None = None
) -> PuzzleReview:
    """
    Record a puzzle review in the database.
    
    Args:
        db: Database session
        puzzle_id: ID of the puzzle
        username: Username of the reviewer
        result: 'pass' or 'fail' (or PuzzleResult enum)
        time_spent_ms: Time spent on the puzzle in milliseconds
        reviewed_at: Timestamp of the review (defaults to current time)
        
    Returns:
        The created PuzzleReview object
    """
    if reviewed_at is None:
        reviewed_at = datetime.now(timezone.utc)
        
    # Ensure result is a string if Enum is passed
    result_val = result.value if isinstance(result, PuzzleResult) else result
    
    review = PuzzleReview(
        puzzle_id=puzzle_id,
        username=username,
        reviewed_at=reviewed_at,
        result=result_val,
        time_spent_ms=time_spent_ms
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def update_puzzle_stats(
    db: Session,
    puzzle_id: str,
    username: str,
    result: PuzzleResult | str,
    reviewed_at: datetime | None = None
) -> PuzzleStats:
    """
    Update aggregate statistics for a puzzle.
    Creates the stats entry if it doesn't exist.
    
    Args:
        db: Database session
        puzzle_id: ID of the puzzle
        username: Username of the player
        result: 'pass' or 'fail' (or PuzzleResult enum)
        reviewed_at: Timestamp of the review (defaults to current time)
        
    Returns:
        The updated PuzzleStats object
    """
    if reviewed_at is None:
        reviewed_at = datetime.now(timezone.utc)
        
    stats = get_puzzle_stats(db, puzzle_id, username)
    result_val = result.value if isinstance(result, PuzzleResult) else result
    
    if not stats:
        stats = PuzzleStats(
            puzzle_id=puzzle_id,
            username=username,
            attempts=0,
            pass_count=0,
            fail_count=0,
            ease_factor=2.0
        )
        db.add(stats)
    
    stats.attempts += 1
    if result_val == 'pass':
        stats.pass_count += 1
    else:
        stats.fail_count += 1
        
    stats.last_reviewed_at = reviewed_at
    stats.last_result = result_val
    
    db.commit()
    db.refresh(stats)
    return stats
