"""Smoke test: the benchmark harness runs end-to-end and emits results.

Runs the whole harness at the smallest scale with a tiny iteration count so CI /
other developers can reproduce it quickly, and asserts the JSON results file is
produced with sane structure. This is a *harness* test, not a performance gate:
it never asserts on latency numbers (those are host-dependent).
"""

import json

from services.api.benchmarks.runner import SCALES, format_human, run_all


def test_scales_registered():
    assert set(SCALES) == {"small", "medium", "large"}
    assert SCALES["small"].games == 100


def test_harness_runs_end_to_end(tmp_path):
    payload = run_all("small", iterations=2, seed=1234)

    meta = payload["meta"]
    assert meta["scale"] == "small"
    assert meta["seed"] == 1234
    assert meta["iterations"] == 2
    # Fixtures were actually generated.
    assert meta["fixture_counts"]["games"] == 100
    assert meta["fixture_counts"]["puzzles"] > 0

    results = payload["results"]
    assert results, "expected at least one benchmark result"

    names = {r["name"] for r in results}
    # Every hot path family is represented.
    assert any("opening_tree.build" in n for n in names)
    assert any("engine_cache.get_or_compute (warm/hit)" in n for n in names)
    assert any("generate_puzzles" in n for n in names)
    assert any("get_user_motif_performance" in n for n in names)
    assert any("get_all_puzzles" in n for n in names)
    assert any("get_pgns" in n for n in names)
    assert any("store_game batch" in n for n in names)
    assert any("ratings.explain aggregation" in n for n in names)

    for r in results:
        # Latency summary is well-formed and non-negative.
        assert r["median_ms"] >= 0.0
        assert r["p95_ms"] >= 0.0
        assert r["p99_ms"] >= 0.0
        assert r["iterations"] >= 1

    # The warm cache hit must be a single primary-key lookup.
    warm = next(r for r in results if "warm/hit" in r["name"])
    assert warm["query_count"] == 1


def test_results_file_is_written(tmp_path):
    payload = run_all("small", iterations=2, seed=1234)
    out = tmp_path / "results.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["meta"]["scale"] == "small"
    assert reloaded["results"]

    # Human formatter produces a non-trivial table.
    text = format_human(payload)
    assert "KnightMind benchmarks" in text
    assert "median" in text


def test_determinism_same_seed_same_fixtures():
    """Same seed -> identical fixture corpus (counts + first PGN)."""
    from services.api.benchmarks import fixtures as fx
    from services.api.benchmarks.runner import bind_benchmark_db

    def corpus():
        with bind_benchmark_db() as (_engine, sf):
            data = fx.generate(sf, SCALES["small"], seed=42)
            return data.counts, data.pgns[0], data.game_ids[0]

    a = corpus()
    b = corpus()
    assert a == b
