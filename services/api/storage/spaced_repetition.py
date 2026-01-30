"""
Spaced repetition storage module.
Handles database operations for puzzle reviews and statistics.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select
from services.api.models import PuzzleStats, PuzzleReview, PuzzleResult
from datetime import timedelta


def calculate_next_interval(
    current_interval: int | None,
    ease_factor: float,
    result: PuzzleResult | str
) -> tuple[int, float]:
    """
    Calculate the next interval and ease factor based on simple SR rules.
    
    Rules:
    - On FAIL: interval=1, ease=max(1.3, ease-0.2)
    - On PASS:
        - if interval is None (new): interval=1
        - else if interval == 1: interval=3
        - else: interval = round(interval * ease)
        - ease = min(2.8, ease+0.05)
    """
    res = result if isinstance(result, PuzzleResult) else PuzzleResult(result)
    
    if res == PuzzleResult.FAIL:
        new_interval = 1
        new_ease = max(1.3, ease_factor - 0.2)
    else:  # PASS
        if current_interval is None:
            new_interval = 1
        elif current_interval == 1:
            new_interval = 3
        else:
            new_interval = round(current_interval * ease_factor)
        
        new_ease = min(2.8, ease_factor + 0.05)
        
    return new_interval, new_ease


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
    res_enum = result if isinstance(result, PuzzleResult) else PuzzleResult(result)
    
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
    if res_enum == PuzzleResult.PASS:
        stats.pass_count += 1
    else:
        stats.fail_count += 1
        
    # Apply scheduling rules
    new_interval, new_ease = calculate_next_interval(
        stats.interval_days,
        stats.ease_factor,
        res_enum
    )
    
    stats.interval_days = new_interval
    stats.ease_factor = new_ease
    stats.next_due_at = reviewed_at + timedelta(days=new_interval)
    
    stats.last_reviewed_at = reviewed_at
    stats.last_result = res_enum.value
    
    db.commit()
    db.refresh(stats)
    return stats


def get_due_puzzles(
    db: Session,
    username: str,
    puzzle_ids: list[str],
    n: int = 5
) -> tuple[list[str], dict[str, PuzzleStats]]:
    """
    Get puzzles for the user from the candidate list, ordered by SR priority.
    
    Priority:
    1. Due: next_due_at <= now
    2. New: next_due_at IS NULL
    3. Future: ordered by next_due_at ASC
    """
    now = datetime.now(timezone.utc)
    
    # Query stats for the given puzzle IDs
    stmt = select(PuzzleStats).where(
        PuzzleStats.username == username,
        PuzzleStats.puzzle_id.in_(puzzle_ids)
    )
    all_stats = {s.puzzle_id: s for s in db.scalars(stmt).all()}
    
    # Sort candidate IDs
    def sort_key(pid: str):
        stats = all_stats.get(pid)
        if not stats or stats.next_due_at is None:
            # New puzzle: Priority 2 (high, but after due)
            return (1, now) 
        
        # Ensure stats.next_due_at is aware for comparison
        next_due = stats.next_due_at
        if next_due.tzinfo is None:
            next_due = next_due.replace(tzinfo=timezone.utc)
            
        if next_due <= now:
            # Due puzzle: Priority 1 (highest)
            return (0, next_due)
        
        # Future puzzle: Priority 3 (lowest)
        return (2, next_due)

    sorted_pids = sorted(puzzle_ids, key=sort_key)
    return sorted_pids[:n], all_stats
