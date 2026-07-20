"""Shared pytest fixtures for the API test suite."""

import pytest


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
