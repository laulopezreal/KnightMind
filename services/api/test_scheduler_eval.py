"""Tests for the offline scheduler-evaluation harness.

These lock in the three properties that make the harness trustworthy:
  1. determinism  (same seed -> identical numbers),
  2. metric correctness on a tiny hand-checkable case,
  3. both schedulers respect their documented bounds.
"""

import math

from services.api.analysis.scheduler_eval import (
    CURRENT,
    EASE_MAX,
    EASE_MIN,
    TARGET,
    TUNED,
    MemoryState,
    ReviewSample,
    SchedulerRun,
    _make_memory,
    _quantile,
    evaluate,
    run,
    summarize,
)

# --- determinism ---------------------------------------------------------


def test_evaluate_is_deterministic_same_seed():
    a = evaluate(seed=12345, n_learners=5, n_puzzles=5, horizon_days=90)
    b = evaluate(seed=12345, n_learners=5, n_puzzles=5, horizon_days=90)
    assert a == b


def test_evaluate_changes_with_seed():
    a = evaluate(seed=1, n_learners=5, n_puzzles=5, horizon_days=90)
    b = evaluate(seed=2, n_learners=5, n_puzzles=5, horizon_days=90)
    assert a != b


def test_run_no_randomness_leak_between_schedulers():
    # Each pair shares one memory + one deck across schedulers, so the review
    # count is stable and every scheduler sees the same number of items.
    runs = run(seed=7, n_learners=2, n_puzzles=2, horizon_days=60)
    n_items = {r.n_items for r in runs.values()}
    assert n_items == {4}


# --- metric correctness (hand-checkable) ---------------------------------


def test_quantile_hand_values():
    assert _quantile([1, 2, 3], 0.5) == 2
    assert _quantile([1, 2, 3, 4], 0.5) == 2.5
    # pos = 0.25 * 3 = 0.75 -> 1*0.25 + 2*0.75 = 1.75
    assert _quantile([1, 2, 3, 4], 0.25) == 1.75
    assert _quantile([42], 0.5) == 42
    assert math.isnan(_quantile([], 0.5))


def test_summarize_hand_case():
    r = SchedulerRun(
        name="x",
        samples=[
            ReviewSample(recall_prob=0.9, passed=True, interval_days=2),
            ReviewSample(recall_prob=0.5, passed=False, interval_days=4),
        ],
        end_retention=[0.8, 0.6],
        total_reviews=2,
        n_items=2,
        horizon_days=365,
    )
    m = summarize(r, target=0.9)
    assert m["n_reviews"] == 2.0
    assert m["median_recall_at_due"] == 0.7
    assert m["recall_p25"] == 0.6
    assert m["recall_p75"] == 0.8
    assert m["frac_remembered_at_due"] == 0.5
    # median(|0.9-0.9|, |0.5-0.9|) = median(0.0, 0.4) = 0.2
    assert abs(m["median_calibration_error"] - 0.2) < 1e-12
    # 2 reviews / 2 items * (365/365) = 1.0
    assert m["reviews_per_item_per_year"] == 1.0
    assert m["median_end_retention"] == 0.7


# --- ground-truth model sanity ------------------------------------------


def test_recall_prob_is_monotone_and_bounded():
    mem = MemoryState(half_life=5.0, difficulty=0.3)
    assert mem.recall_prob(0.0) == 1.0
    assert mem.recall_prob(5.0) == 0.5  # one half-life
    # strictly decreasing in elapsed time
    prev = 1.01
    for t in range(0, 30):
        p = mem.recall_prob(float(t))
        assert 0.0 < p <= 1.0
        assert p < prev
        prev = p


def test_pass_grows_and_fail_shrinks_stability():
    import random

    mem = _make_memory(random.Random(0))
    grown = mem.after_pass(0.5)
    assert grown.half_life > mem.half_life
    lapsed = grown.after_fail()
    assert lapsed.half_life <= grown.half_life
    assert lapsed.half_life >= 0.5  # relearn floor


# --- scheduler bounds ----------------------------------------------------

import random as _random  # noqa: E402


def _drive(scheduler, outcomes):
    """Drive a scheduler through a fixed pass/fail sequence, yielding intervals
    and the raw state so bounds can be asserted."""
    state = scheduler.init()
    out = []
    for passed in outcomes:
        interval, state = scheduler.step(state, passed)
        out.append((interval, state))
    return out


def _random_outcomes(seed, n):
    rng = _random.Random(seed)
    return [rng.random() < 0.7 for _ in range(n)]


def test_current_scheduler_respects_bounds():
    for seed in range(20):
        for interval, state in _drive(CURRENT, _random_outcomes(seed, 40)):
            assert interval >= 1
            assert EASE_MIN <= state.ease <= EASE_MAX


def test_tuned_scheduler_respects_bounds():
    for seed in range(20):
        for interval, state in _drive(TUNED, _random_outcomes(seed, 40)):
            assert interval >= 1
            assert EASE_MIN <= state.ease <= EASE_MAX


def test_target_scheduler_respects_bounds():
    for seed in range(20):
        for interval, state in _drive(TARGET, _random_outcomes(seed, 40)):
            assert interval >= 1
            assert state.stability > 0.0


def test_current_wraps_real_production_rules():
    # A fresh item that passes should get interval 1 then 3 (production ladder).
    state = CURRENT.init()
    i1, state = CURRENT.step(state, True)
    i2, state = CURRENT.step(state, True)
    assert (i1, i2) == (1, 3)
    # A fail resets interval to 1 and drops ease by 0.2.
    ease_before = state.ease
    i3, state = CURRENT.step(state, False)
    assert i3 == 1
    assert abs(state.ease - max(EASE_MIN, ease_before - 0.2)) < 1e-12
