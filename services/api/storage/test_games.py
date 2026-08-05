"""Tests for the game repository module."""

from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from scripts.backfill_storage import validate_game_metadata
from services.api.storage.game_repository import GameRepository


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


def test_store_game_batch_mode_defers_commit(repository, db_session):
    """With commit=False, store_game never commits; the caller owns it."""
    with patch.object(db_session, "commit", wraps=db_session.commit) as mock_commit:
        for i in range(3):
            is_new, _ = repository.store_game(
                username="testuser",
                url=f"https://chess.com/game/{i}",
                pgn='[Event "Test"]\n1. e4 e5 *',
                white_username="testuser",
                black_username="opponent",
                white_result="win",
                black_result="lose",
                time_control="600",
                end_time=1704067200 + i,
                rated=True,
                commit=False,
            )
            assert is_new is True
        mock_commit.assert_not_called()

    db_session.commit()
    assert repository.get_game_count("testuser") == 3


def test_store_game_batch_mode_deduplicates_within_batch(repository, db_session):
    """Duplicates inside an uncommitted batch are still detected."""
    url = "https://chess.com/game/12345"
    results = [
        repository.store_game(
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
            commit=False,
        )
        for _ in range(2)
    ]

    assert results[0][0] is True
    assert results[1][0] is False
    assert results[0][1] == results[1][1]

    db_session.commit()
    assert repository.get_game_count("testuser") == 1


