"""Audit gate 10 — per-principal rate limiting + request-size caps.

Unit tests drive the ``RateLimiter`` with a deterministic injected clock (no
sleeping). Integration tests drive the limited routes through the TestClient
with auth OFF (principal = client IP) to prove over/under-limit behavior,
principal isolation, the ``Retry-After`` header, window reset, and the FEN
size cap.
"""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from typing import cast
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from services.api.engine import EvalResult
from services.api.main import app
from services.api.models import Account
from services.api.ratelimit import (
    RateLimiter,
    client_ip,
    get_limiter,
    principal_key,
    reset_limiters,
)


class FakeClock:
    """A controllable monotonic clock for deterministic window tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# --- Unit: RateLimiter ------------------------------------------------------


def test_under_limit_allows():
    clock = FakeClock()
    limiter = RateLimiter(limit=3, window_seconds=60, time_fn=clock)
    for _ in range(3):
        assert limiter.check("k").allowed


def test_over_limit_denies_with_retry_after():
    clock = FakeClock()
    limiter = RateLimiter(limit=2, window_seconds=60, time_fn=clock)
    assert limiter.check("k").allowed
    assert limiter.check("k").allowed
    decision = limiter.check("k")
    assert decision.allowed is False
    # All three hits at the same instant -> oldest ages out a full window later.
    assert decision.retry_after == 60


def test_distinct_principals_are_isolated():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_seconds=60, time_fn=clock)
    assert limiter.check("a").allowed
    assert limiter.check("a").allowed is False
    # A different key has its own independent window.
    assert limiter.check("b").allowed


def test_window_resets_after_it_elapses():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_seconds=60, time_fn=clock)
    assert limiter.check("k").allowed
    assert limiter.check("k").allowed is False
    clock.advance(60)  # oldest hit is now exactly at the cutoff -> drops out
    assert limiter.check("k").allowed


def test_retry_after_shrinks_as_time_passes():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_seconds=60, time_fn=clock)
    assert limiter.check("k").allowed
    clock.advance(45)
    assert limiter.check("k").retry_after == 15


def test_zero_limit_disables_the_limiter():
    limiter = RateLimiter(limit=0, window_seconds=60, time_fn=FakeClock())
    for _ in range(100):
        assert limiter.check("k").allowed


def test_reset_clears_state():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_seconds=60, time_fn=clock)
    assert limiter.check("k").allowed
    assert limiter.check("k").allowed is False
    limiter.reset()
    assert limiter.check("k").allowed


# --- Unit: key derivation ---------------------------------------------------


class _FakeRequest:
    """A structural stand-in for starlette's Request.

    client_ip/principal_key only read `.headers` and `.client.host`, so a real
    Request is unnecessary here -- but they are annotated with the real type,
    so the call sites cast. The cast is the honest record that this is a
    deliberate duck-type, not an accident.
    """

    def __init__(self, headers=None, host="9.9.9.9"):
        self.headers = headers or {}

        class _Client:
            def __init__(self, h):
                self.host = h

        self.client = _Client(host)


def test_client_ip_prefers_rightmost_xff_entry():
    # Client-spoofed left entry must be ignored; trust the proxy-appended right.
    req = _FakeRequest(headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
    assert client_ip(cast(Request, req)) == "5.6.7.8"


def test_client_ip_falls_back_to_socket_peer():
    assert client_ip(cast(Request, _FakeRequest(host="9.9.9.9"))) == "9.9.9.9"


def test_principal_key_uses_account_when_present():
    class _Acct:
        id = "acct-123"

    assert (
        principal_key(cast(Request, _FakeRequest()), cast(Account, _Acct()))
        == "acct:acct-123"
    )


def test_principal_key_falls_back_to_ip():
    req = _FakeRequest(headers={"x-forwarded-for": "5.6.7.8"})
    assert principal_key(cast(Request, req), None) == "ip:5.6.7.8"


# --- Integration: /engine/eval (auth OFF -> IP-keyed) -----------------------

_VALID_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _mock_eval():
    return patch(
        "services.api.engine_routes.get_or_compute_eval",
        return_value=EvalResult(
            best_move_uci="e2e4", eval=0.2, mate_in=None, is_terminal=False
        ),
    )


def test_engine_eval_over_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENGINE_EVAL", "2")
    reset_limiters()
    client = TestClient(app)
    with _mock_eval():
        for _ in range(2):
            ok = client.post("/engine/eval", json={"fen": _VALID_FEN})
            assert ok.status_code == 200
        limited = client.post("/engine/eval", json={"fen": _VALID_FEN})
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    assert int(limited.headers["Retry-After"]) >= 1


def test_engine_eval_under_limit_is_ok(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENGINE_EVAL", "5")
    reset_limiters()
    client = TestClient(app)
    with _mock_eval():
        for _ in range(5):
            resp = client.post("/engine/eval", json={"fen": _VALID_FEN})
            assert resp.status_code == 200


def test_distinct_ips_do_not_share_a_budget(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENGINE_EVAL", "1")
    reset_limiters()
    client = TestClient(app)
    with _mock_eval():
        a1 = client.post(
            "/engine/eval",
            json={"fen": _VALID_FEN},
            headers={"X-Forwarded-For": "1.1.1.1"},
        )
        a2 = client.post(
            "/engine/eval",
            json={"fen": _VALID_FEN},
            headers={"X-Forwarded-For": "1.1.1.1"},
        )
        b1 = client.post(
            "/engine/eval",
            json={"fen": _VALID_FEN},
            headers={"X-Forwarded-For": "2.2.2.2"},
        )
    assert a1.status_code == 200
    assert a2.status_code == 429  # second hit from the same IP is throttled
    assert b1.status_code == 200  # a different IP has its own budget


def test_window_reset_lets_the_caller_through_again(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENGINE_EVAL", "1")
    reset_limiters()
    clock = FakeClock()
    # Force the route's limiter to use our deterministic clock. The assert is
    # the narrowing: get_limiter can now return the Postgres-backed store, which
    # has no injectable clock because its window is wall-clock and shared.
    limiter = get_limiter("engine_eval", default_limit=30)
    assert isinstance(limiter, RateLimiter)
    limiter.time_fn = clock

    client = TestClient(app)
    with _mock_eval():
        first = client.post("/engine/eval", json={"fen": _VALID_FEN})
        blocked = client.post("/engine/eval", json={"fen": _VALID_FEN})
        clock.advance(60)
        after = client.post("/engine/eval", json={"fen": _VALID_FEN})
    assert first.status_code == 200
    assert blocked.status_code == 429
    assert after.status_code == 200


def test_oversized_fen_is_rejected_with_400():
    reset_limiters()
    client = TestClient(app)
    from services.api.engine_routes import MAX_FEN_LENGTH

    huge = "8/" * 400  # far longer than any legal FEN
    assert len(huge) > MAX_FEN_LENGTH
    with _mock_eval() as mock_eval:
        resp = client.post("/engine/eval", json={"fen": huge})
    assert resp.status_code == 400
    assert "too long" in resp.json()["detail"].lower()
    # Rejected before the engine was ever consulted.
    mock_eval.assert_not_called()


# --- the shared store -------------------------------------------------------
#
# The in-process limiter is correct for one uvicorn worker and wrong for two:
# each keeps its own window, so the effective limit multiplies by the worker
# count. That is what pinned the API to --workers 1. These pin the property that
# lifts it.


@pytest.mark.postgres
def test_postgres_limiter_shares_a_window_across_instances(db_session):
    """Two limiter instances = two API workers. They must count as one.

    Separate instances with no shared memory: this fails against the in-process
    limiter, which is the whole reason the Postgres store exists.
    """
    from services.api.ratelimit import PostgresRateLimiter

    worker_a = PostgresRateLimiter("engine_eval", limit=3, window_seconds=60)
    worker_b = PostgresRateLimiter("engine_eval", limit=3, window_seconds=60)
    worker_a.reset()

    assert worker_a.check("acct-1").allowed is True
    assert worker_b.check("acct-1").allowed is True
    assert worker_a.check("acct-1").allowed is True

    # Four hits against a limit of three, spread over both "workers".
    denied = worker_b.check("acct-1")
    assert denied.allowed is False
    assert denied.retry_after >= 1
    worker_a.reset()


@pytest.mark.postgres
def test_postgres_limiter_keeps_principals_apart(db_session):
    from services.api.ratelimit import PostgresRateLimiter

    limiter = PostgresRateLimiter("engine_eval", limit=1, window_seconds=60)
    limiter.reset()

    assert limiter.check("acct-1").allowed is True
    assert limiter.check("acct-1").allowed is False
    # A different principal has its own window.
    assert limiter.check("acct-2").allowed is True
    limiter.reset()


@pytest.mark.postgres
def test_postgres_limiter_lets_the_caller_through_once_the_window_passes(db_session):
    """The window slides: an aged-out hit stops counting."""
    from datetime import datetime, timedelta, timezone

    from services.api.models import RateLimitHit
    from services.api.ratelimit import PostgresRateLimiter

    limiter = PostgresRateLimiter("engine_eval", limit=1, window_seconds=60)
    limiter.reset()
    assert limiter.check("acct-1").allowed is True
    assert limiter.check("acct-1").allowed is False

    # Age the recorded hit past the window rather than sleeping through it.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.query(RateLimitHit).update({"hit_at": now - timedelta(seconds=120)})
    db_session.commit()

    assert limiter.check("acct-1").allowed is True
    limiter.reset()


@pytest.mark.postgres
def test_postgres_limiter_disabled_at_zero(db_session):
    """0 is the documented per-route kill switch, same as the in-memory store."""
    from services.api.ratelimit import PostgresRateLimiter

    limiter = PostgresRateLimiter("engine_eval", limit=0, window_seconds=60)
    for _ in range(10):
        assert limiter.check("acct-1").allowed is True


@pytest.mark.postgres
def test_postgres_limiter_keeps_routes_apart(db_session):
    """Each route needs its own window, exactly as the in-memory store gives.

    The in-memory store namespaces implicitly -- one dict per registry entry --
    so this is free there and easy to lose here, where every limiter writes to
    one flat table. Without the route in the key, five requests to ANY limited
    route would 429 puzzle generation, import and diagnosis at once.
    """
    from services.api.ratelimit import PostgresRateLimiter

    generous = PostgresRateLimiter("openings_baseline", limit=60, window_seconds=60)
    strict = PostgresRateLimiter("puzzles_generate", limit=5, window_seconds=60)
    generous.reset()
    strict.reset()

    # Spend more than the strict limiter's budget on the generous route.
    for _ in range(10):
        assert generous.check("acct-1").allowed is True

    # The strict route has not been used at all, so it is untouched.
    assert strict.check("acct-1").allowed is True

    generous.reset()
    strict.reset()
