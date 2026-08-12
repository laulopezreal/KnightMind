"""In-process, per-principal rate limiting for expensive public routes.

Audit gate 10 (public-API abuse resistance). The expensive routes
(``/engine/eval``, ``/puzzles/generate``, ``/import/chesscom``,
``/ratings/snapshot``) are gated behind ``require_account`` when auth is enabled
(#198) and ``/engine/eval`` already has a per-process in-flight cap (#191), but
nothing stops a *single* principal from hammering them: one account (or one IP
when auth is off) can issue unbounded requests. This module adds the missing
rate-limit layer.

Algorithm — sliding-window log
------------------------------
Each principal key maps to a deque of request timestamps. On each check we drop
timestamps older than ``window`` seconds, then either reject (if the window is
already full) or record the new hit. This gives an exact ``Retry-After``: the
time until the oldest in-window hit ages out. Compared to a fixed-window
counter it has no burst-at-the-boundary doubling; compared to a token bucket it
yields a precise retry hint without extra state.

Key
---
``acct:<id>`` when an authenticated account is present, else ``ip:<addr>``. The
client address trusts only the *immediate* proxy: on the production deploy the
public Caddy is the sole reverse proxy and appends the real client to
``X-Forwarded-For``, so we take the right-most XFF entry (the one our trusted
proxy vouches for). Left-most entries are client-supplied and therefore
spoofable, so we never trust them. With no XFF header we fall back to the
socket peer.

Thread-safety
-------------
All mutation of the shared state happens under a ``threading.Lock``. The check
is O(1) amortized and non-blocking, so calling it from an async route on the
event loop is safe.

Clock injection
---------------
The time source is an injectable callable (default ``time.monotonic``) so tests
are fully deterministic and never sleep. ``time.monotonic`` — not wall-clock —
so limits are immune to NTP steps and clock changes.

Multi-worker caveat
-------------------
This store lives in one process. It is correct for the current single-worker
deployment (uvicorn with one worker). A multi-worker / multi-replica deploy
would give each worker its own window, multiplying the effective limit by the
worker count; that setup needs a shared store (e.g. Redis) instead. Per-IP DDoS
absorption belongs at the ingress (Caddy), not here — this layer defends
against a single well-behaved-TCP principal abusing an expensive endpoint.
"""

import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Union

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from services.api.db import SessionLocal
from services.api.envutil import env_int
from services.api.identity import require_account
from services.api.models import Account, RateLimitHit

logger = logging.getLogger("knightmind.ratelimit")

# Fail-open is deliberate; SILENT fail-open is not. Surfaced on /ops/status so
# a limiter that has been off since the last deploy is answerable, not guessed.
FAILURES: dict[str, Any] = {"count": 0, "last_error": None}

# Both stores answer `check(key) -> RateLimitDecision`; nothing else is used.
Limiter = Union["RateLimiter", "PostgresRateLimiter"]

_TimeFn = Callable[[], float]

# When the key table grows past this many principals we sweep out keys whose
# newest hit has already aged out of the window, bounding memory under a churn
# of distinct IPs. A generous cap so the sweep is rare in normal operation.
_SWEEP_THRESHOLD = 4096


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int  # whole seconds until the caller may retry (>=1 when denied)


