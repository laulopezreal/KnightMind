"""Shared pytest fixtures for the API test suite."""

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _hermetic_auto_snapshot(monkeypatch):
    """Keep automatic rating snapshots off the network in every test.

    Auto-snapshot fetches Chess.com stats on session complete, game import,
    and (throttled) rating-insights views. Default the fetch to a network
    failure — production treats it as best-effort, so callers proceed — and
    clear the per-username throttle registry so state can't leak between
    tests. Tests that need snapshot data re-patch
    ``services.api.ratings_auto.get_player_stats``.
    """
    from services.api import ratings_auto
    from services.ingest import NetworkError

    ratings_auto.reset_throttle()
    monkeypatch.setattr(
        ratings_auto,
        "get_player_stats",
        AsyncMock(side_effect=NetworkError("network disabled in tests")),
    )
    yield
    ratings_auto.reset_throttle()


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Clear the in-process rate-limit registry around every test.

    The limiters (audit gate 10) are module-global and keyed by principal, so
    without this reset hits would accumulate across tests that share a principal
    (e.g. the TestClient's ``testclient`` IP) and later tests would spuriously
    see 429s. Resetting before and after keeps each test hermetic.
    """
    from services.api.ratelimit import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


@pytest.fixture(autouse=True)
def _reset_openings_cache():
    """Clear the opening-tree cache around every test.

    It is a module-global keyed partly by game count and latest game time, so
    two tests importing the same fixture games under the same username would
    produce the same key and the second would silently be served the first
    one's tree — masking real differences in what the endpoint built.
    """
    from services.api.openings import tree_cache

    tree_cache.clear()
    yield
    tree_cache.clear()
