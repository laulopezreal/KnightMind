"""Cache behaviour for opening-explorer aggregates."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from services.api.models import OpeningExplorerCache
from services.api.openings.explorer import CACHE_TTL_DAYS, ExplorerStats
from services.api.storage.explorer_repository import ExplorerRepository

STATS = ExplorerStats(white=600, draws=200, black=200)


@pytest.fixture
def sessions(db_engine):
    """Two sessions on one database, so a real write race can be staged."""
    factory = sessionmaker(bind=db_engine, autoflush=False)
    first, second = factory(), factory()
    try:
        yield first, second
    finally:
        first.close()
        second.close()


class TestRoundTrip:
    def test_stores_and_reads_back(self, sessions):
        db, _ = sessions
        repo = ExplorerRepository(db)

        repo.put("k", "epd", STATS)

        assert repo.get_fresh("k") == STATS

    def test_a_miss_is_a_miss(self, sessions):
        db, _ = sessions

        assert ExplorerRepository(db).get_fresh("never-seen") is None

    def test_a_second_write_updates_rather_than_duplicates(self, sessions):
        db, _ = sessions
        repo = ExplorerRepository(db)
        repo.put("k", "epd", STATS)

        repo.put("k", "epd", ExplorerStats(white=1, draws=2, black=3))

        assert db.query(OpeningExplorerCache).count() == 1
        assert repo.get_fresh("k") == ExplorerStats(white=1, draws=2, black=3)


class TestFreshness:
    def stale_row(self, db, days: int) -> None:
        ExplorerRepository(db).put("k", "epd", STATS)
        row = db.query(OpeningExplorerCache).one()
        row.fetched_at = datetime.now(timezone.utc) - timedelta(days=days)
        db.commit()

    def test_an_old_row_is_not_served_as_fresh(self, sessions):
        db, _ = sessions
        self.stale_row(db, CACHE_TTL_DAYS + 1)

        assert ExplorerRepository(db).get_fresh("k") is None

    def test_but_it_is_still_there_to_fall_back_on(self, sessions):
        # The whole reason `get` and `get_fresh` are separate: an expired row
        # is what stands between a lichess outage and a blank comparison.
        db, _ = sessions
        self.stale_row(db, CACHE_TTL_DAYS + 1)

        cached = ExplorerRepository(db).get("k")
        assert cached is not None
        assert cached.stats == STATS

    def test_a_row_inside_the_ttl_is_fresh(self, sessions):
        db, _ = sessions
        self.stale_row(db, CACHE_TTL_DAYS - 1)

        assert ExplorerRepository(db).get_fresh("k") == STATS

    def test_a_naive_stored_timestamp_does_not_blow_up_the_read(self, sessions):
        # SQLite hands back naive datetimes and Postgres may not. Comparing a
        # naive one against an aware `now` raises, which would turn a cache
        # read into a 500 on one backend and not the other.
        db, _ = sessions
        ExplorerRepository(db).put("k", "epd", STATS)
        row = db.query(OpeningExplorerCache).one()
        row.fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        assert ExplorerRepository(db).get_fresh("k") == STATS


class TestLosingTheWriteRace:
    def test_a_colliding_insert_is_not_an_error(self, sessions, monkeypatch):
        """Two requests miss the same key, and the loser must not 500.

        Reachable whenever two lookups for one position overlap: two tabs, two
        users first opening a popular line, or one person clicking through
        lines that transpose. The read-then-write in `put` is not atomic, so
        the loser's INSERT hits a primary key that appeared underneath it.
        """
        winner, loser = sessions
        ExplorerRepository(winner).put("k", "epd", STATS)

        # The loser read before the winner committed, so its own check still
        # says the key is absent.
        repo = ExplorerRepository(loser)
        monkeypatch.setattr(repo, "_row", lambda key: None)

        repo.put("k", "epd", ExplorerStats(white=1, draws=1, black=1))

        # The winner's row stands, and it holds the same public aggregate the
        # loser was about to write, so nothing was lost.
        assert ExplorerRepository(winner).get_fresh("k") == STATS

    def test_the_session_is_usable_afterwards(self, sessions, monkeypatch):
        # A failed commit leaves the session in a broken transaction until it
        # is rolled back; without that, every later query on this request dies.
        winner, loser = sessions
        ExplorerRepository(winner).put("k", "epd", STATS)
        repo = ExplorerRepository(loser)
        monkeypatch.setattr(repo, "_row", lambda key: None)
        repo.put("k", "epd", STATS)

        monkeypatch.undo()
        assert repo.get_fresh("k") == STATS
