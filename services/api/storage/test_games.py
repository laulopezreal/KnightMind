"""Tests for the game repository module."""

import pytest

from scripts.backfill_storage import validate_game_metadata
from services.api.db import Base
from services.api.storage.game_repository import GameRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


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
    return GameRepository(db_session)


def test_game_repository_database_mode_stores_pgn(repository):
    is_new, game_id = repository.store_game(
        username="testuser",
        url="https://chess.com/game/12345",
        pgn='[Event "Test"]\n1. e4 e5 *',
        white_username="testuser",
        black_username="opponent",
        white_result="win",
        black_result="lose",
        time_control="600",
        end_time=1704067200,
        rated=True,
    )

    assert is_new is True
    assert repository.get_game_count("testuser") == 1
    assert repository.get_pgn("testuser", game_id) == '[Event "Test"]\n1. e4 e5 *'


def test_game_repository_get_users(repository):
    repository.store_game(
        username="user1",
        url="https://chess.com/game/1",
        pgn='[Event "Test"]\n1. e4 e5 *',
        white_username="user1",
        black_username="opponent",
        white_result="win",
        black_result="lose",
        time_control="600",
        end_time=1704067200,
        rated=True,
    )
    repository.store_game(
        username="user2",
        url="https://chess.com/game/2",
        pgn='[Event "Test"]\n1. e4 e5 *',
        white_username="user2",
        black_username="opponent",
        white_result="win",
        black_result="lose",
        time_control="600",
        end_time=1704067201,
        rated=True,
    )

    assert repository.get_users() == ["user1", "user2"]


def test_game_repository_deduplication(repository):
    url = "https://chess.com/game/12345"
    is_new1, game_id1 = repository.store_game(
        username="testuser",
        url=url,
        pgn='[Event "Test"]\n1. e4 e5 *',
        white_username="testuser",
        black_username="opponent",
        white_result="win",
        black_result="lose",
        time_control="600",
        end_time=1704067200,
        rated=True,
    )
    is_new2, game_id2 = repository.store_game(
        username="testuser",
        url=url,
        pgn='[Event "Test"]\n1. e4 e5 *',
        white_username="testuser",
        black_username="opponent",
        white_result="win",
        black_result="lose",
        time_control="600",
        end_time=1704067200,
        rated=True,
    )

    assert is_new1 is True
    assert is_new2 is False
    assert game_id1 == game_id2
    assert repository.get_game_count("testuser") == 1


def test_game_repository_get_all_metadata(repository):
    for i in range(3):
        repository.store_game(
            username="testuser",
            url=f"https://chess.com/game/{i}",
            pgn=f'[Event "Test {i}"]\n1. e4 e5 *',
            white_username="testuser",
            black_username=f"opponent{i}",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200 + i * 1000,
            rated=True,
        )

    metadata = repository.get_all_metadata("testuser")
    assert len(metadata) == 3
    # Should be sorted by end_time descending
    assert metadata[0].end_time > metadata[1].end_time
    assert metadata[1].end_time > metadata[2].end_time


def test_game_repository_username_case_insensitive(repository):
    repository.store_game(
        username="TestUser",
        url="https://chess.com/game/12345",
        pgn='[Event "Test"]\n1. e4 e5 *',
        white_username="testuser",
        black_username="opponent",
        white_result="win",
        black_result="lose",
        time_control="600",
        end_time=1704067200,
        rated=True,
    )

    assert repository.get_game_count("testuser") == 1
    assert repository.get_game_count("TESTUSER") == 1


def test_game_repository_import_summary(repository):
    assert repository.get_last_import_summary("testuser") is None

    repository.record_import_summary("testuser", 5)
    summary = repository.get_last_import_summary("testuser")
    assert summary is not None
    assert summary["last_new_games"] == 5
    assert summary["last_imported_at"] is not None

    # Update
    repository.record_import_summary("testuser", 3)
    summary = repository.get_last_import_summary("testuser")
    assert summary["last_new_games"] == 3


def test_validate_game_metadata_missing_fields():
    errors = validate_game_metadata({"game_id": "g1"})
    assert any(error.startswith("missing_fields") for error in errors)
