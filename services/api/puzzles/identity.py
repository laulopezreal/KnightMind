"""
Puzzle identity logic.

Handles motif assignment, title generation, and backfilling identity data.
"""
import logging
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from services.api.models import PuzzleStats
from services.api.storage import PuzzleRepository

logger = logging.getLogger(__name__)

# Motif to Title mapping
MOTIF_TITLES = {
    "back_rank": "Back Rank Panic",
    "hanging_queen": "The Hanging Queen",
    "hanging_piece": "Loose Piece",
    "fork": "The Fork",
    "pin": "Pinned and Lost",
    "mate_threat": "Missed Mate",
    "blunder": "The Missed Win",
}

def assign_primary_motif(puzzle_data) -> str:
    """
    Assign a primary motif to a puzzle based on its metadata.
    
    Args:
        puzzle_data: Puzzle object or dict with puzzle metadata.
        
    Returns:
        The primary motif string.
        Defaults to "blunder" if no specific motif is detected.
    """
    # Placeholder for future logic when we have richer metadata.
    # Currently we only store FEN/Moves/Eval, not themes.
    # So we strictly default to "blunder" as per requirements.
    return "blunder"

def generate_puzzle_title(primary_motif: str) -> str:
    """
    Generate a human-readable title from a primary motif.
    
    Args:
        primary_motif: The primary motif string.
        
    Returns:
        The generated title string.
    """
    return MOTIF_TITLES.get(primary_motif, "Puzzle")

def backfill_puzzle_identity(db: Session):
    """
    Backfill missing identity data (title, primary_motif) for existing puzzles.
    Only updates records where title is NULL.
    """
    logger.info("Starting puzzle identity backfill check...")

    try:
        # query all stats where title is NULL
        stmt = select(PuzzleStats).where(PuzzleStats.title.is_(None))
        stats_to_update = db.scalars(stmt).all()
    except OperationalError:
        # Table may not exist yet (e.g. in test environments or before migrations)
        logger.warning("puzzle_stats table not available, skipping backfill.")
        db.rollback()
        return

    if not stats_to_update:
        logger.info("No puzzles need identity backfill.")
        return

    count = 0
    puzzle_repository = PuzzleRepository(db)

    for stats in stats_to_update:
        # Load puzzle data to (potentially) determine motif
        puzzle = puzzle_repository.get_puzzle(stats.username, stats.puzzle_id)

        # Determine motif
        motif = assign_primary_motif(puzzle)

        # Generate title
        title = generate_puzzle_title(motif)

        # Update DB
        stats.primary_motif = motif
        stats.title = title
        count += 1

    db.commit()
    logger.info(f"Backfilled identity for {count} puzzles.")
