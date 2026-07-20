"""Unit tests for the Chess.com ingest layer, focused on incremental sync.

Chess.com is fully mocked here (get_player_archives / fetch_games_from_archive
are patched); no network calls are made.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.ingest import chesscom
from services.ingest.chesscom import (
    RateLimitError,
    _archive_year_month,
    import_all_games,
)

BASE = "https://api.chess.com/pub/player/testuser/games"

# Three consecutive monthly archives with one game each.
ARCHIVES = [f"{BASE}/2023/11", f"{BASE}/2023/12", f"{BASE}/2024/01"]


def _game(url: str, end_time: int) -> dict:
    return {
        "url": url,
        "pgn": '[Event "Test"]\n\n1. e4 e5 1/2-1/2',
        "time_control": "600",
        "end_time": end_time,
        "rated": True,
        "white": {"username": "testuser", "result": "win"},
        "black": {"username": "opponent", "result": "lose"},
    }


# end_time roughly inside each archive's month (UTC).
GAMES_BY_ARCHIVE = {
    ARCHIVES[0]: [
        _game("g-2023-11", int(datetime(2023, 11, 15, tzinfo=timezone.utc).timestamp()))
    ],
    ARCHIVES[1]: [
        _game("g-2023-12", int(datetime(2023, 12, 15, tzinfo=timezone.utc).timestamp()))
    ],
    ARCHIVES[2]: [
        _game("g-2024-01", int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp()))
    ],
}


async def _collect(username, since=None):
    return [g async for g in import_all_games(username, since=since)]


def _run(coro):
    return asyncio.run(coro)


def _fetch_mock():
    """AsyncMock that returns the games for whichever archive is requested."""
    return AsyncMock(side_effect=lambda url: list(GAMES_BY_ARCHIVE.get(url, [])))


# --- _archive_year_month ---


@pytest.mark.parametrize(
    "url,expected",
    [
        (f"{BASE}/2024/01", (2024, 1)),
        (f"{BASE}/2024/01/", (2024, 1)),  # trailing slash tolerated
        (f"{BASE}/2023/12", (2023, 12)),
        ("not-a-real-url", None),
        (f"{BASE}/2024/13", None),  # month out of range
        (f"{BASE}/abcd/ef", None),  # non-numeric
    ],
)
def test_archive_year_month_parsing(url, expected):
    assert _archive_year_month(url) == expected


# --- incremental sync ---


def test_first_sync_fetches_every_archive():
    """No `since` (first sync) downloads the full history."""
    fetch = _fetch_mock()
    with (
        patch.object(chesscom, "get_player_archives", AsyncMock(return_value=ARCHIVES)),
        patch.object(chesscom, "fetch_games_from_archive", fetch),
    ):
        games = _run(_collect("testuser", since=None))

    assert len(games) == 3
    fetched = [c.args[0] for c in fetch.call_args_list]
    assert fetched == ARCHIVES  # all three months fetched


def test_second_sync_skips_fully_imported_older_months():
    """`since` in the latest month → only the latest month is re-fetched."""
    since = datetime(2024, 1, 20, tzinfo=timezone.utc)  # newest stored game's month
    fetch = _fetch_mock()
    with (
        patch.object(chesscom, "get_player_archives", AsyncMock(return_value=ARCHIVES)),
        patch.object(chesscom, "fetch_games_from_archive", fetch),
    ):
        games = _run(_collect("testuser", since=since))

    fetched = [c.args[0] for c in fetch.call_args_list]
    # Older months (2023/11, 2023/12) skipped; only the cutoff month fetched.
    assert fetched == [ARCHIVES[2]]
    assert [g.url for g in games] == ["g-2024-01"]


def test_resume_after_interrupted_sync_refetches_cutoff_and_newer():
    """A sync interrupted mid-history resumes from the last stored month.

    Simulates: 2023/11 fully stored, 2023/12 partially stored (so the newest
    stored game is in 2023/12), 2024/01 never fetched. Resuming must re-fetch
    2023/12 (to finish it) and 2024/01, and skip the completed 2023/11.
    """
    since = datetime(2023, 12, 10, tzinfo=timezone.utc)
    fetch = _fetch_mock()
    with (
        patch.object(chesscom, "get_player_archives", AsyncMock(return_value=ARCHIVES)),
        patch.object(chesscom, "fetch_games_from_archive", fetch),
    ):
        games = _run(_collect("testuser", since=since))

    fetched = [c.args[0] for c in fetch.call_args_list]
    assert fetched == [ARCHIVES[1], ARCHIVES[2]]  # 2023/11 skipped
    assert {g.url for g in games} == {"g-2023-12", "g-2024-01"}


def test_unparseable_archive_url_is_always_fetched():
    """A URL that doesn't match .../YYYY/MM is fetched rather than dropped."""
    weird = f"{BASE}/latest"
    archives = [ARCHIVES[0], weird, ARCHIVES[2]]
    since = datetime(2024, 1, 20, tzinfo=timezone.utc)
    fetch = AsyncMock(side_effect=lambda url: list(GAMES_BY_ARCHIVE.get(url, [])))
    with (
        patch.object(chesscom, "get_player_archives", AsyncMock(return_value=archives)),
        patch.object(chesscom, "fetch_games_from_archive", fetch),
    ):
        _run(_collect("testuser", since=since))

    fetched = [c.args[0] for c in fetch.call_args_list]
    # 2023/11 skipped (older, parseable); unparseable URL kept; 2024/01 kept.
    assert fetched == [weird, ARCHIVES[2]]


def test_rate_limit_during_incremental_fetch_propagates():
    """Rate-limit errors from a fetched archive still surface unchanged."""
    since = datetime(2024, 1, 20, tzinfo=timezone.utc)
    fetch = AsyncMock(side_effect=RateLimitError(retry_after=42))
    with (
        patch.object(chesscom, "get_player_archives", AsyncMock(return_value=ARCHIVES)),
        patch.object(chesscom, "fetch_games_from_archive", fetch),
    ):
        with pytest.raises(RateLimitError) as exc:
            _run(_collect("testuser", since=since))
    assert exc.value.retry_after == 42