def test_store_game_batch_mode_survives_concurrent_insert_race(repository, db_session):
    """A flush-time IntegrityError (concurrent-writer race) is contained.

    Exercises the savepoint branch in store_game: when another session commits
    the same game between our existence check and the INSERT, the flush raises
    IntegrityError inside the savepoint. Only that row must roll back — the
    duplicate is reported as (False, game_id), previously flushed batch rows
    survive, and the batch session keeps working.
    """

    def _store_in_batch(url):
        return repository.store_game(
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
            commit=False,
        )

    dup_url = "https://chess.com/game/raced"

    # Simulate the concurrent importer: a second session commits the same
    # game before the batch session tries to insert it.
    other_session = sessionmaker(bind=db_session.get_bind())()
    try:
        is_new_other, dup_id = GameRepository(other_session).store_game(
            username="testuser",
            url=dup_url,
            pgn='[Event "Test"]\n1. e4 e5 *',
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
        assert is_new_other is True
    finally:
        other_session.close()

    # Flush one row in the batch first, to prove the later savepoint rollback
    # does not take previously flushed batch rows down with it.
    is_new_a, _ = _store_in_batch("https://chess.com/game/a")
    assert is_new_a is True

    # store_game's db.get() guard would see the already-committed row and
    # short-circuit before ever reaching the INSERT, so the flush-time race
    # cannot be reproduced naturally in a single-threaded test. Patch the
    # guard lookup to miss, reproducing the real race where the concurrent
    # row lands between the guard SELECT and the INSERT.
    with patch.object(db_session, "get", return_value=None):
        is_new_dup, returned_id = _store_in_batch(dup_url)
    assert is_new_dup is False
    assert returned_id == dup_id

    # The batch session is still usable after the contained rollback.
    is_new_c, _ = _store_in_batch("https://chess.com/game/c")
    assert is_new_c is True

    # The outer commit persists every non-duplicate row exactly once.
    db_session.commit()
    assert repository.get_game_count("testuser") == 3


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


def _store_game(repository, username, index):
    _, game_id = repository.store_game(
        username=username,
        url=f"https://chess.com/game/{username}/{index}",
        pgn=f'[Event "{username} {index}"]\n1. e4 e5 *',
        white_username=username,
        black_username=f"opponent{index}",
        white_result="win",
        black_result="lose",
        time_control="600",
        end_time=1704067200 + index * 1000,
        rated=True,
    )
    return game_id


def test_game_repository_get_pgns_bulk(repository):
    game_ids = [_store_game(repository, "testuser", i) for i in range(3)]

    pgns = repository.get_pgns("testuser", game_ids)

    assert set(pgns) == set(game_ids)
    for i, game_id in enumerate(game_ids):
        assert pgns[game_id] == f'[Event "testuser {i}"]\n1. e4 e5 *'

    # Unknown ids are silently omitted
    assert repository.get_pgns("testuser", ["missing-id"]) == {}
    # Empty input returns an empty mapping without querying
    assert repository.get_pgns("testuser", []) == {}


def test_game_repository_get_pgns_scoped_to_username(repository):
    own_id = _store_game(repository, "testuser", 0)
    other_id = _store_game(repository, "otheruser", 1)

    # Requesting another user's game id must not leak their PGN
    pgns = repository.get_pgns("testuser", [own_id, other_id])
    assert set(pgns) == {own_id}

    # Case-insensitive username matching, consistent with get_pgn
    assert set(repository.get_pgns("TESTUSER", [own_id])) == {own_id}


def test_game_repository_iter_pgns_preserves_order_and_batches(repository, monkeypatch):
    monkeypatch.setattr("services.api.storage.game_repository.PGN_BATCH_SIZE", 2)
    game_ids = [_store_game(repository, "testuser", i) for i in range(5)]

    pgns = list(repository.iter_pgns("testuser", game_ids))

    assert pgns == [f'[Event "testuser {i}"]\n1. e4 e5 *' for i in range(5)]

    # Ids owned by other users are skipped while order is preserved
    other_id = _store_game(repository, "otheruser", 9)
    pgns = list(repository.iter_pgns("testuser", [game_ids[2], other_id, game_ids[0]]))
    assert pgns == [
        '[Event "testuser 2"]\n1. e4 e5 *',
        '[Event "testuser 0"]\n1. e4 e5 *',
    ]


def test_validate_game_metadata_missing_fields():
    errors = validate_game_metadata({"game_id": "g1"})
    assert any(error.startswith("missing_fields") for error in errors)


# See services.api.usernames for why these four spellings and not just one:
# each folds to "testuser" under canonical_username, and each survives a bare
# .lower() as a key that matches no row.
NONCANONICAL_SPELLINGS = [
    " TestUser ",
    "ＴＥＳＴＵＳＥＲ",
    "TESTUSER\xa0",
    " Ｔestuser\xa0",
]


@pytest.mark.parametrize("handle", NONCANONICAL_SPELLINGS)
def test_game_repository_folds_noncanonical_usernames(repository, handle):
    """A non-canonical handle reads and writes the same rows as its canonical form.

    The pre-existing coverage above only exercised case ("TESTUSER"), which a
    plain .lower() satisfies. Whitespace and compatibility forms do not: they
    produce a key that silently matches nothing, and an empty read here means
    "this user has imported no games" — which the import path then acts on.

    Reads AND writes are asserted together on purpose. A fold on one side only
    is worse than none: store_game would insert under ' testuser ' while every
    query looked under 'testuser', so the game would exist and be invisible.
    """
    own_id = _store_game(repository, "testuser", 0)

    assert repository.get_game_count(handle) == 1
    assert repository.get_pgn(handle, own_id) is not None
    assert set(repository.get_pgns(handle, [own_id])) == {own_id}
    assert list(repository.iter_pgns(handle, [own_id])) == [
        '[Event "testuser 0"]\n1. e4 e5 *'
    ]
    assert [m.game_id for m in repository.get_all_metadata(handle)] == [own_id]

    # A write under the ugly spelling must land on the SAME key, not fork.
    repository.record_import_summary(handle, new_games=7)
    summary = repository.get_last_import_summary("testuser")
    assert summary is not None and summary["last_new_games"] == 7

    # And storing a game under the ugly spelling must not create a second user.
    repository.store_game(
        username=handle,
        url="https://chess.com/game/noncanonical",
        pgn='[Event "x"]\n1. e4 *',
        white_username="testuser",
        black_username="opponent",
        white_result="win",
        black_result="lose",
        time_control="600",
        end_time=1234567890,
        rated=True,
    )
    assert repository.get_users() == ["testuser"]
