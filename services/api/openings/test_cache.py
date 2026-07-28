"""Tests for the opening-tree cache."""

from datetime import datetime, timedelta

import pytest

from .cache import SCHEME_VERSION, OpeningTreeCache, make_key

NOW = datetime(2026, 7, 27, 12, 0, 0)


def key(**overrides):
    base = {
        "username": "alice",
        "color": "both",
        "max_ply": 12,
        "game_count": 10,
        "latest_game_time": NOW,
    }
    return make_key(**{**base, **overrides})


class TestKeyInvalidation:
    """The key must change whenever the games behind the tree could have."""

    def test_same_inputs_produce_the_same_key(self):
        assert key() == key()

    def test_new_game_changes_the_key(self):
        assert key(game_count=11) != key()

    def test_reimport_without_a_count_change_still_changes_the_key(self):
        # Re-importing can replace games without changing how many there are,
        # so the count alone would serve a stale tree.
        assert key(latest_game_time=NOW + timedelta(minutes=1)) != key()

    def test_each_colour_filter_is_cached_separately(self):
        assert key(color="white") != key(color="black") != key(color="both")

    def test_max_ply_is_part_of_the_key(self):
        assert key(max_ply=20) != key()

    def test_username_is_normalised(self):
        assert key(username="  ALICE ") == key(username="alice")

    def test_users_do_not_share_entries(self):
        assert key(username="bob") != key(username="alice")

    def test_absent_timestamp_is_representable(self):
        assert key(latest_game_time=None) != key()

    def test_scheme_version_is_in_the_key(self):
        assert SCHEME_VERSION in key()


class TestCacheBehaviour:
    def test_returns_none_before_anything_is_stored(self):
        cache = OpeningTreeCache()
        assert cache.get(key()) is None
        assert cache.stats.misses == 1

    def test_round_trips_a_tree(self):
        cache = OpeningTreeCache()
        tree = {"move_san": "Start", "games_count": 10}
        cache.put(key(), tree)

        assert cache.get(key()) == tree
        assert cache.stats.hits == 1

    def test_a_newer_import_misses(self):
        cache = OpeningTreeCache()
        cache.put(key(), {"games_count": 10})

        assert cache.get(key(game_count=11)) is None

    def test_evicts_least_recently_used(self):
        cache = OpeningTreeCache(max_entries=2)
        cache.put(key(game_count=1), {"n": 1})
        cache.put(key(game_count=2), {"n": 2})
        # Touch the oldest so the *other* one becomes the eviction candidate.
        cache.get(key(game_count=1))
        cache.put(key(game_count=3), {"n": 3})

        assert cache.get(key(game_count=1)) == {"n": 1}
        assert cache.get(key(game_count=2)) is None
        assert cache.stats.evictions == 1

    def test_never_grows_past_its_bound(self):
        cache = OpeningTreeCache(max_entries=3)
        for i in range(20):
            cache.put(key(game_count=i), {"n": i})

        assert len(cache) == 3

    def test_overwriting_a_key_does_not_grow_the_cache(self):
        cache = OpeningTreeCache(max_entries=3)
        cache.put(key(), {"n": 1})
        cache.put(key(), {"n": 2})

        assert len(cache) == 1
        assert cache.get(key()) == {"n": 2}

    def test_clear_resets_entries_and_stats(self):
        cache = OpeningTreeCache()
        cache.put(key(), {"n": 1})
        cache.get(key())
        cache.clear()

        assert len(cache) == 0
        assert cache.stats.hits == 0
        assert cache.get(key()) is None

    def test_rejects_a_useless_bound(self):
        with pytest.raises(ValueError):
            OpeningTreeCache(max_entries=0)

    def test_reports_hit_rate(self):
        cache = OpeningTreeCache()
        cache.put(key(), {"n": 1})
        cache.get(key())
        cache.get(key(game_count=99))

        assert cache.stats.to_dict()["hit_rate"] == 0.5

    def test_hit_rate_is_zero_before_any_lookup(self):
        assert OpeningTreeCache().stats.to_dict()["hit_rate"] == 0.0


class TestThreadSafety:
    def test_concurrent_writers_leave_the_cache_bounded_and_consistent(self):
        import threading

        cache = OpeningTreeCache(max_entries=8)
        errors: list[BaseException] = []

        def hammer(offset: int) -> None:
            try:
                for i in range(200):
                    k = key(game_count=offset * 1000 + i)
                    cache.put(k, {"n": i})
                    cache.get(k)
            except BaseException as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(cache) <= 8
