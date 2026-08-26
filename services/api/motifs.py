"""
Motif performance tracking module.
Provides analytics on user performance across different chess tactical patterns/motifs.
"""

from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.api.analytics_confidence import MIN_ATTEMPTS_FOR_MOTIF_RANK
from services.api.diagnosis.clusters import usable_motif
from services.api.models import PuzzleStats

MotifRank = Literal["needs_work", "learning", "mastered"]


class MotifPerformance(BaseModel):
    """Performance statistics for a single chess motif/pattern.

    Descriptive only. `rank` is a bucketed view of observed accuracy; when
    `attempts` is below MIN_ATTEMPTS_FOR_MOTIF_RANK the accuracy is not yet
    reliable and `insufficient_data` is True. Such motifs are excluded from
    `weakest_motifs` so one unlucky attempt is never called a weakness.
    """

    name: str
    total_puzzles: int
    passed: int
    accuracy: float
    rank: MotifRank
    attempts: int
    insufficient_data: bool


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
            func.sum(PuzzleStats.attempts).label("attempts"),
        )
        .where(
            PuzzleStats.username == username,
            PuzzleStats.primary_motif.isnot(None),
            PuzzleStats.attempts > 0,
        )
        .group_by(PuzzleStats.primary_motif)
        .order_by(func.sum(PuzzleStats.pass_count) / func.sum(PuzzleStats.attempts))
    )

    results = db.execute(stmt).all()

    motifs = []
    for row in results:
        # "blunder" is the placeholder classification assign_primary_motif
        # falls back to when no tactic was identified — a row with no motif,
        # not a pattern one can master. The identity design (step 7, #409)
        # already strips it from every puzzle payload via usable_motif();
        # this aggregation is the same contract at analytics grain. Without
        # the filter it surfaces as the user's #1 "Weak Area", which is
        # exactly the label the design retires.
        motif_name = usable_motif(row.primary_motif)
        if motif_name is None:
            continue
        total = row.total_puzzles
        passed = row.passed or 0
        attempts = row.attempts or 0

        # Calculate accuracy (avoid division by zero)
        accuracy = passed / attempts if attempts > 0 else 0.0
        rank = calculate_motif_rank(accuracy)
        insufficient_data = attempts < MIN_ATTEMPTS_FOR_MOTIF_RANK

        motifs.append(
            MotifPerformance(
                name=motif_name,
                total_puzzles=total,
                passed=passed,
                accuracy=accuracy,
                rank=rank,
                attempts=attempts,
                insufficient_data=insufficient_data,
            )
        )

    # Identify weakest motifs (bottom 2, needs_work rank only). Motifs with too
    # few attempts are excluded — an unreliable accuracy is not a "weakness".
    weakest = [
        m.name for m in motifs if m.rank == "needs_work" and not m.insufficient_data
    ][:2]

    return MotifPerformanceResponse(
        motifs=motifs, weakest_motifs=weakest, total_motifs_practiced=len(motifs)
    )
