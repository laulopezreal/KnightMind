# API Performance Benchmarks

A deterministic, reproducible benchmark harness for the KnightMind API's hot
paths. It exists to give SCORECARD dim 26 a repeatable baseline: same seed, same
fixtures, same mocked engine → the same work every run, on any machine.

- **Harness code:** `services/api/benchmarks/`
- **Smoke test:** `services/api/benchmarks/test_benchmarks_smoke.py`
- **Nothing production:** every input is synthetic, generated from a fixed seed.
  No production data, no network, no real Stockfish binary is ever touched.

## What is benchmarked

| Benchmark | Code under test | Needs DB? | Engine |
| --- | --- | --- | --- |
| `opening_tree.build max_ply={6,12,20}` | `openings/tree_builder.py:build_opening_tree` | no (pure CPU) | n/a |
| `engine_cache.get_or_compute (cold/miss)` | `engine/stockfish.py:get_or_compute_eval` | yes | mocked compute |
| `engine_cache.get_or_compute (warm/hit)` | `engine/stockfish.py:get_or_compute_eval` | yes | mocked compute |
| `puzzles.generate_puzzles` | `puzzles/generator.py:generate_puzzles` | yes | **mocked** |
| `motifs.get_user_motif_performance` | `motifs.py` | yes | n/a |
| `dashboard.calculate_recent_form` | `dashboard.py` | yes | n/a |
| `dashboard.calculate_training_streak` | `dashboard.py` | yes | n/a |
| `game_repo.get_all_metadata` | `storage/game_repository.py` | yes | n/a |
| `game_repo.get_pgns (batch)` | `storage/game_repository.py` | yes | n/a |
| `puzzle_repo.get_all_puzzles` | `storage/puzzle_repository.py` | yes | n/a |
| `puzzle_repo.get_daily_puzzles` | `storage/puzzle_repository.py` | yes | n/a |
| `import.store_game batch` | `storage/game_repository.py:store_game(commit=False)` | yes | n/a |
| `ratings.explain aggregation` | mirrors `main.py:explain_rating_changes` loop | yes | n/a |

### What is mocked (and why)

- **Stockfish is always mocked** with a deterministic fixed-eval stub
  (`benchmarks/mock_engine.py`). The real engine is a subprocess whose latency
  depends on the host, the binary version, and the search depth — none of which
  belong in a repeatable benchmark. The stub returns a stable pseudo-eval derived
  from `crc32(fen)`, so the timing measures **only our own Python/DB code**, never
  the engine's search.
  - `puzzles.generate_puzzles`: `create_engine`, `get_or_compute_eval`,
    `get_top_moves`, `close_engine` are all patched. The full generation control
    flow runs (PGN load, per-ply walk, shallow scan, deep confirmation, uniqueness
    check, `save_puzzle`) — everything except the engine search itself.
  - `engine_cache.get_or_compute`: only the underlying `evaluate_fen` and the
    `get_engine_version` probe are mocked. The **real** `get_or_compute_eval` runs,
    so the numbers reflect the genuine DB cache read (hit) and read+insert (miss).
- **The DB is a throwaway SQLite file**, bound only for the run and deleted after
  (`runner.py:bind_benchmark_db`). See the Postgres caveat below.

### What the numbers do *not* tell you

Because Stockfish is mocked, the `generate_puzzles` and `engine_cache` latencies
are **not** representative of real evaluation cost (a real depth-12 eval is
~10–100 ms of engine search per position, dwarfing everything here). They measure
the *orchestration and DB overhead around* the engine, which is what we can make
faster without touching engine correctness. The engine cost itself is a separate,
host-and-depth-dependent quantity.

## How to run

```bash
# Smallest scale, human summary to stdout:
DATABASE_URL=postgresql+psycopg://knightmind:knightmind@localhost:5432/knightmind python -m services.api.benchmarks --scale small

# Medium scale, also write machine-readable JSON:
DATABASE_URL=postgresql+psycopg://knightmind:knightmind@localhost:5432/knightmind python -m services.api.benchmarks --scale medium --out bench_medium.json

# Reproduce the smoke test (CI-friendly, tiny iteration count):
DATABASE_URL=postgresql+psycopg://knightmind:knightmind@localhost:5432/knightmind python -m pytest services/api/benchmarks/test_benchmarks_smoke.py -q
```

`DATABASE_URL` must be set: the app fails fast without it, and refuses SQLite.
DB regardless of that variable.

Flags: `--scale {small,medium,large}`, `--iterations N` (override), `--seed N`
(default 1234), `--out PATH` (JSON), `--quiet`.

## Fixture scales

