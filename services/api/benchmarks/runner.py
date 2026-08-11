"""Deterministic benchmark runner for the KnightMind API hot paths.

Times each hot path over multiple iterations and reports median / p95 / p99
latency plus peak allocated memory (tracemalloc) for a representative run. All
inputs are deterministic (fixed-seed fixtures + a mocked engine), so re-running
at the same scale reproduces the same work.

Run it with::

    python -m services.api.benchmarks --scale small

The DB layer uses a throwaway SQLite file bound only for the run (see
``bind_benchmark_db``). A real deployment runs Postgres; ``docs/benchmarks.md``
notes what a Postgres run would add (network round-trips, real index behaviour).
"""

import gc
import statistics
import tempfile
import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from services.api.benchmarks import fixtures as fx
from services.api.benchmarks.fixtures import Scale
from services.api.benchmarks.mock_engine import (
    mock_engine_cache_compute,
    mock_generator_engine,
)

# --------------------------------------------------------------------------- #
# Scales
# --------------------------------------------------------------------------- #
SCALES: dict[str, Scale] = {
    "small": Scale(name="small", games=100, sessions=40),
    "medium": Scale(name="medium", games=2_000, sessions=60),
    "large": Scale(name="large", games=20_000, sessions=90),
}


# --------------------------------------------------------------------------- #
# DB binding
# --------------------------------------------------------------------------- #
@contextmanager
def bind_benchmark_db(db_path: str | None = None):
    """Bind an isolated SQLite DB and repoint every module-level ``SessionLocal``.

    The app code (``get_or_compute_eval``, ``generate_puzzles``) opens sessions
    via module-level ``SessionLocal`` symbols captured at import time. We swap
    those symbols (and the ``db`` module's engine) for the duration of the run so
    the benchmark never touches the real dev/prod database, then restore them.

    Yields ``(engine, session_factory)``.
    """
    import services.api.db as db_mod
    import services.api.engine.stockfish as sf_mod
    import services.api.puzzles.generator as gen_mod

    # A temp file (not :memory:) so sessions opened on separate connections all
    # see the same data, mirroring a shared server DB.
    tmp = None
    if db_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        db_path = tmp.name
    url = f"sqlite:///{db_path}"

    engine = create_engine(url, connect_args={"check_same_thread": False})
    # Import models so every table is registered on Base.metadata before create.
    import services.api.models  # noqa: F401
    from services.api.db import Base

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Four locals rather than one dict: a dict literal over a sessionmaker and
    # an Engine infers as dict[str, object], so restoring from it assigns
    # `object` back onto attributes that are typed.
    saved_db_session_local = db_mod.SessionLocal
    saved_db_engine = db_mod.engine
    saved_sf_session_local = sf_mod.SessionLocal
    saved_gen_session_local = gen_mod.SessionLocal
    db_mod.SessionLocal = session_factory
    db_mod.engine = engine
    sf_mod.SessionLocal = session_factory
    gen_mod.SessionLocal = session_factory
    try:
        yield engine, session_factory
    finally:
        db_mod.SessionLocal = saved_db_session_local
        db_mod.engine = saved_db_engine
        sf_mod.SessionLocal = saved_sf_session_local
        gen_mod.SessionLocal = saved_gen_session_local
        engine.dispose()
        if tmp is not None:
            Path(tmp.name).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Measurement primitives
# --------------------------------------------------------------------------- #
@dataclass
class BenchResult:
    name: str
    category: str
    iterations: int
    needs_db: bool
    median_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    mean_ms: float
    peak_kib: float | None = None
    query_count: int | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)


