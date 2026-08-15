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


def _pg(name, *, limit, window):
    from services.api.ratelimit import PostgresRateLimiter

    return PostgresRateLimiter(name, limit, window)


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


@pytest.mark.postgres
def test_concurrent_processes_cannot_exceed_the_limit(db_session):
    """The atomicity claim, tested the only way it can be: real processes.

    Every other test here is sequential, and adversarial review showed that
    removing the advisory lock — the entire design justification — left them all
    passing. Threads would not do either: the race is between transactions, and
    under READ COMMITTED neither sees the other's uncommitted hit.
    """
    import multiprocessing as mp
    import os

    url = os.environ["KNIGHTMIND_TEST_DATABASE_URL"]

    limiter = _pg("burst", limit=5, window=60)
    limiter.reset()

    ctx = mp.get_context("spawn")
    with ctx.Pool(8, initializer=_init_worker, initargs=(url,)) as pool:
        results = pool.map(_attempt, range(40))

    allowed = sum(results)
    assert allowed == 5, f"{allowed} of 40 allowed against a limit of 5"
    limiter.reset()


def _init_worker(url: str) -> None:  # pragma: no cover - subprocess helper
    import os

    os.environ["DATABASE_URL"] = url


def _attempt(_: int) -> bool:  # pragma: no cover - subprocess helper
    from services.api.ratelimit import PostgresRateLimiter

    return PostgresRateLimiter("burst", 5, 60).check("acct-burst").allowed


@pytest.mark.postgres
def test_retry_after_reflects_the_oldest_hit(db_session):
    """Pinned because a hardcoded `1` survived every previous test."""
    limiter = _pg("retry_probe", limit=1, window=60)
    limiter.reset()

    assert limiter.check("acct-1").allowed is True
    denied = limiter.check("acct-1")
    assert denied.allowed is False
    # The oldest hit is seconds old, so the wait is most of a full window --
    # not the 1s floor a broken implementation returns.
    assert 30 <= denied.retry_after <= 60, denied.retry_after
    limiter.reset()


@pytest.mark.postgres
def test_reset_touches_only_its_own_limiter(db_session):
    """`reset` used to issue an unfiltered DELETE across every limiter."""
    from services.api.models import RateLimitHit

    a = _pg("reset_a", limit=5, window=60)
    b = _pg("reset_b", limit=5, window=60)
    a.reset()
    b.reset()

    a.check("acct-1")
    b.check("acct-1")
    a.reset()

    remaining = {
        row.key
        for row in db_session.query(RateLimitHit).all()
        if row.key.startswith(("reset_a:", "reset_b:"))
    }
    assert remaining == {"reset_b:acct-1"}, remaining
    b.reset()


@pytest.mark.postgres
def test_the_housekeeping_purge_reclaims_abandoned_principals(db_session):
    """The per-key sweep never touches a principal that does not come back."""
    from datetime import datetime, timedelta, timezone

    from services.api.jobs.cleanup_sessions import purge_stale_rate_limit_hits
    from services.api.models import RateLimitHit

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6)
    db_session.add(RateLimitHit(key="engine_eval:ip:1.2.3.4", hit_at=old))
    db_session.add(RateLimitHit(key="engine_eval:ip:5.6.7.8", hit_at=old))
    db_session.commit()

    removed = purge_stale_rate_limit_hits(db_session)
    assert removed >= 2
    assert (
        db_session.query(RateLimitHit)
        .filter(RateLimitHit.key.startswith("engine_eval:ip:"))
        .count()
        == 0
    )


