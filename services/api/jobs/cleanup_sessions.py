"""Hourly housekeeping run from the app lifespan.

Username note: neither sweep is user-scoped — ``cleanup_abandoned_sessions``
predicates on ``completed_at``/``created_at`` and ``purge_expired_ai_audit`` on
the retention window. There is deliberately no username fold here because there
is no username: adding one would be a scope change, not a correctness fix. If a
per-user sweep is ever added it must fold with ``canonical_username`` like every
other storage entry point (see ``services.api.usernames``).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import CursorResult, text, update
from sqlalchemy.orm import Session

from services.api.db import SessionLocal
from services.api.envutil import env_int
from services.api.models import TrainingSession
from services.api.storage.ai_audit_repository import AIAuditRepository

# Hourly. Low-frequency housekeeping; the interval is not load-bearing.
CLEANUP_INTERVAL_SECONDS = 3600


def cleanup_abandoned_sessions(db: Session, hours_threshold: int = 24) -> int:
    """
    Auto-complete sessions that have been abandoned (not completed after threshold).

    Args:
        db: Database session
        hours_threshold: Number of hours after which a session is considered abandoned

    Returns:
        Number of sessions auto-completed
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours_threshold)

    # Use bulk update for efficiency
    stmt = (
        update(TrainingSession)
        .where(
            TrainingSession.completed_at.is_(None),
            TrainingSession.created_at < cutoff_time,
        )
        .values(completed_at=datetime.now(timezone.utc))
    )

    result = db.execute(stmt)
    count = cast(CursorResult, result).rowcount

    if count > 0:
        db.commit()
        print(f"Auto-completed {count} abandoned session(s)")

    return count


def purge_expired_ai_audit(db: Session) -> int:
    """Drop AI diagnosis audit rows past the retention window.

    Rides the existing cleanup loop rather than introducing a scheduler: that
    loop already runs hourly from the app lifespan, and a retention sweep is
    exactly the kind of low-frequency housekeeping it exists for.

    Retention is deliberate, not incidental — prompts and responses are kept
    long enough to investigate an incident and no longer.
    """
    removed = AIAuditRepository(db).purge_older_than()
    if removed:
        db.commit()
        print(f"Purged {removed} expired AI audit row(s)")
    return removed


def _longest_configured_window() -> float:
    """The largest window any limiter is actually configured with.

    RATE_LIMIT_<NAME>_WINDOW is a supported env override, so "an hour is longer
    than the longest window" is only true of the defaults. A window above the
    retention would have this sweep delete LIVE hits every hour, silently
    resetting that limiter.
    """
    from services.api.ratelimit import DEFAULT_WINDOW_SECONDS, KNOWN_LIMITERS

    return max(
        (
            float(env_int(f"RATE_LIMIT_{name.upper()}_WINDOW", DEFAULT_WINDOW_SECONDS))
            for name in KNOWN_LIMITERS
        ),
        default=float(DEFAULT_WINDOW_SECONDS),
    )


# Rows per purge statement. Small enough that each transaction is short, large
# enough that a year's backlog is a few hundred round trips rather than a
# million.
_PURGE_BATCH = 5000