def _percentile(sorted_samples: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in [0, 100])."""
    if not sorted_samples:
        return 0.0
    k = max(0, min(len(sorted_samples) - 1, round(pct / 100 * len(sorted_samples)) - 1))
    return sorted_samples[k]


def _time_ms(fn: Callable[[], object]) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000.0


def measure(
    fn: Callable[[], object],
    *,
    iterations: int,
    warmup: int = 1,
) -> dict:
    """Run ``fn`` ``iterations`` times and summarize latency (ms)."""
    for _ in range(warmup):
        fn()
    gc.collect()
    samples = [_time_ms(fn) for _ in range(iterations)]
    samples_sorted = sorted(samples)
    return {
        "median_ms": round(statistics.median(samples), 4),
        "p95_ms": round(_percentile(samples_sorted, 95), 4),
        "p99_ms": round(_percentile(samples_sorted, 99), 4),
        "min_ms": round(samples_sorted[0], 4),
        "max_ms": round(samples_sorted[-1], 4),
        "mean_ms": round(statistics.fmean(samples), 4),
    }


def measure_peak_kib(fn: Callable[[], object]) -> float:
    """Peak *allocated* Python memory (KiB) for a single call of ``fn``."""
    gc.collect()
    tracemalloc.start()
    fn()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return round(peak / 1024.0, 1)


@contextmanager
def count_queries(engine):
    """Count SQL statements issued on ``engine`` within the block.

    Yields a one-key dict ``{"count": int}`` updated live via a cursor-execute
    event listener.
    """
    box = {"count": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        box["count"] += 1

    event.listen(engine, "before_cursor_execute", _before)
    try:
        yield box
    finally:
        event.remove(engine, "before_cursor_execute", _before)


# --------------------------------------------------------------------------- #
# Benchmark definitions
# --------------------------------------------------------------------------- #
def _bench_opening_tree(data: fx.Fixtures, iterations: int) -> list[BenchResult]:
    """Pure-CPU opening tree build at several max_ply depths (no DB)."""
    from services.api.openings.tree_builder import build_opening_tree

    results = []
    for max_ply in (6, 12, 20):

        def run(mp=max_ply):
            build_opening_tree(data.pgns, data.username, max_ply=mp)

        stats = measure(run, iterations=iterations)
        peak = measure_peak_kib(run)
        results.append(
            BenchResult(
                name=f"opening_tree.build max_ply={max_ply}",
                category="cpu",
                iterations=iterations,
                needs_db=False,
                peak_kib=peak,
                notes=f"{len(data.pgns)} PGNs parsed + tree built",
                **stats,
            )
        )
    return results


def _bench_engine_cache(
    data: fx.Fixtures, engine, session_factory, iterations: int
) -> list[BenchResult]:
    """Engine-cache read cost: cold (miss -> compute + store) vs warm (hit)."""
    from services.api.engine.stockfish import get_or_compute_eval

    results = []
    # Distinct FENs so a "miss" always computes+stores; reuse the same FEN for
    # the warm-hit measurement.
    base = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 {n}"
    with mock_engine_cache_compute():
        counter = {"n": 0}

        def cold():
            counter["n"] += 1
            get_or_compute_eval(base.format(n=counter["n"]))

        cold_stats = measure(cold, iterations=iterations, warmup=0)
        results.append(
            BenchResult(
                name="engine_cache.get_or_compute (cold/miss)",
                category="db+engine(mock)",
                iterations=iterations,
                needs_db=True,
                notes="cache miss: DB lookup + mocked compute + DB insert",
                **cold_stats,
            )
        )

        # Warm the cache once, then measure repeated hits on the same FEN.
        warm_fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 2 3"
        get_or_compute_eval(warm_fen)

        def warm():
            get_or_compute_eval(warm_fen)

        with count_queries(engine) as qbox:
            warm()
        warm_stats = measure(warm, iterations=iterations)
        results.append(
            BenchResult(
                name="engine_cache.get_or_compute (warm/hit)",
                category="db+engine(mock)",
                iterations=iterations,
                needs_db=True,
                query_count=qbox["count"],
                notes="cache hit: single DB primary-key lookup",
                **warm_stats,
            )
        )
    return results


def _bench_generate_puzzles(
    data: fx.Fixtures, session_factory, iterations: int
) -> list[BenchResult]:
    """Full puzzle-generation batch over recent games with a mocked engine."""
    from services.api.puzzles.generator import generate_puzzles

    # Bound the batch so it is practical and identical every run.
    max_games = 20

    def run():
        # Each run may create puzzles; duplicates are skipped (unique index), so
        # repeated iterations stay deterministic and don't blow up the DB.
        generate_puzzles(data.username, max_games=max_games, max_puzzles=30)

    with mock_generator_engine():
        stats = measure(run, iterations=iterations, warmup=1)
        peak = measure_peak_kib(run)
    return [
        BenchResult(
            name=f"puzzles.generate_puzzles (max_games={max_games})",
            category="db+engine(mock)",
            iterations=iterations,
            needs_db=True,
            peak_kib=peak,
            notes="bulk PGN load + per-ply mocked eval + confirmation + save",
            **stats,
        )
    ]


def _bench_aggregations(
    data: fx.Fixtures, engine, session_factory, iterations: int
) -> list[BenchResult]:
    """Dashboard / motif / trends aggregation queries."""
    from services.api.dashboard import (
        calculate_recent_form,
        calculate_training_streak,
    )
    from services.api.motifs import get_user_motif_performance

    results = []

    def _db_bench(name, fn_factory, note):
        # Query count is captured on one call; latency over many.
        with session_factory() as s, count_queries(engine) as qbox:
            fn_factory(s)()
        with session_factory() as s:
            fn = fn_factory(s)
            stats = measure(fn, iterations=iterations)
        results.append(
            BenchResult(
                name=name,
                category="db-aggregation",
                iterations=iterations,
                needs_db=True,
                query_count=qbox["count"],
                notes=note,
                **stats,
            )
        )

    _db_bench(
        "motifs.get_user_motif_performance",
        lambda s: (lambda: get_user_motif_performance(s, data.username)),
        "GROUP BY primary_motif over puzzle_stats",
    )
    _db_bench(
        "dashboard.calculate_recent_form",
        lambda s: (lambda: calculate_recent_form(s, data.username)),
        "last-20 reviews ORDER BY reviewed_at DESC LIMIT 20",
    )
    _db_bench(
        "dashboard.calculate_training_streak",
        lambda s: (lambda: calculate_training_streak(s, data.username)),
        "DISTINCT completed-session days, walked in Python",
    )
    return results


def _bench_repositories(
    data: fx.Fixtures, engine, session_factory, iterations: int
) -> list[BenchResult]:
    """Storage repository list / pagination / PGN batch loading."""
    from services.api.storage import GameRepository, PuzzleRepository

    results = []

    def _db_bench(name, fn_factory, note, extra=None):
        with session_factory() as s, count_queries(engine) as qbox:
            fn_factory(s)()
        with session_factory() as s:
            stats = measure(fn_factory(s), iterations=iterations)
        results.append(
            BenchResult(
                name=name,
                category="db-repository",
                iterations=iterations,
                needs_db=True,
                query_count=qbox["count"],
                notes=note,
                extra=extra or {},
                **stats,
            )
        )

    _db_bench(
        "game_repo.get_all_metadata",
        lambda s: (lambda: GameRepository(s).get_all_metadata(data.username)),
        "metadata-only projection, ORDER BY end_time DESC",
        {"rows": data.counts["games"]},
    )

    # PGN batch loading (bulk-load recent 200 ids in one/few IN queries).
    batch_ids = data.game_ids[: min(200, len(data.game_ids))]
    _db_bench(
        "game_repo.get_pgns (batch=%d)" % len(batch_ids),
        lambda s: (lambda: GameRepository(s).get_pgns(data.username, batch_ids)),
        "bulk PGN load via IN() batched at PGN_BATCH_SIZE",
        {"batch": len(batch_ids)},
    )

    _db_bench(
        "puzzle_repo.get_all_puzzles",
        lambda s: (lambda: PuzzleRepository(s).get_all_puzzles(data.username)),
        "full puzzle list for user (no pagination)",
        {"rows": data.counts["puzzles"]},
    )
    _db_bench(
        "puzzle_repo.get_daily_puzzles (n=5)",
        lambda s: (lambda: PuzzleRepository(s).get_daily_puzzles(data.username, 5)),
        "loads ALL puzzles then paginates/sorts in Python",
        {"rows": data.counts["puzzles"]},
    )
    return results


def _bench_import_batch(
    data: fx.Fixtures, session_factory, iterations: int
) -> list[BenchResult]:
    """Import batch: store_game(commit=False) x N + single commit."""
    from services.api.storage import GameRepository

    n = 200
    rng_pgn = data.pgns[0] if data.pgns else ""

    def run():
        # A fresh tenant per call so inserts are always NEW (no dup short-circuit)
        # and never collide across iterations.
        import uuid

        user = f"import_{uuid.uuid4().hex[:8]}"
        with session_factory() as s:
            repo = GameRepository(s)
            for i in range(n):
                repo.store_game(
                    username=user,
                    url=f"https://example.org/import/{user}/{i}",
                    pgn=rng_pgn,
                    white_username=user,
                    black_username="opp",
                    white_result="win",
                    black_result="loss",
                    time_control="600",
                    end_time=fx._BASE_END_TIME - i,
                    rated=True,
                    commit=False,
                )
            s.commit()

    stats = measure(run, iterations=max(3, iterations // 3), warmup=1)
    return [
        BenchResult(
            name=f"import.store_game batch (n={n}, single commit)",
            category="db-write",
            iterations=max(3, iterations // 3),
            needs_db=True,
            notes="savepoint-per-row insert, one commit for the batch",
            extra={"batch": n},
            **stats,
        )
    ]


def _bench_ratings_aggregation(
    data: fx.Fixtures, session_factory, iterations: int
) -> list[BenchResult]:
    """Ratings-explain aggregation: metadata + bulk PGN + Elo extraction.

    Mirrors the hot loop of ``main.py:explain_rating_changes`` (metadata scan,
    bulk PGN load, per-game opponent-Elo regex + expected-score) WITHOUT
    importing the FastAPI app, so the harness stays decoupled. The regex + Elo
    math below are a faithful copy of the app's helpers.
    """
    import re

    from services.api.storage import GameMetadata, GameRepository
    from services.api.time_control import classify_time_control

    elo_re = re.compile(r'\[(?:White|Black)Elo "(\d+)"\]')

    def _expected(player: int, opp: int) -> float:
        return 1 / (1 + 10 ** ((opp - player) / 400))

    limit_games = 200

    def run():
        with session_factory() as s:
            repo = GameRepository(s)
            metadata = repo.get_all_metadata(data.username)
            relevant: list[GameMetadata] = []
            for meta in metadata:
                if len(relevant) >= limit_games:
                    break
                if classify_time_control(meta.time_control) != "rapid":
                    continue
                relevant.append(meta)
            pgns = repo.get_pgns(data.username, [m.game_id for m in relevant])
            total = 0.0
            for meta in relevant:
                pgn = pgns.get(meta.game_id)
                if not pgn:
                    continue
                m = elo_re.search(pgn)
                if m:
                    total += _expected(1500, int(m.group(1)))
            return total

    stats = measure(run, iterations=iterations, warmup=1)
    return [
        BenchResult(
            name=f"ratings.explain aggregation (limit={limit_games})",
            category="db+cpu",
            iterations=iterations,
            needs_db=True,
            notes="metadata scan + bulk PGN + Elo regex + expected-score",
            **stats,
        )
    ]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_all(
    scale_name: str = "small",
    *,
    iterations: int | None = None,
    seed: int = 1234,
    db_path: str | None = None,
) -> dict:
    """Run every benchmark at ``scale_name`` and return a results dict."""
    if scale_name not in SCALES:
        raise ValueError(f"unknown scale {scale_name!r}; choose from {list(SCALES)}")
    scale = SCALES[scale_name]

    # Fewer iterations for bigger scales so wall-clock stays bounded.
    if iterations is None:
        iterations = {"small": 30, "medium": 15, "large": 5}[scale_name]

    started = time.time()
    results: list[BenchResult] = []
    fixture_seconds = 0.0

    with bind_benchmark_db(db_path) as (engine, session_factory):
        t_fix = time.perf_counter()
        data = fx.generate(session_factory, scale, seed=seed)
        fixture_seconds = round(time.perf_counter() - t_fix, 3)

        results += _bench_opening_tree(data, iterations)
        results += _bench_engine_cache(data, engine, session_factory, iterations)
        results += _bench_generate_puzzles(data, session_factory, iterations)
        results += _bench_aggregations(data, engine, session_factory, iterations)
        results += _bench_repositories(data, engine, session_factory, iterations)
        results += _bench_import_batch(data, session_factory, iterations)
        results += _bench_ratings_aggregation(data, session_factory, iterations)

    total_seconds = round(time.time() - started, 3)

    return {
        "meta": {
            "scale": scale_name,
            "seed": seed,
            "iterations": iterations,
            "fixture_counts": data.counts,
            "fixture_build_seconds": fixture_seconds,
            "total_seconds": total_seconds,
            "db": "sqlite (throwaway file)",
            "engine": "mocked (deterministic fixed-eval stub)",
            "generated_at_epoch": int(started),
        },
        "results": [asdict(r) for r in results],
    }


def format_human(payload: dict) -> str:
    """Render a results payload as a plain-text table."""
    meta = payload["meta"]
    lines = []
    lines.append(
        f"KnightMind benchmarks — scale={meta['scale']} seed={meta['seed']} "
        f"iterations={meta['iterations']}"
    )
    lines.append(
        "fixtures: "
        + ", ".join(f"{k}={v}" for k, v in meta["fixture_counts"].items())
        + f"  (built in {meta['fixture_build_seconds']}s)"
    )
    lines.append(
        f"db={meta['db']}  engine={meta['engine']}  " f"total={meta['total_seconds']}s"
    )
    lines.append("")
    header = (
        f"{'benchmark':<48}{'median':>10}{'p95':>10}{'p99':>10}"
        f"{'peakKiB':>10}{'queries':>9}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in payload["results"]:
        peak = "" if r["peak_kib"] is None else f"{r['peak_kib']:.0f}"
        q = "" if r["query_count"] is None else str(r["query_count"])
        lines.append(
            f"{r['name']:<48}"
            f"{r['median_ms']:>10.3f}"
            f"{r['p95_ms']:>10.3f}"
            f"{r['p99_ms']:>10.3f}"
            f"{peak:>10}"
            f"{q:>9}"
        )
    lines.append("")
    lines.append(
        "latency in ms; peak = tracemalloc peak (single rep); queries = SQL statements (single rep)"
    )
    return "\n".join(lines)
