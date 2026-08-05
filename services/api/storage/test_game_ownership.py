"""Regression tests for per-user game ownership (AUDIT GATE 2).

A physical chess game (identified by its canonical URL) is played by two
participants. When BOTH participants import the same game, each must end up
owning their own copy of it: get_game_count / get_all_metadata / get_pgns must
return the game for each user independently.

Previously ``games.game_id`` was a GLOBAL primary key with a single
``username`` column, and ``store_game`` short-circuited on the mere existence
of the row without checking the caller's username -- so the second importer
never gained ownership.
"""

import pytest

from services.api.storage.game_repository import GameRepository

SHARED_URL = "https://chess.com/game/shared-between-two-players"


def _store_for(repository, username, *, url=SHARED_URL):
    """Store the same physical game as imported by ``username``."""
    return repository.store_game(
        username=username,
        url=url,
        pgn='[Event "Shared"]\n1. e4 e5 *',
        white_username="alice",
        black_username="bob",
        white_result="win",
        black_result="lose",
        time_control="600",
        end_time=1704067200,
        rated=True,
    )


@pytest.fixture
def repository(db_session):
    return GameRepository(db_session)


def test_both_participants_own_the_same_game(repository):
    """The opponent who imports the same game must also own it."""
    is_new_a, game_id_a = _store_for(repository, "alice")
    is_new_b, game_id_b = _store_for(repository, "bob")

    # Same canonical game -> same game_id, but each import is a NEW ownership.
    assert game_id_a == game_id_b
    assert is_new_a is True
    assert is_new_b is True, "opponent's import must create their own ownership"

    # Both users can see and own the game.
    assert repository.get_game_count("alice") == 1
    assert repository.get_game_count("bob") == 1

    alice_meta = repository.get_all_metadata("alice")
    bob_meta = repository.get_all_metadata("bob")
    assert [m.game_id for m in alice_meta] == [game_id_a]
    assert [m.game_id for m in bob_meta] == [game_id_b]
    assert alice_meta[0].username == "alice"
    assert bob_meta[0].username == "bob"

    # Each user can pull the PGN for their own copy.
    assert repository.get_pgns("alice", [game_id_a]) == {
        game_id_a: '[Event "Shared"]\n1. e4 e5 *'
    }
    assert repository.get_pgns("bob", [game_id_b]) == {
        game_id_b: '[Event "Shared"]\n1. e4 e5 *'
    }
    assert repository.get_pgn("alice", game_id_a) == '[Event "Shared"]\n1. e4 e5 *'
    assert repository.get_pgn("bob", game_id_b) == '[Event "Shared"]\n1. e4 e5 *'


def test_reimport_by_same_user_is_deduplicated(repository):
    """Ownership is still idempotent per user (no double-counting)."""
    is_new_1, _ = _store_for(repository, "alice")
    is_new_2, _ = _store_for(repository, "alice")

    assert is_new_1 is True
    assert is_new_2 is False
    assert repository.get_game_count("alice") == 1


def test_empty_url_is_rejected_to_avoid_identity_collapse(repository):
    """A missing/empty url would collapse every game to one sha256 hash.

    Guard against silently merging unrelated games (or a user's whole library)
    into a single identity.
    """
    with pytest.raises(ValueError):
        _store_for(repository, "alice", url="")