Fixtures are generated deterministically from the seed (`benchmarks/fixtures.py`).
Games are real, parseable PGNs of 30 seeded-random legal moves each.

| Scale | Games | Puzzles | Puzzle stats | Reviews | Sessions | Default iters |
| --- | --- | --- | --- | --- | --- | --- |
| `small` | 100 | 100 | 100 | 300 | 40 | 30 |
| `medium` | 2,000 | 2,000 | 2,000 | 6,000 | 60 | 15 |
| `large` | 20,000 | 4,000 (capped) | 4,000 | 12,000 | 90 | 5 |

Puzzle count is capped at 4,000 so the aggregation paths stay bounded at the
`large` scale. `generate_puzzles` is always bounded to the 20 most-recent games
regardless of scale, so its cost does not grow with the corpus.

## Baseline results

Measured on: **Apple M3, macOS 26.5, Python 3.13.9, SQLAlchemy 2.0.36,
python-chess 1.11.2, SQLite (throwaway file)**. Latency in **milliseconds**;
`peakKiB` is the tracemalloc peak of a single representative call; `queries` is
the SQL statement count of a single representative call.

Absolute numbers are host-dependent — treat them as a **baseline to detect
regressions against on the same machine**, not as an SLO.

### small (100 games) — full run ≈ 11 s

| benchmark | median | p95 | p99 | peakKiB | queries |
| --- | ---: | ---: | ---: | ---: | ---: |
| opening_tree.build max_ply=6 | 40.01 | 51.07 | 112.80 | 566 | — |
| opening_tree.build max_ply=12 | 56.95 | 58.21 | 64.85 | 964 | — |
| opening_tree.build max_ply=20 | 92.34 | 95.82 | 103.35 | 1627 | — |
| engine_cache.get_or_compute (cold/miss) | 0.61 | 1.00 | 3.43 | — | — |
| engine_cache.get_or_compute (warm/hit) | 0.26 | 0.33 | 0.40 | — | 1 |
| puzzles.generate_puzzles (max_games=20) | 36.32 | 41.81 | 123.08 | 768 | — |
| motifs.get_user_motif_performance | 0.23 | 0.26 | 0.46 | — | 1 |
| dashboard.calculate_recent_form | 0.21 | 0.30 | 0.42 | — | 1 |
| dashboard.calculate_training_streak | 0.33 | 0.36 | 0.54 | — | 1 |
| game_repo.get_all_metadata | 0.67 | 0.78 | 0.86 | — | 1 |
| game_repo.get_pgns (batch=100) | 0.24 | 0.30 | 0.57 | — | 1 |
| puzzle_repo.get_all_puzzles | 1.10 | 1.25 | 1.38 | — | 1 |
| puzzle_repo.get_daily_puzzles (n=5) | 1.11 | 1.28 | 1.37 | — | 1 |
| import.store_game batch (n=200, single commit) | 124.98 | 206.76 | 206.76 | — | — |
| ratings.explain aggregation (limit=200) | 1.07 | 1.26 | 1.54 | — | — |

### medium (2,000 games) — full run ≈ 105 s

| benchmark | median | p95 | p99 | peakKiB | queries |
| --- | ---: | ---: | ---: | ---: | ---: |
| opening_tree.build max_ply=6 | 825.21 | 858.69 | 937.73 | 5,998 | — |
| opening_tree.build max_ply=12 | 1,184.69 | 1,331.01 | 1,346.06 | 15,389 | — |
| opening_tree.build max_ply=20 | 1,927.38 | 2,084.72 | 2,108.91 | 27,996 | — |
| engine_cache.get_or_compute (cold/miss) | 0.62 | 0.98 | 4.57 | — | — |
| engine_cache.get_or_compute (warm/hit) | 0.20 | 0.22 | 0.35 | — | 1 |
| puzzles.generate_puzzles (max_games=20) | 48.32 | 54.41 | 140.38 | 2,414 | — |
| motifs.get_user_motif_performance | 0.73 | 0.95 | 1.07 | — | 1 |
| dashboard.calculate_recent_form | 0.60 | 0.84 | 0.97 | — | 1 |
| dashboard.calculate_training_streak | 0.42 | 0.45 | 0.63 | — | 1 |
| game_repo.get_all_metadata | 11.86 | 12.22 | 12.24 | — | 1 |
| game_repo.get_pgns (batch=200) | 0.41 | 0.46 | 0.66 | — | 1 |
| puzzle_repo.get_all_puzzles | 15.85 | 16.41 | 31.74 | — | 1 |
| puzzle_repo.get_daily_puzzles (n=5) | 15.90 | 16.85 | 33.62 | — | 1 |
| import.store_game batch (n=200, single commit) | 124.80 | 126.49 | 126.49 | — | — |
| ratings.explain aggregation (limit=200) | 12.36 | 12.68 | 12.91 | — | — |

