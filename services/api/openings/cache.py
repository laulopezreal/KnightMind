"""
Process-local cache for built opening trees.

Building a tree re-parses every stored PGN for a user — O(games x plies) per
request — and the frontend refetches on mount and on every colour-filter
change, so flipping between "all games" and "as White" used to re-parse the
whole archive each time.

The key folds in the two cheap signals that change whenever the underlying
games do (`game_count` and the latest game's timestamp), so an import
invalidates affected entries by construction; there is no invalidation call to
forget. `SCHEME_VERSION` covers changes to the tree's own shape.

Deliberately in-process rather than a table like `FenEvalCache`: an opening
tree is derived data that costs a second to rebuild, so it does not warrant a
migration or the write traffic. With multiple workers each holds its own copy,
which trades a little duplicated work for zero coordination.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Bump when the serialized tree's shape changes, so old entries cannot be
# served against new client expectations.
SCHEME_VERSION = "v2"  # v2: nodes gained eco/opening_name; key gained min_games

# Trees are the largest thing held here; a few dozen covers a single user
# flipping filters plus a handful of concurrent users without unbounded growth.
DEFAULT_MAX_ENTRIES = 32

CacheKey = tuple[str, str, int, int, int, str, str]


def make_key(
    *,
    username: str,
    color: str,
    max_ply: int,
    game_count: int,
    latest_game_time: datetime | None,
    min_games: int = 1,
) -> CacheKey:
    """
    Build a self-invalidating key.

    `game_count` alone is not enough: re-importing can replace games without
    changing the count. Pairing it with the newest game's timestamp catches
    both growth and replacement.
    """
    return (
        SCHEME_VERSION,
        username.strip().lower(),
        max_ply,
        min_games,
        game_count,
        latest_game_time.isoformat() if latest_game_time else "none",
        color,
    )


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def lookups(self) -> int:
        return self.hits + self.misses

    def to_dict(self) -> dict[str, int | float]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "lookups": self.lookups,
            "hit_rate": round(self.hits / self.lookups, 3) if self.lookups else 0.0,
        }


class OpeningTreeCache:
    """
    Bounded, thread-safe LRU.

    Sync FastAPI endpoints run in a threadpool, so concurrent requests can hit
    this from several threads at once; every mutation is taken under the lock.

    Stored values are handed back by reference rather than copied — deep-copying
    a large tree on every hit would undo the saving. Callers must therefore
    treat a returned tree as read-only, which is why the endpoint finishes
    composing the response (including `analysis`) *before* storing it.
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._entries: OrderedDict[CacheKey, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def get(self, key: CacheKey) -> dict[str, Any] | None:
        with self._lock:
            if key not in self._entries:
                self.stats.misses += 1
                return None
            self._entries.move_to_end(key)
            self.stats.hits += 1
            return self._entries[key]

    def put(self, key: CacheKey, value: dict[str, Any]) -> None:
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            self._entries[key] = value
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self.stats.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.stats = CacheStats()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# Module-level instance shared by the endpoint.
tree_cache = OpeningTreeCache()