@pytest.mark.postgres
def test_a_broken_limiter_is_loud_and_counted(db_session, monkeypatch, caplog):
    """Fail-open is deliberate. Silent fail-open is how it stays broken.

    A typo, or a deploy landing before its migration, makes every limited route
    unlimited — measured at 200/200 allowed against a limit of 5 — while the
    logs read as a hiccup and /ops/health stays green. This pins the two things
    that make it visible: ERROR level, and a counter /ops/status can report.
    """
    import logging

    from services.api import ratelimit as rl

    limiter = _pg("broken", limit=1, window=60)
    limiter.reset()
    monkeypatch.setitem(rl.FAILURES, "count", 0)

    def explode(*_a, **_k):
        raise RuntimeError('relation "rate_limit_hits" does not exist')

    monkeypatch.setattr(limiter, "_check", explode)

    with caplog.at_level(logging.ERROR, logger="knightmind.ratelimit"):
        for _ in range(3):
            # Fails OPEN, deliberately: the limiter is not the DDoS layer.
            assert limiter.check("acct-1").allowed is True

    assert rl.FAILURES["count"] == 3
    assert "FAILED OPEN" in caplog.text
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.postgres
def test_a_held_lock_does_not_hang_the_request(db_session):
    """`SET LOCAL lock_timeout` is what stops an unbounded hang.

    pg_advisory_xact_lock does not raise while it waits, so the fail-open
    handler cannot see it: one long-lived transaction holding a key would block
    every limited request indefinitely, each pinning a threadpool token and a
    pool connection. Without the timeout this test hangs rather than fails,
    which is exactly the production symptom.
    """
    import time

    from sqlalchemy import text

    from services.api.db import SessionLocal

    limiter = _pg("held", limit=5, window=60)
    limiter.reset()
    key = "held:acct-1"

    holder = SessionLocal()
    holder.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
    try:
        started = time.monotonic()
        decision = limiter.check("acct-1")
        waited = time.monotonic() - started
        # Bounded, and fails OPEN -- the limiter is not the availability layer.
        assert decision.allowed is True
        assert waited < 10, f"waited {waited:.1f}s; lock_timeout did not apply"
    finally:
        holder.rollback()
        holder.close()
        limiter.reset()


@pytest.mark.postgres
def test_the_purge_uses_the_database_clock(db_session):
    """The sweep must compare against the clock that stamped the rows.

    `_check` inserts with SQL now(); a Python-side naive cutoff is offset by the
    session TimeZone. Measured west of UTC, a hit inserted zero seconds earlier
    was purged, handing the principal a fresh budget every hour -- the heartbeat
    bug, reintroduced in the sweep one commit later.
    """
    from sqlalchemy import text

    from services.api import db as db_module
    from services.api.jobs.cleanup_sessions import purge_stale_rate_limit_hits
    from services.api.models import RateLimitHit

    dbname = db_session.execute(text("SELECT current_database()")).scalar_one()
    db_session.execute(
        # Pacific/Kiritimati (+14), deliberately EAST of UTC. The bug only bites
        # in that direction -- west of UTC the broken SQL over-retains, so a
        # western zone lets the pre-fix version pass and proves nothing. That is
        # exactly what the first version of this test did.
        text(f"ALTER DATABASE \"{dbname}\" SET timezone TO 'Pacific/Kiritimati'")
    )
    db_session.commit()
    db_module.engine.dispose()
    try:
        limiter = _pg("tz_purge", limit=5, window=60)
        limiter.reset()
        assert limiter.check("acct-1").allowed is True

        # A FRESH session, so the purge runs under the odd timezone too. Using
        # the test's own session hid the bug: that connection was opened before
        # the ALTER and stayed on UTC, so the broken SQL compared UTC to UTC and
        # behaved. Both sides have to be on the wrong clock for it to show.
        from services.api.db import SessionLocal

        with SessionLocal() as purge_db:
            purge_stale_rate_limit_hits(purge_db)

        live = (
            db_session.query(RateLimitHit)
            .filter(RateLimitHit.key == "tz_purge:acct-1")
            .count()
        )
        assert live == 1, "the sweep deleted a hit that is inside its window"
        limiter.reset()
    finally:
        db_session.execute(text(f'ALTER DATABASE "{dbname}" RESET timezone'))
        db_session.commit()
        db_module.engine.dispose()