### large (20,000 games)

Not run as part of this baseline (fixture build alone is ~30 s and the full run is
several minutes). It is fully supported: `python -m services.api.benchmarks
--scale large`. The DB-aggregation paths that load the whole corpus
(`get_all_metadata`, `get_all_puzzles`, `get_daily_puzzles`) are the ones expected
to grow; `generate_puzzles`, the engine cache, and `get_pgns` are bounded and
should stay flat.

## Query-count / index notes

Query counts are captured with a SQLAlchemy `before_cursor_execute` listener
(`runner.py:count_queries`). Findings for the DB-heavy paths:

- **Single-query aggregations.** `get_user_motif_performance` (GROUP BY
  `primary_motif`), `calculate_recent_form` (ORDER BY `reviewed_at` DESC LIMIT
  20), `get_all_metadata`, `get_all_puzzles`, and the warm cache hit each issue
  **exactly one** statement — no N+1. Good.
- **Indexes exercised.**
  - `get_all_metadata` uses `ix_games_username_end_time` (`username`, `end_time`)
    for the `WHERE username = ? ORDER BY end_time DESC` — index-covered ordering.
  - `puzzle_stats` aggregation and `recent_form` filter on `username` (indexed).
  - `tricky_puzzles` (not benchmarked directly) has a dedicated composite
    `ix_puzzle_stats_tricky_puzzles`.
  - The warm cache hit is a `db.get(FenEvalCache, key)` primary-key lookup (1
    query), confirming the cache read is O(1).
- **Linear-scan hot spots (grow with corpus size).**
  - `get_all_metadata` / `get_all_puzzles` return the **entire** user corpus with
    no LIMIT: ~12 ms and ~16 ms at 2k rows, up from <1 ms at 100. Fine today,
    worth pagination as corpora grow.
  - `puzzle_repo.get_daily_puzzles` loads **all** puzzles then paginates/sorts in
    Python (it calls `get_all_puzzles` internally). It tracks `get_all_puzzles`
    exactly — the daily rotation does no DB-side filtering or LIMIT.
- **Write path.** `import.store_game` batches inserts with one commit, but does a
  per-row `db.get(Game, pk)` existence check **and** an insert inside a savepoint
  (`begin_nested`) — ~125 ms for 200 rows (~0.6 ms/row) and flat across scales
  (fresh tenant, no contention).

## Postgres caveat

The baseline runs against a **throwaway SQLite file**. Production runs Postgres
(Supabase today, self-hosted Hetzner planned). A Postgres run would add, per
statement:

- **Network round-trip latency** to the DB host (SQLite is in-process; there is
  no round-trip here). This dominates for the single-query paths — expect each
  ~0.2–1 ms SQLite query to become a few ms over a network.
- **Real planner/index behaviour**: Postgres honours the partial indexes
  (`ix_jobs_active_username`, the `COALESCE` functional unique index on
  `puzzle_reviews`) and `SELECT … FOR UPDATE` locking that SQLite treats as
  no-ops. Query *counts* are identical; per-query *cost* differs.
- **`pool_pre_ping`** is enabled for Postgres (disabled for SQLite here), adding a
  liveness check per checked-out connection.

To run against a disposable Postgres instead of SQLite, point the harness's bound
engine at a throwaway database — e.g. a local `docker run --rm postgres` — by
adapting `bind_benchmark_db` to accept a Postgres URL (the fixtures and benchmarks
are dialect-agnostic; only the engine URL and `connect_args` change). Do **not**
point it at any shared/staging DB: the harness creates and writes tables.

## Follow-ups worth investigating (not fixed here)

1. **`OpeningTreeBuilder` is O(ply²) per game.** In
   `tree_builder._add_moves_to_tree`, `board = node.board()` is called inside the
   per-ply loop; python-chess's `GameNode.board()` replays every move from the
   start on each call, so a 30-ply game does ~30 replays of growing length. This
   shows up as the super-linear growth of the `max_ply=20` benchmark (≈2 s for 2k
   PGNs) and its memory (≈28 MiB peak). Walking one board with `board.push(move)`
   and computing SAN before the push would make it linear. **Flagged, not
   changed** (product code is out of scope for this task).
2. **Whole-corpus reads without pagination.** `get_all_metadata` and
   `get_all_puzzles` (and therefore `get_daily_puzzles`) materialize the full user
   corpus. Acceptable at today's per-user sizes; candidates for a
   LIMIT/keyset-pagination pass before per-user corpora reach tens of thousands.
