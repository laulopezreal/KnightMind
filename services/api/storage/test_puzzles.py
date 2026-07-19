"""
Unit tests for puzzle repository.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.backfill_storage import validate_puzzle_data
from services.api.db import Base
from services.api.models import Game
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