@pytest.mark.postgres
def test_the_recorded_failure_carries_no_connection_detail(db_session, monkeypatch):
    """What /ops/status stores must not describe the infrastructure.

    Truncating the message was not enough: a real bad-password
    OperationalError puts the host and port inside the first 120 characters,
    and the username escaped only because that prefix happens to run to 124.
    """
    from sqlalchemy.exc import OperationalError

    from services.api import ratelimit as rl

    limiter = _pg("leaky", limit=1, window=60)
    monkeypatch.setitem(rl.FAILURES, "last_error", None)

    class _Orig(Exception):
        sqlstate = "28P01"

    def explode(*_a, **_k):
        raise OperationalError(
            "SELECT 1",
            {},
            _Orig(
                'connection failed: connection to server at "172.19.0.2", '
                "port 5432 failed: FATAL:  password authentication failed "
                'for user "knightmind"'
            ),
        )

    monkeypatch.setattr(limiter, "_check", explode)
    assert limiter.check("acct-1").allowed is True

    recorded = str(rl.FAILURES["last_error"])
    for secret in ("172.19.0.2", "5432", "knightmind", "password"):
        assert secret not in recorded, f"{secret!r} leaked into {recorded!r}"
    # Still actionable: the class and the SQLSTATE say what went wrong.
    assert "OperationalError" in recorded and "28P01" in recorded


@pytest.mark.postgres
def test_the_purge_batches_and_terminates(db_session):
    """The batching loop shipped with no coverage at all.

    A single DELETE holds row locks for the whole sweep, and a live check
    deleting its own expired rows blocks on them until its 2s lock_timeout
    fires and it fails OPEN — incrementing the one counter that means "the
    limiter is broken". Committing per batch bounds that hold. The loop must
    therefore actually loop, and must terminate.
    """
    from datetime import datetime, timedelta, timezone

    from services.api.jobs.cleanup_sessions import (
        _PURGE_BATCH,
        purge_stale_rate_limit_hits,
    )
    from services.api.models import RateLimitHit

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6)
    # One more than a batch, so a non-looping implementation leaves rows behind.
    db_session.bulk_save_objects(
        [
            RateLimitHit(key=f"engine_eval:ip:10.0.{i // 256}.{i % 256}", hit_at=old)
            for i in range(_PURGE_BATCH + 1)
        ]
    )
    db_session.commit()

    removed = purge_stale_rate_limit_hits(db_session)

    assert removed >= _PURGE_BATCH + 1
    assert (
        db_session.query(RateLimitHit)
        .filter(RateLimitHit.key.startswith("engine_eval:ip:10.0."))
        .count()
        == 0
    )


@pytest.mark.postgres
def test_the_purge_leaves_a_fresh_hit_alone(db_session):
    """Termination is not enough: it must not take rows inside a live window."""
    from datetime import datetime, timedelta, timezone

    from services.api.jobs.cleanup_sessions import purge_stale_rate_limit_hits
    from services.api.models import RateLimitHit

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.add(RateLimitHit(key="engine_eval:ip:9.9.9.9", hit_at=now))
    db_session.add(
        RateLimitHit(key="engine_eval:ip:9.9.9.8", hit_at=now - timedelta(hours=6))
    )
    db_session.commit()

    purge_stale_rate_limit_hits(db_session)

    remaining = {
        row.key
        for row in db_session.query(RateLimitHit)
        .filter(RateLimitHit.key.startswith("engine_eval:ip:9.9.9."))
        .all()
    }
    assert remaining == {"engine_eval:ip:9.9.9.9"}


@pytest.mark.postgres
def test_a_no_op_purge_does_not_leave_a_transaction_open(db_session):
    """The common path on a healthy stack, and the one that used to leak.

    The DELETE opens a transaction before its rowcount can be read, so a sweep
    that returns without committing leaves that transaction idle with its
    snapshot pinned — on an hourly job over a table that is usually already
    clean, nearly every time. Nothing downstream fails loudly, which is why it
    needs a test rather than a rollback in the logs.
    """
    from services.api.jobs.cleanup_sessions import purge_stale_rate_limit_hits
    from services.api.models import RateLimitHit

    db_session.query(RateLimitHit).delete()
    db_session.commit()

    removed = purge_stale_rate_limit_hits(db_session)

    assert removed == 0
    assert db_session.in_transaction() is False
