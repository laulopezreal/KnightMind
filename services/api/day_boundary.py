"""
Single, documented day-boundary rule for all day-based analytics.

THE RULE
--------
Every day-based metric in KnightMind (training streaks, "today", warmup,
due-today, motif-trend day buckets) is computed on **UTC calendar days**.

Rationale:
- Canonical timestamps (``TrainingSession.completed_at``, ``PuzzleReview.reviewed_at``,
  ``RatingSnapshot.recorded_at`` …) are written with ``datetime.now(timezone.utc)``
  and stored in naive ``DateTime`` columns as UTC wall-clock values.
- ``func.date(col)`` truncates that stored value with **no timezone conversion**
  on both SQLite and Postgres, so it yields the UTC calendar day on either
  backend. Pairing it with ``utc_today()`` keeps SQL-side grouping and
  Python-side comparisons on the same boundary.

Consequence to keep in mind (documented, not a bug): the boundary is UTC, not
the user's local midnight. A user who trains at 01:00 local time in UTC+2 has
that session counted against the *previous* UTC day. If per-user local days are
ever required, that must be a deliberate, snapshot-stored timezone decision —
not an implicit ``func.date`` on a server-local clock. Until then, UTC is the
one rule and this module is the single place that encodes it.

SQLite vs Postgres note
-----------------------
``func.date()`` returns a *string* ("YYYY-MM-DD") on SQLite but a ``date``
object on Postgres. Use :func:`day_key` to normalize either into a stable
"YYYY-MM-DD" string so grouping/formatting code is backend-agnostic.
"""

from datetime import date, datetime, timezone

from sqlalchemy import func

# The one timezone all day-based analytics use.
APP_DAY_TIMEZONE = timezone.utc


def utc_today() -> date:
    """Return the current UTC calendar day (the app's day boundary)."""
    return datetime.now(APP_DAY_TIMEZONE).date()


def day_expr(column):
    """SQL day-bucket for a stored (UTC) timestamp column.

    Thin wrapper over ``func.date`` so callers go through the documented rule
    rather than sprinkling ``func.date`` ad hoc. See module docstring for the
    SQLite-vs-Postgres return-type difference (normalize with :func:`day_key`).
    """
    return func.date(column)


def day_key(value) -> str:
    """Normalize a ``func.date`` result to a stable "YYYY-MM-DD" string.

    Handles both backends: Postgres returns ``date``/``datetime`` objects while
    SQLite returns strings. Any other type is coerced with ``str``.
    """
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)
