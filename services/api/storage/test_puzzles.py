"""
Unit tests for puzzle repository.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.backfill_storage import validate_puzzle_data
from services.api.db import Base
from services.api.models import Game, PuzzleStats
from services.api.puzzles.identity import assign_primary_motif, generate_puzzle_title
from services.api.storage.puzzle_repository import PuzzleRepository


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def repository(db_session):
    # Insert a parent game for FK constraint
    db_session.add(
        Game(
            game_id="game123",
            url="https://chess.com/game/123",
            username="testuser",
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
    )
    db_session.commit()
    return PuzzleRepository(db_session)


def _add_game(db_session, game_id: str, username: str = "testuser"):
    existing = db_session.get(Game, (game_id, username))
    if not existing:
        db_session.add(
            Game(
                game_id=game_id,
                url=f"https://chess.com/game/{game_id}",
                username=username,
                white_username=username,
                black_username="opponent",
                white_result="win",
                black_result="lose",
                time_control="600",
                end_time=1704067200,
                rated=True,
            )
        )
        db_session.flush()


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


def test_puzzle_repository_deduplication(repository):
    is_new1, pid1 = repository.save_puzzle(
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
    is_new2, pid2 = repository.save_puzzle(
        username="testuser",
        source_game_id="game123",
        ply=15,
        fen="fen2",
        side_to_move="white",
        played_move_uci="e2e3",
        best_move_uci="d2d3",
        eval_before=1.0,
        eval_after=-2.0,
        swing=3.0,
    )

    assert is_new1 is True
    assert is_new2 is False
    assert pid1 == pid2
    assert repository.get_puzzle_count("testuser") == 1


def test_save_puzzle_creates_identity_stats(repository, db_session):
    fen = "3q3k/6pp/8/8/8/8/PP4PP/3Q2K1 w - - 0 1"
    is_new, puzzle_id = repository.save_puzzle(
        username="TestUser",
        source_game_id="game123",
        ply=15,
        fen=fen,
        side_to_move="white",
        played_move_uci="a2a3",
        best_move_uci="d1d5",
        eval_before=9.0,
        eval_after=0.0,
        swing=9.0,
    )

    assert is_new is True
    puzzle = repository.get_puzzle("testuser", puzzle_id)
    motif = assign_primary_motif(puzzle)

    stats = db_session.get(PuzzleStats, puzzle_id)
    assert stats is not None
    assert stats.username == "testuser"
    assert stats.primary_motif == motif
    assert stats.title == generate_puzzle_title(motif)
    assert stats.attempts == 0
    assert stats.pass_count == 0
    assert stats.fail_count == 0
    assert stats.ease_factor == 2.0


def test_duplicate_save_preserves_existing_stats(repository, db_session):
    is_new, puzzle_id = repository.save_puzzle(
        username="testuser",
        source_game_id="game123",
        ply=15,
        fen="3q3q1k/6pp/8/8/8/8/PP4PP/3Q2K1 w - - 0 1",
        side_to_move="white",
        played_move_uci="a2a3",
        best_move_uci="d1d8",
        eval_before=9.0,
        eval_after=0.0,
        swing=9.0,
    )
    stats = db_session.get(PuzzleStats, puzzle_id)
    stats.attempts = 7
    stats.pass_count = 5
    stats.fail_count = 2
    stats.primary_motif = "manual_motif"
    stats.title = "Manual title"
    db_session.commit()

    is_duplicate, duplicate_id = repository.save_puzzle(
        username="testuser",
        source_game_id="game123",
        ply=15,
        fen="8/8/8/8/8/8/8/8 w - - 0 1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=1.0,
        eval_after=-2.0,
        swing=3.0,
    )

    assert is_new is True
    assert is_duplicate is False
    assert duplicate_id == puzzle_id
    preserved = db_session.get(PuzzleStats, puzzle_id)
    assert preserved.attempts == 7
    assert preserved.pass_count == 5
    assert preserved.fail_count == 2
    assert preserved.primary_motif == "manual_motif"
    assert preserved.title == "Manual title"


def test_puzzle_repository_different_ply_not_duplicate(repository):
    is_new1, pid1 = repository.save_puzzle(
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
    is_new2, pid2 = repository.save_puzzle(
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
    assert pid1 != pid2
    assert repository.get_puzzle_count("testuser") == 2


def test_puzzle_repository_daily_puzzles(repository, db_session):
    # Create 5 puzzles
    for i in range(5):
        _add_game(db_session, f"game-daily-{i}")
        repository.save_puzzle(
            username="testuser",
            source_game_id=f"game-daily-{i}",
            ply=10 + i,
            fen=f"fen{i}",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
        )

    daily = repository.get_daily_puzzles("testuser", n=3)
    assert len(daily) == 3


def test_puzzle_repository_mark_with_specific_date(repository):
    _, puzzle_id = repository.save_puzzle(
        username="testuser",
        source_game_id="game123",
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
    repository.mark_puzzles_used("testuser", [puzzle_id], used_date=specific_date)

    puzzle = repository.get_puzzle("testuser", puzzle_id)
    assert puzzle.used_on == "2024-01-15"


def test_puzzle_repository_username_case_insensitive(repository):
    is_new, pid = repository.save_puzzle(
        username="TestUser",
        source_game_id="game123",
        ply=10,
        fen="fen1",
        side_to_move="white",
        played_move_uci="e2e4",
        best_move_uci="d2d4",
        eval_before=0.5,
        eval_after=-1.5,
        swing=2.0,
    )
    assert is_new is True

    puzzle = repository.get_puzzle("testuser", pid)
    assert puzzle is not None


def test_validate_puzzle_data_missing_fields():
    errors = validate_puzzle_data({"id": "p1"})
    assert any(error.startswith("missing_fields") for error in errors)
