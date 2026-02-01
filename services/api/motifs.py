"""
Motif performance tracking module.
Provides analytics on user performance across different chess tactical patterns/motifs.
"""

from typing import Literal
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from pydantic import BaseModel
from services.api.models import PuzzleStats


MotifRank = Literal["needs_work", "learning", "mastered"]


class MotifPerformance(BaseModel):
    """Performance statistics for a single chess motif/pattern."""
    name: str
    total_puzzles: int
    passed: int
    accuracy: float
    rank: MotifRank


class MotifPerformanceResponse(BaseModel):
    """Complete motif performance breakdown for a user."""
    motifs: list[MotifPerformance]
    weakest_motifs: list[str]
    total_motifs_practiced: int


def calculate_motif_rank(accuracy: float) -> MotifRank:
    """
    Calculate the proficiency rank based on accuracy.

    Args:
        accuracy: Accuracy as a decimal (0.0 to 1.0)

    Returns:
        Rank classification: needs_work (<70%), learning (70-85%), mastered (>85%)
    """
    if accuracy < 0.70:
        return "needs_work"
    elif accuracy < 0.85:
        return "learning"
    else:
        return "mastered"


def get_user_motif_performance(db: Session, username: str) -> MotifPerformanceResponse:
    """
    Get user's performance breakdown across all chess motifs/tactical patterns.

    Args:
        db: Database session
        username: Username to query

    Returns:
        Complete motif performance report with accuracy, rankings, and weak areas
    """
    # Query for motif aggregation
    stmt = (
        select(
            PuzzleStats.primary_motif,
            func.count(PuzzleStats.puzzle_id).label("total_puzzles"),
            func.sum(PuzzleStats.pass_count).label("passed"),
            func.sum(PuzzleStats.attempts).label("attempts")
        )
        .where(
            PuzzleStats.username == username,
            PuzzleStats.primary_motif.isnot(None),
            PuzzleStats.attempts > 0
        )
        .group_by(PuzzleStats.primary_motif)
        .order_by(func.sum(PuzzleStats.pass_count) / func.sum(PuzzleStats.attempts))
    )

    results = db.execute(stmt).all()

    motifs = []
    for row in results:
        motif_name = row.primary_motif
        total = row.total_puzzles
        passed = row.passed or 0
        attempts = row.attempts or 0

        # Calculate accuracy (avoid division by zero)
        accuracy = passed / attempts if attempts > 0 else 0.0
        rank = calculate_motif_rank(accuracy)

        motifs.append(MotifPerformance(
            name=motif_name,
            total_puzzles=total,
            passed=passed,
            accuracy=accuracy,
            rank=rank
        ))

    # Identify weakest motifs (bottom 2, needs_work rank only)
    weakest = [
        m.name for m in motifs
        if m.rank == "needs_work"
    ][:2]

    return MotifPerformanceResponse(
        motifs=motifs,
        weakest_motifs=weakest,
        total_motifs_practiced=len(motifs)
    )
