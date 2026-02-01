"""
Unit tests for puzzle storage.
"""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from services.api.storage.puzzles import Puzzle, PuzzleStorage
from scripts.backfill_storage import validate_puzzle_data
from services.api.db import Base
from services.api.storage.puzzle_repository import PuzzleRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def temp_storage():
    """Create a temporary puzzle storage for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield PuzzleStorage(base_path=tmpdir)


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_STORAGE_MODE", "database")
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def repository(db_session, tmp_path):
    return PuzzleRepository(db_session, base_path=tmp_path)


def test_save_puzzle_creates_new(temp_storage):
    """Test that saving a puzzle creates a new puzzle."""
    is_new, puzzle_id = temp_storage.save_puzzle(
        username="testuser",
        source_game_id="game123",
        ply=15,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    assert is_new is True
    assert puzzle_id is not None

    # Verify puzzle was saved
    puzzle = temp_storage.get_puzzle("testuser", puzzle_id)
    assert puzzle is not None
    assert puzzle.username == "testuser"
    assert puzzle.source_game_id == "game123"
    assert puzzle.ply == 15
    assert puzzle.swing == 2.0
    assert puzzle.used_on is None


def test_save_puzzle_deduplication(temp_storage):
    """Test that duplicate puzzles are detected and not saved."""
    # Save first puzzle
    is_new1, puzzle_id1 = temp_storage.save_puzzle(
        username="testuser",
        source_game_id="game123",
        ply=15,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    # Try to save same puzzle again (same username, game_id, ply)
    is_new2, puzzle_id2 = temp_storage.save_puzzle(
        username="testuser",
        source_game_id="game123",
        ply=15,
        fen="different_fen",  # Even with different data
        side_to_move="white",
        played_move_uci="e2e3",
        best_move_uci="d2d3",
        eval_before=1.0,
        eval_after=-2.0,
        swing=3.0,
    )

    assert is_new1 is True
    assert is_new2 is False
    assert puzzle_id1 == puzzle_id2

    # Verify only one puzzle exists
    assert temp_storage.get_puzzle_count("testuser") == 1


def test_save_puzzle_different_ply_not_duplicate(temp_storage):
    """Test that puzzles with same game but different ply are not duplicates."""
    # Save puzzle at ply 15
    is_new1, puzzle_id1 = temp_storage.save_puzzle(
        username="testuser",
        source_game_id="game123",
        ply=15,
        fen="fen1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    # Save puzzle at ply 17 (different ply)
    is_new2, puzzle_id2 = temp_storage.save_puzzle(
        username="testuser",
        source_game_id="game123",
        ply=17,
        fen="fen2",
        side_to_move="white",
        played_move_uci="e2e5",
        best_move_uci="d2d5",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    assert is_new1 is True
    assert is_new2 is True
    assert puzzle_id1 != puzzle_id2
    assert temp_storage.get_puzzle_count("testuser") == 2


def test_save_puzzle_different_users_not_duplicate(temp_storage):
    """Test that same game/ply for different users are separate puzzles."""
    # Save for user1
    is_new1, puzzle_id1 = temp_storage.save_puzzle(
        username="user1",
        source_game_id="game123",
        ply=15,
        fen="fen1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    # Save for user2 (same game_id and ply)
    is_new2, puzzle_id2 = temp_storage.save_puzzle(
        username="user2",
        source_game_id="game123",
        ply=15,
        fen="fen1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    assert is_new1 is True
    assert is_new2 is True
    assert puzzle_id1 != puzzle_id2
    assert temp_storage.get_puzzle_count("user1") == 1
    assert temp_storage.get_puzzle_count("user2") == 1


def test_get_daily_puzzles_prefers_unused(temp_storage):
    """Test that daily puzzle selection prefers unused puzzles (when none used today)."""
    # Create 3 unused puzzles
    for i in range(3):
        temp_storage.save_puzzle(
            username="testuser",
            source_game_id=f"game{i}",
            ply=10 + i,
            fen=f"fen{i}",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
        )

    # Create 2 used puzzles (from yesterday)
    yesterday = date.today() - timedelta(days=1)
    for i in range(3, 5):
        _, puzzle_id = temp_storage.save_puzzle(
            username="testuser",
            source_game_id=f"game{i}",
            ply=10 + i,
            fen=f"fen{i}",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
        )
        temp_storage.mark_puzzles_used("testuser", [puzzle_id], yesterday)

    # Request 5 puzzles
    daily = temp_storage.get_daily_puzzles("testuser", n=5)

    assert len(daily) == 5
    # First 3 should be unused (no puzzles used today, so unused come first)
    assert all(p.used_on is None for p in daily[:3])
    # Last 2 should be used from yesterday
    assert all(p.used_on is not None for p in daily[3:])


def test_get_daily_puzzles_returns_less_when_not_enough(temp_storage):
    """Test that daily puzzles returns fewer than n when not enough exist."""
    # Create only 2 puzzles
    for i in range(2):
        temp_storage.save_puzzle(
            username="testuser",
            source_game_id=f"game{i}",
            ply=10 + i,
            fen=f"fen{i}",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
        )

    # Request 5 puzzles
    daily = temp_storage.get_daily_puzzles("testuser", n=5)

    assert len(daily) == 2


def test_get_daily_puzzles_empty_when_no_puzzles(temp_storage):
    """Test that daily puzzles returns empty list when no puzzles exist."""
    daily = temp_storage.get_daily_puzzles("nonexistent", n=5)
    assert daily == []


def test_mark_puzzles_used(temp_storage):
    """Test marking puzzles as used."""
    # Create 3 puzzles
    puzzle_ids = []
    for i in range(3):
        _, puzzle_id = temp_storage.save_puzzle(
            username="testuser",
            source_game_id=f"game{i}",
            ply=10 + i,
            fen=f"fen{i}",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
        )
        puzzle_ids.append(puzzle_id)

    # Mark first 2 as used
    marked = temp_storage.mark_puzzles_used("testuser", puzzle_ids[:2])

    assert marked == 2

    # Verify the puzzles are marked
    puzzle1 = temp_storage.get_puzzle("testuser", puzzle_ids[0])
    puzzle2 = temp_storage.get_puzzle("testuser", puzzle_ids[1])
    puzzle3 = temp_storage.get_puzzle("testuser", puzzle_ids[2])

    assert puzzle1.used_on is not None
    assert puzzle2.used_on is not None
    assert puzzle3.used_on is None


def test_mark_puzzles_used_with_specific_date(temp_storage):
    """Test marking puzzles with a specific date."""
    _, puzzle_id = temp_storage.save_puzzle(
        username="testuser",
        source_game_id="game1",
        ply=10,
        fen="fen1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    specific_date = date(2024, 1, 15)
    temp_storage.mark_puzzles_used("testuser", [puzzle_id], used_date=specific_date)

    puzzle = temp_storage.get_puzzle("testuser", puzzle_id)
    assert puzzle.used_on == "2024-01-15"


def test_get_all_puzzles(temp_storage):
    """Test getting all puzzles for a user."""
    # Create 5 puzzles
    for i in range(5):
        temp_storage.save_puzzle(
            username="testuser",
            source_game_id=f"game{i}",
            ply=10 + i,
            fen=f"fen{i}",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
        )

    all_puzzles = temp_storage.get_all_puzzles("testuser")
    assert len(all_puzzles) == 5


def test_get_puzzle_count(temp_storage):
    """Test puzzle count."""
    assert temp_storage.get_puzzle_count("testuser") == 0

    temp_storage.save_puzzle(
        username="testuser",
        source_game_id="game1",
        ply=10,
        fen="fen1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    assert temp_storage.get_puzzle_count("testuser") == 1


def test_username_case_insensitive(temp_storage):
    """Test that username handling is case-insensitive."""
    # Save with uppercase
    is_new1, puzzle_id1 = temp_storage.save_puzzle(
        username="TestUser",
        source_game_id="game1",
        ply=10,
        fen="fen1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    # Try to save with lowercase (should be duplicate)
    is_new2, puzzle_id2 = temp_storage.save_puzzle(
        username="testuser",
        source_game_id="game1",
        ply=10,
        fen="fen1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    assert is_new1 is True
    assert is_new2 is False
    assert puzzle_id1 == puzzle_id2

    # Both should return the same puzzle
    puzzle_upper = temp_storage.get_puzzle("TestUser", puzzle_id1)
    puzzle_lower = temp_storage.get_puzzle("testuser", puzzle_id1)
    assert puzzle_upper is not None
    assert puzzle_lower is not None
    assert puzzle_upper.id == puzzle_lower.id


def test_puzzle_repository_database_mode_stores_and_updates(repository):
    is_new, puzzle_id = repository.save_puzzle(
        username="testuser",
        source_game_id="game123",
        ply=15,
        fen="fen",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )

    assert is_new is True
    puzzle = repository.get_puzzle("testuser", puzzle_id)
    assert puzzle is not None

    marked = repository.mark_puzzles_used("testuser", [puzzle_id])
    assert marked == 1
    updated = repository.get_puzzle("testuser", puzzle_id)
    assert updated.used_on is not None


def test_validate_puzzle_data_missing_fields():
    errors = validate_puzzle_data({"id": "p1"})
    assert any(error.startswith("missing_fields") for error in errors)