def purge_stale_rate_limit_hits(db: Session, keep_seconds: int | None = None) -> int:
    """Drop rate-limit rows no live window can still reference.

    The limiter sweeps only the key it is currently checking, so a principal
    that hits once and never returns leaves its rows forever. Every limited
    route is unauthenticated and internet-facing, so "distinct principals ever
    seen" is the growth term, not "live traffic": at 5,000 new one-off IPs a day
    that is ~1.8M rows and ~287MB after a year.

    It is not only disk. Autovacuum's threshold scales with live tuples, so the
    permanent garbage makes vacuuming the real churn progressively lazier, and
    the index-only scan the query plan wants degrades to heap fetches.

    An hour is far longer than the longest window (60s), so this can only ever
    remove rows that are already outside every window.
    """
    # Compared in SQL, against the same clock that stamped the rows. `_check`
    # inserts hit_at with now(), a timestamptz cast into a naive column through
    # the session TimeZone -- so a Python-side naive cutoff is offset by that
    # timezone. Measured west of UTC: a hit inserted ZERO seconds earlier was
    # purged, handing the principal a fresh budget every hour. This is the same
    # bug the heartbeat had, reintroduced one commit later in the sweep.
    if keep_seconds is None:
        # Ten windows of slack past the widest one actually configured.
        keep_seconds = max(3600, int(_longest_configured_window() * 10))
    # Batched, and SKIP LOCKED — in that order of importance.
    #
    # A single DELETE holds row locks for the whole sweep, and a live `_check`
    # deleting its own expired rows then blocks on them until its 2s
    # lock_timeout fires and it fails OPEN, incrementing rate_limit_failures.
    # That counter is the only signal a broken limiter has, so housekeeping was
    # manufacturing the alarm it exists to raise.
    #
    # **The per-batch commit is what fixes that**, by bounding the hold to one
    # batch (~10ms measured) instead of the whole backlog. SKIP LOCKED protects
    # the other direction: the purge steps over rows a checker is holding rather
    # than waiting on them. Both matter, but only the first was the reported
    # symptom — an earlier version of this comment credited SKIP LOCKED for it,
    # and a probe holding rows against a live checker showed the timeout still
    # firing. Skipped rows are collected by the next pass; nothing is stranded.
    #
    # Batching also bounds one sweep over a large backlog: the DELETE is an
    # unindexed scan (there is no standalone hit_at index, only (key, hit_at)),
    # and the backlog grows with however long the worker was down.
    removed = 0
    while True:
        result = db.execute(
            text(
                "DELETE FROM rate_limit_hits WHERE ctid IN ("
                "  SELECT ctid FROM rate_limit_hits"
                "   WHERE hit_at < (now() AT TIME ZONE 'UTC') - CAST(:keep AS interval)"
                "   FOR UPDATE SKIP LOCKED"
                "   LIMIT :batch"
                ")"
            ),
            {"keep": f"{keep_seconds} seconds", "batch": _PURGE_BATCH},
        )
        batch = cast(CursorResult, result).rowcount
        # Unconditionally, including on a no-op sweep.
        #
        # Gating this on `if batch` looks tidier — why commit when nothing was
        # deleted? — but the DELETE has already opened a transaction by the time
        # its rowcount is read, so skipping the commit returns with that
        # transaction idle and its snapshot pinned. On a quiet stack the no-op
        # IS the common path, so the tidier version holds a snapshot open on
        # almost every sweep.
        #
        # The objection it was meant to answer — that committing a
        # caller-supplied session is a contract change — does not hold: this
        # function commits once per batch by design, so it already owns the
        # transaction boundary on any sweep that finds work. A no-op is the one
        # case where it would pretend otherwise. `db.rollback()` would end the
        # transaction too, but it would also discard whatever the caller had
        # pending, which is the worse of the two ways to be surprising.
        db.commit()
        removed += batch
        if batch < _PURGE_BATCH:
            break
    if removed:
        print(f"Purged {removed} stale rate-limit row(s)")
    return removed


def purge_dead_worker_heartbeats(db: Session, keep_seconds: int = 7 * 86400) -> int:
    """Drop heartbeat rows for workers that will never beat again.

    `worker_id` defaults to the container hostname, which changes on every
    recreate, so each deploy leaves its predecessor's row behind forever. Health
    reads max(beat_at), so this is a tidiness problem rather than a correctness
    one -- but this PR added the table and extended this loop, and leaving the
    one table it introduced unswept is how the next one gets forgotten too.

    A week is far past any staleness threshold, so a row this old cannot belong
    to a worker anyone is waiting on.
    """
    result = db.execute(
        text(
            "DELETE FROM worker_heartbeats "
            "WHERE beat_at < (now() AT TIME ZONE 'UTC') - CAST(:keep AS interval)"
        ),
        {"keep": f"{keep_seconds} seconds"},
    )
    removed = cast(CursorResult, result).rowcount
    if removed:
        db.commit()
        print(f"Purged {removed} dead worker heartbeat row(s)")
    return removed


async def run_session_cleanup():
    """Background task for periodic housekeeping.

    Session cleanup and AI-audit retention run in separate try blocks on
    purpose: a failure in one must not skip the other, and a stalled retention
    sweep would let prompt/response blobs accumulate past their window
    silently.
    """
    while True:
        try:
            # Run cleanup
            with SessionLocal() as db:
                await asyncio.to_thread(cleanup_abandoned_sessions, db)
        except Exception as e:
            print(f"Error in session cleanup: {e}")

        try:
            with SessionLocal() as db:
                await asyncio.to_thread(purge_expired_ai_audit, db)
        except Exception as e:
            print(f"Error in AI audit purge: {e}")

        try:
            with SessionLocal() as db:
                await asyncio.to_thread(purge_stale_rate_limit_hits, db)
        except Exception as e:
            print(f"Error in rate-limit purge: {e}")

        try:
            with SessionLocal() as db:
                await asyncio.to_thread(purge_dead_worker_heartbeats, db)
        except Exception as e:
            print(f"Error in worker heartbeat purge: {e}")

        # Crash recovery, on the clock rather than only at startup.
        #
        # cleanup_stuck_jobs ran once, before the job loop, and nothing called
        # it again — so a job orphaned in RUNNING was reclaimed only if the
        # worker process restarted. A worker that stays up (the normal case)
        # left it there forever: /ops/status showed it as active_job
        # indefinitely, and until the lease-aware fix in ops.py it also masked
        # the queue-stall check. Recovery belongs on the same clock as every
        # other sweep.
        try:
            from services.api.worker import worker

            await worker.cleanup_stuck_jobs()
        except Exception as e:
            print(f"Error in stuck-job recovery: {e}")

        # Sleep for defined interval
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
