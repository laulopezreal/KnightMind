import pytest
from unittest.mock import MagicMock
from services.api.puzzles.identity import (
    assign_primary_motif,
    generate_puzzle_title,
    backfill_puzzle_identity,
    MOTIF_TITLES,
)
from services.api.models import PuzzleStats


def test_assign_primary_motif_default():
    """Verify that we default to 'blunder' when no metadata is available."""
    puzzle = {}
    assert assign_primary_motif(puzzle) == "blunder"


def test_generate_puzzle_title_mapping():
    """Verify all motif -> title mappings."""
    for motif, expected_title in MOTIF_TITLES.items():
        assert generate_puzzle_title(motif) == expected_title


def test_generate_puzzle_title_unknown():
    """Verify fallback for unknown motif."""
    assert generate_puzzle_title("unknown_motif") == "Puzzle"


def test_backfill_puzzle_identity(monkeypatch):
    """Verify backfill updates NULL titles and skips existing ones."""

    # Mock DB session
    mock_db = MagicMock()

    # Mock existing stats: one with NULL title, one with existing title
    stat_needs_update = MagicMock(spec=PuzzleStats)
    stat_needs_update.username = "user1"
    stat_needs_update.puzzle_id = "p1"
    stat_needs_update.title = None
    stat_needs_update.primary_motif = None

    # Mock db.scalars().all() return
    mock_db.scalars.return_value.all.return_value = [stat_needs_update]

    # Mock puzzle repository get_puzzle
    mock_repo = MagicMock()
    mock_puzzle = MagicMock()
    mock_repo.get_puzzle.return_value = mock_puzzle

    monkeypatch.setattr(
        "services.api.puzzles.identity.PuzzleRepository", lambda db: mock_repo
    )

    # Run backfill
    backfill_puzzle_identity(mock_db)

    # Verify updates
    assert stat_needs_update.primary_motif == "blunder"
    assert stat_needs_update.title == "The Missed Win"

    # Verify commit was called
    mock_db.commit.assert_called_once()
