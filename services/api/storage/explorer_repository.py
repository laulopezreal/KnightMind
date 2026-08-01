"""Persistence for opening-explorer aggregates.

The whole job of this module is to make sure the page can show a baseline on
every selection without making an outbound call on every selection. Rows are
public aggregates keyed by position and rating band, identical for every
account, so one user's lookup answers everyone else's.

A stale row is still shown. A baseline is a slow-moving statistic over millions
of games, so serving a month-old aggregate is right and serving nothing because
lichess is down is not — see :meth:`ExplorerRepository.get_fresh` and the
fallback in :meth:`stale_entry`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.models import OpeningExplorerCache
from services.api.openings.explorer import CACHE_TTL_DAYS, ExplorerStats


@dataclass(frozen=True)
class CachedStats:
    stats: ExplorerStats
    fetched_at: datetime


def _aware(moment: datetime) -> datetime:
    """Read a stored timestamp as UTC.

    SQLite hands back naive datetimes, Postgres may not. Comparing a naive one
    to an aware `now` raises, which would turn a cache read into a 500 on one
    backend and not the other.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class ExplorerRepository:
    def __init__(self, db: Session):
        self.db = db

    def _row(self, key: str) -> OpeningExplorerCache | None:
        return self.db.execute(
            select(OpeningExplorerCache).where(OpeningExplorerCache.key == key)
        ).scalar_one_or_none()

    def get(self, key: str) -> CachedStats | None:
        """Whatever is stored, fresh or not."""
        row = self._row(key)
        if row is None:
            return None
        return CachedStats(
            stats=ExplorerStats(white=row.white, draws=row.draws, black=row.black),
            fetched_at=_aware(row.fetched_at),
        )

    def get_fresh(self, key: str) -> ExplorerStats | None:
        """A row young enough to serve without asking upstream again."""
        cached = self.get(key)
        if cached is None:
            return None
        age = datetime.now(timezone.utc) - cached.fetched_at
        if age > timedelta(days=CACHE_TTL_DAYS):
            return None
        return cached.stats

    def put(self, key: str, epd: str, stats: ExplorerStats) -> None:
        """Store a fetched aggregate, replacing any older row for the key.

        Upsert by hand rather than by dialect: this runs on both SQLite (dev,
        tests) and Postgres (production), and the two spell ON CONFLICT
        differently. The row is a cache, so a lost race just refetches.
        """
        row = self._row(key)
        if row is None:
            self.db.add(
                OpeningExplorerCache(
                    key=key,
                    epd=epd,
                    white=stats.white,
                    draws=stats.draws,
                    black=stats.black,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        else:
            row.white = stats.white
            row.draws = stats.draws
            row.black = stats.black
            row.fetched_at = datetime.now(timezone.utc)
        self.db.commit()