class RateLimiter:
    """Thread-safe sliding-window-log limiter with an injectable clock.

    ``limit <= 0`` disables the limiter (every request is allowed), which makes
    an env value of ``0`` a clean per-route kill switch.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        time_fn: _TimeFn | None = None,
    ) -> None:
        self.limit = limit
        self.window_seconds = float(window_seconds)
        # Public so tests can swap in a deterministic clock.
        self.time_fn: _TimeFn = time_fn or time.monotonic
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> RateLimitDecision:
        """Record a hit for ``key`` and decide whether it is allowed."""
        if self.limit <= 0:
            return RateLimitDecision(allowed=True, retry_after=0)

        now = self.time_fn()
        cutoff = now - self.window_seconds
        with self._lock:
            if len(self._hits) > _SWEEP_THRESHOLD:
                self._sweep(cutoff)

            dq = self._hits.get(key)
            if dq is None:
                dq = deque()
                self._hits[key] = dq

            while dq and dq[0] <= cutoff:
                dq.popleft()

            if len(dq) >= self.limit:
                # Oldest hit ages out at dq[0] + window; retry after that.
                retry_after = math.ceil(dq[0] + self.window_seconds - now)
                return RateLimitDecision(allowed=False, retry_after=max(retry_after, 1))

            dq.append(now)
            return RateLimitDecision(allowed=True, retry_after=0)

    def reset(self) -> None:
        """Drop all recorded state. For tests and hot-reconfiguration."""
        with self._lock:
            self._hits.clear()

    def _sweep(self, cutoff: float) -> None:
        """Remove keys whose most recent hit has aged out. Caller holds lock."""
        stale = [k for k, dq in self._hits.items() if not dq or dq[-1] <= cutoff]
        for k in stale:
            del self._hits[k]


class PostgresRateLimiter:
    """Sliding-window-log limiter whose state is shared by every process.

    Same contract as :class:`RateLimiter` -- ``check(key)`` records a hit and
    decides -- so the two are interchangeable and the route code is unchanged.

    Why not one atomic upsert into a per-window counter: that is a FIXED window,
    which lets a caller spend the whole limit at the end of one window and again
    at the start of the next. Double the intended rate, exactly at the burst
    these routes exist to stop. Matching the in-process limiter's semantics is
    worth three statements at 5-60 requests a minute.

    The three statements need to be atomic against each other, or two workers
    counting concurrently both see room and both insert. A transaction alone
    does not give that -- under READ COMMITTED neither sees the other's
    uncommitted hit -- so the whole check runs under a per-key transactional
    advisory lock. It is released at commit or rollback, with no unlock path to
    leak, and it serialises only the principals colliding on one key.

    Wall clock, not ``time.monotonic``: monotonic is only comparable within one
    process, and this state crosses processes by construction.
    """

    def __init__(self, name: str, limit: int, window_seconds: float) -> None:
        # The limiter NAME is part of the stored key. The in-memory store gets
        # this for free -- one `_hits` dict per registry entry -- but here every
        # limiter writes to one flat table, so without the prefix all six share
        # a single window: five requests to any limited route would 429 puzzle
        # generation, import and diagnosis. The two stores must namespace the
        # same way or they are not interchangeable.
        self.name = name
        self.limit = limit
        self.window_seconds = float(window_seconds)

    def _scoped(self, key: str) -> str:
        return f"{self.name}:{key}"

    def check(self, key: str) -> RateLimitDecision:
        if self.limit <= 0:
            return RateLimitDecision(allowed=True, retry_after=0)

        with SessionLocal() as db:
            try:
                return self._check(db, self._scoped(key))
            except Exception as exc:
                db.rollback()
                # Fail OPEN, deliberately. This limiter protects expensive
                # routes from a well-behaved-TCP principal; it is not the DDoS
                # layer (that is the ingress). A database blip must not turn
                # every limited endpoint into a 429 -- the outage would be
                # larger than the abuse it prevents.
                # ERROR, not warning: this is the security control switching
                # itself off. A typo, or a deploy landing before its migration,
                # makes every limited route unlimited while the logs read as a
                # hiccup and /ops/health stays green. The counter is what makes
                # "it has been off for an hour" answerable -- see /ops/status.
                FAILURES["count"] += 1
                FAILURES["last_error"] = repr(exc)
                logger.error(
                    "Rate limit check FAILED OPEN (%s total); limiting is NOT in "
                    "effect for this request",
                    FAILURES["count"],
                    exc_info=True,
                )
                return RateLimitDecision(allowed=True, retry_after=0)

    def _check(self, db: Session, key: str) -> RateLimitDecision:
        # Every time value comes from the SERVER, not this process. The Python
        # clock is per-host, which is the property a shared store exists to
        # remove: a host 90s fast sweeps away another's live window, so the
        # principal gets a second full budget; one 90s slow reads future-dated
        # rows and hands a legitimate client a Retry-After well past the window.
        window = f"{self.window_seconds} seconds"

        # Bounded wait. pg_advisory_xact_lock does NOT raise while it waits, so
        # the fail-open below cannot see it: one long-lived transaction holding
        # this key would hang every limited request indefinitely, each pinning a
        # threadpool token and a pool connection -- a worse outage than the 429s
        # fail-open was written to avoid. LOCAL, so it reverts with the
        # transaction rather than leaking into the next use of this connection.
        db.execute(text("SET LOCAL lock_timeout = '2s'"))
        # A collision in hashtext() merges two keys onto one lock: a slowdown,
        # not a cycle. Each transaction takes exactly one advisory lock and then
        # touches only that key's rows, so there is no second lock to invert.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
        db.execute(
            text(
                "DELETE FROM rate_limit_hits "
                "WHERE key = :k AND hit_at <= now() - CAST(:w AS interval)"
            ),
            {"k": key, "w": window},
        )
        count, retry_after = db.execute(
            text(
                "SELECT count(*), "
                "       CEIL(EXTRACT(EPOCH FROM "
                "            (min(hit_at) + CAST(:w AS interval)) - now())) "
                "FROM rate_limit_hits "
                "WHERE key = :k AND hit_at > now() - CAST(:w AS interval)"
            ),
            {"k": key, "w": window},
        ).one()

        if count >= self.limit:
            db.commit()
            return RateLimitDecision(
                allowed=False, retry_after=max(int(retry_after or 1), 1)
            )

        db.execute(
            text("INSERT INTO rate_limit_hits (key, hit_at) VALUES (:k, now())"),
            {"k": key},
        )
        db.commit()
        return RateLimitDecision(allowed=True, retry_after=0)

    def reset(self) -> None:
        """Drop this limiter's recorded state. For tests and hot-reconfiguration."""
        with SessionLocal() as db:
            db.execute(
                delete(RateLimitHit).where(RateLimitHit.key.startswith(f"{self.name}:"))
            )
            db.commit()


