"""Automatic rating snapshots.

Rating history must never depend on the user remembering to press a button.
Snapshots are recorded server-side whenever the platform already has a fresh
reason to look at Chess.com:

- completing a training session (with the session id attached),
- importing games,
- viewing rating insights (throttled per username).

Every call site is best-effort: a Chess.com hiccup never breaks the caller,
and unchanged ratings are deduplicated so quiet periods don't fill the
history with flat duplicate rows.
"""

import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.models import RatingSnapshot
from services.ingest import get_player_stats

logger = logging.getLogger(__name__)

# Time controls tracked automatically.
AUTO_SNAPSHOT_TIME_CONTROLS = ["rapid", "blitz", "bullet"]


def _env_seconds(name: str, default: int) -> int:
    """Read a non-negative seconds value from the environment, else default."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


# View-triggered refreshes are throttled per username so refreshing Rating
# Insights doesn't hammer the Chess.com API. 0 disables the throttle.
AUTO_SNAPSHOT_MIN_INTERVAL_SECONDS = _env_seconds(
    "RATINGS_AUTO_SNAPSHOT_MIN_INTERVAL_SECONDS", 6 * 3600
)

# Last opportunistic check per canonical username. Process-local: a restart
# just means one extra Chess.com stats call per user.
_last_checked: dict[str, float] = {}


def reset_throttle() -> None:
    """Test hook: clear the per-username throttle registry."""
    _last_checked.clear()


async def auto_snapshot(
    username: str, db: Session, *, session_id: str | None = None
) -> None:
    """Best-effort: record snapshots for all tracked time controls.

    Skips a time control when the latest stored snapshot already has the same
    rating. Never raises: failures are logged and swallowed.
    """
    try:
        stats = await get_player_stats(username)
    except Exception as e:
        logger.debug(
            "Auto-snapshot: could not fetch Chess.com stats for %s: %s", username, e
        )
        return

    now = datetime.now(timezone.utc)
    added = 0
    for tc in AUTO_SNAPSHOT_TIME_CONTROLS:
        rating = stats.get(f"chess_{tc}", {}).get("last", {}).get("rating")
        if not rating:
            continue

        latest_stmt = (
            select(RatingSnapshot)
            .where(
                RatingSnapshot.username == username,
                RatingSnapshot.time_control == tc,
            )
            .order_by(RatingSnapshot.recorded_at.desc())
            .limit(1)
        )
        latest = db.scalars(latest_stmt).first()
        if latest and latest.rating == rating:
            continue

        db.add(
            RatingSnapshot(
                username=username,
                source="chesscom",
                time_control=tc,
                rating=rating,
                recorded_at=now,
                session_id=session_id,
            )
        )
        added += 1

    if added == 0:
        return

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("Auto-snapshot: commit failed for %s: %s", username, e)


async def auto_snapshot_throttled(username: str, db: Session) -> None:
    """Opportunistic ``auto_snapshot``, at most once per throttle interval."""
    key = username.lower()
    now = time.monotonic()
    last = _last_checked.get(key)
    if last is not None and now - last < AUTO_SNAPSHOT_MIN_INTERVAL_SECONDS:
        return
    # Mark before fetching so a failing Chess.com API isn't hammered either.
    _last_checked[key] = now
    await auto_snapshot(username, db)