# --- Registry ---------------------------------------------------------------
# One named limiter per route, created lazily from env on first use so config is
# read once at startup and tests can reach a limiter by name to inject a clock.

_LIMITERS: dict[str, Limiter] = {}
_REGISTRY_LOCK = threading.Lock()


def get_limiter(
    name: str,
    *,
    default_limit: int,
    default_window: float = 60.0,
) -> Limiter:
    """Return the named limiter, creating it from env config on first request.

    Env overrides (read once, at creation):
      ``RATE_LIMIT_<NAME>``         -> max requests per window (0 disables)
      ``RATE_LIMIT_<NAME>_WINDOW``  -> window length in seconds
    where ``<NAME>`` is ``name`` upper-cased.

    ``KNIGHTMIND_RATE_LIMIT_STORE`` picks the backing store: ``memory`` (default,
    and correct only while the API runs one process) or ``postgres`` (shared, and
    what makes more than one safe). Defaulting to memory keeps the unit tests
    free of a database round trip per check; the deployed API sets postgres.
    """
    with _REGISTRY_LOCK:
        limiter = _LIMITERS.get(name)
        if limiter is None:
            upper = name.upper()
            # No min_value: 0 (or any int) is meaningful here — 0 disables.
            limit = env_int(f"RATE_LIMIT_{upper}", default_limit)
            window = float(env_int(f"RATE_LIMIT_{upper}_WINDOW", int(default_window)))
            if os.environ.get("KNIGHTMIND_RATE_LIMIT_STORE") == "postgres":
                limiter = PostgresRateLimiter(name, limit, window)
            else:
                limiter = RateLimiter(limit, window)
            _LIMITERS[name] = limiter
        return limiter


def reset_limiters() -> None:
    """Clear the whole registry. Test hook so limiters re-read env / start empty."""
    with _REGISTRY_LOCK:
        _LIMITERS.clear()


def client_ip(request: Request) -> str:
    """Best-effort client address, trusting only the immediate proxy.

    Takes the right-most ``X-Forwarded-For`` entry (appended by our trusted
    Caddy); left-most entries are client-controlled and ignored. Falls back to
    the socket peer when no XFF header is present.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def principal_key(request: Request, account: Account | None) -> str:
    """Rate-limit identity: account id when authenticated, else client IP."""
    if account is not None:
        return f"acct:{account.id}"
    return f"ip:{client_ip(request)}"


def rate_limit(
    name: str,
    *,
    default_limit: int,
    default_window: float = 60.0,
) -> Callable[..., None]:
    """Build a FastAPI dependency that enforces the named per-principal limit.

    Depends on ``require_account`` so the key is the authenticated account when
    auth is on (and so an unauthenticated 401 is raised *before* we record a
    hit); when auth is off the account is ``None`` and we key by client IP.
    Raises 429 with a ``Retry-After`` header when the window is full.
    """

    def dependency(
        request: Request,
        account: Account | None = Depends(require_account),
    ) -> None:
        limiter = get_limiter(
            name, default_limit=default_limit, default_window=default_window
        )
        decision = limiter.check(principal_key(request, account))
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded; retry later.",
                headers={"Retry-After": str(decision.retry_after)},
            )

    return dependency
