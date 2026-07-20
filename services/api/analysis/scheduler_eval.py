"""Offline, deterministic evaluation harness for the spaced-repetition scheduler.

Purpose (SCORECARD dim 16): the SM2-ish scheduler in
``services/api/storage/spaced_repetition.calculate_next_interval`` has never
been evaluated. This harness replays synthetic review sequences through the
*real* production scheduler and through documented alternatives against the
same simulated memory ground truth, and reports retention / workload /
calibration metrics.

IMPORTANT HONESTY CAVEAT
------------------------
The learner memory here is a *model*, not real KnightMind data. Results show
how each scheduler behaves *under the assumptions of that model* (an
exponential/half-life forgetting curve with per-item difficulty and a
desirable-difficulty stability-growth rule). They are directional evidence for
comparing scheduling policies, not a measurement of real user retention. A
real-data replay (see docs/scheduler-evaluation.md) would be needed to confirm.

Everything is deterministic: a single ``random.Random(seed)`` drives all draws,
there is an integer "day" clock (no wall-clock time), and the same seed always
produces the same numbers. No production state is touched.

Run::

    python -m services.api.analysis.scheduler_eval

The production scheduler is exercised through the genuine
``calculate_next_interval`` import so the harness measures the shipped rules.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Callable

from services.api.storage.spaced_repetition import calculate_next_interval

# --- Fixed configuration -------------------------------------------------

DEFAULT_SEED = 20260720
TARGET_RETENTION = 0.9  # desired recall probability at the moment an item is due

# Production ease bounds (mirrored here only for the bounds test / reporting).
EASE_MIN = 1.3
EASE_MAX = 2.8


# =========================================================================
# Ground-truth memory model (the synthetic learner)
# =========================================================================


@dataclass
class MemoryState:
    """Latent per-(learner, puzzle) memory under the ground-truth model.

    Recall probability ``t`` days after the last review follows a half-life
    forgetting curve ``p(t) = 2 ** (-t / half_life)``. ``half_life`` is measured
    in days; ``difficulty`` in [0, 1] slows stability growth and lowers the
    stability an item relearns to after a lapse.
    """

    half_life: float
    difficulty: float

    def recall_prob(self, elapsed_days: float) -> float:
        hl = max(self.half_life, 0.1)
        return 2.0 ** (-elapsed_days / hl)

    def after_pass(self, recall_prob_at_review: float) -> "MemoryState":
        """Successful recall grows stability (spacing / desirable difficulty).

        The gain is larger for easy items and larger when the recall was
        *effortful* (low ``recall_prob_at_review``) — the well-documented
        desirable-difficulty effect. Growth is bounded so stability cannot
        blow up in a single step.
        """
        d = self.difficulty
        effort_bonus = 0.5 + 0.5 * (1.0 - recall_prob_at_review)  # in [0.5, 1.0]
        growth = 1.0 + (GT_MAX_GROWTH - 1.0) * (1.0 - d) * effort_bonus
        return replace(self, half_life=self.half_life * growth)

    def after_fail(self) -> "MemoryState":
        """A lapse resets stability toward a small relearn floor.

        Harder items relearn to a lower stability than easy ones.
        """
        relearn = GT_RELEARN_HALF_LIFE * (1.0 - 0.5 * self.difficulty)
        return replace(self, half_life=max(relearn, 0.5))


GT_INITIAL_HALF_LIFE = 1.0  # days, right after the first (learning) exposure
GT_RELEARN_HALF_LIFE = 1.0  # days, floor an item relearns to after a lapse
GT_MAX_GROWTH = 3.0  # max single-step stability multiplier (easy, effortful)


def _make_memory(rng: random.Random) -> MemoryState:
    """Draw a fresh ground-truth item.

    Difficulty combines an intrinsic per-item component with the learner's
    ability (able learners find items effectively easier).
    """
    intrinsic = rng.uniform(0.1, 0.9)
    ability = rng.uniform(0.0, 0.5)  # 0 = weak learner, 0.5 = strong
    difficulty = max(0.05, min(0.95, intrinsic * (1.0 - ability)))
    return MemoryState(half_life=GT_INITIAL_HALF_LIFE, difficulty=difficulty)


# =========================================================================
# Schedulers under test
# =========================================================================
#
# A scheduler is a pair of pure functions over an opaque per-item state:
#   init() -> state
#   step(state, passed) -> (interval_days, new_state)
# ``interval_days`` is the number of days until the item is next due.


@dataclass(frozen=True)
class Scheduler:
    name: str
    init: Callable[[], object]
    step: Callable[[object, bool], tuple[int, object]]


# --- (a) CURRENT production scheduler (SM2-ish) --------------------------
#
# Wraps the genuine ``calculate_next_interval``. State is (interval|None, ease).
# Rules (from the shipped code): FAIL -> interval 1, ease-0.2 (>=1.3);
# PASS -> None->1, 1->3, else round(interval*ease); ease+0.05 (<=2.8).


@dataclass
class _CurrentState:
    interval: int | None = None
    ease: float = 2.0


def _current_init() -> _CurrentState:
    return _CurrentState()


def _current_step(state: _CurrentState, passed: bool) -> tuple[int, _CurrentState]:
    result = "pass" if passed else "fail"
    interval, ease = calculate_next_interval(state.interval, state.ease, result)
    return interval, _CurrentState(interval=interval, ease=ease)


CURRENT = Scheduler("current", _current_init, _current_step)


# --- (b) Lightly-tuned SM2 variant --------------------------------------
#
# The same SM2 skeleton with three small, documented tweaks drawn from Anki's
# defaults (https://docs.ankiweb.net/deck-options.html):
#   * start ease at 2.5 (Anki default) rather than 2.0;
#   * graduate 1 -> 6 days instead of 1 -> 3 (Anki "easy graduating" step);
#   * a lapse *multiplies* the interval by 0.5 (min 1) instead of hard-resetting
#     to 1, so a single slip on a well-known item is not fully punished.
# Ease bounds are kept identical to production.

TUNED_START_EASE = 2.5
TUNED_GRADUATE_INTERVAL = 6
TUNED_LAPSE_MULT = 0.5


@dataclass
class _TunedState:
    interval: int | None = None
    ease: float = TUNED_START_EASE


def _tuned_init() -> _TunedState:
    return _TunedState()


def _tuned_step(state: _TunedState, passed: bool) -> tuple[int, _TunedState]:
    if not passed:
        if state.interval is None:
            new_interval = 1
        else:
            new_interval = max(1, round(state.interval * TUNED_LAPSE_MULT))
        new_ease = max(EASE_MIN, state.ease - 0.2)
    else:
        if state.interval is None:
            new_interval = 1
        elif state.interval == 1:
            new_interval = TUNED_GRADUATE_INTERVAL
        else:
            new_interval = round(state.interval * state.ease)
        new_ease = min(EASE_MAX, state.ease + 0.05)
    return new_interval, _TunedState(interval=new_interval, ease=new_ease)


TUNED = Scheduler("tuned_sm2", _tuned_init, _tuned_step)


# --- (c) Target-retention (FSRS / half-life-regression style) ------------
#
# Instead of a fixed interval ladder, track an *estimated* stability and pick
# the interval that lands predicted recall on a target R. Under a half-life
# recall model p = 2**(-t/S), solving p = R gives t = -S * log2(R).
#
# The estimator is intentionally misspecified vs the ground truth: it does NOT
# know per-item difficulty, so it uses a fixed multiplicative growth on pass and
# resets to a floor on lapse. This mirrors the core idea behind FSRS
# (github.com/open-spaced-repetition/fsrs4anki) and Duolingo's Half-Life
# Regression (Settles & Meeder, ACL 2016) without their trained parameters.

TR_START_STABILITY = 1.0
TR_PASS_GROWTH = 1.9
TR_LAPSE_STABILITY = 1.0


@dataclass
class _TargetState:
    stability: float = TR_START_STABILITY
    target: float = TARGET_RETENTION


def _target_init() -> _TargetState:
    return _TargetState()


def _target_step(state: _TargetState, passed: bool) -> tuple[int, _TargetState]:
    if passed:
        stability = state.stability * TR_PASS_GROWTH
    else:
        stability = TR_LAPSE_STABILITY
    interval_real = stability * (-math.log2(state.target))
    interval = max(1, round(interval_real))
    return interval, _TargetState(stability=stability, target=state.target)


def make_target_scheduler(target: float = TARGET_RETENTION) -> Scheduler:
    def init() -> _TargetState:
        return _TargetState(target=target)

    return Scheduler("target_retention", init, _target_step)


TARGET = make_target_scheduler()


ALL_SCHEDULERS: tuple[Scheduler, ...] = (CURRENT, TUNED, TARGET)


# =========================================================================
# Simulation
# =========================================================================


@dataclass
class ReviewSample:
    """One due-review event under a given scheduler."""

    recall_prob: float  # true recall prob at the chosen interval (calibration)
    passed: bool  # drawn outcome
    interval_days: int


@dataclass
class SchedulerRun:
    name: str
    samples: list[ReviewSample] = field(default_factory=list)
    end_retention: list[float] = field(default_factory=list)  # per item, at horizon
    total_reviews: int = 0
    n_items: int = 0
    horizon_days: int = 0


def simulate_one(
    scheduler: Scheduler,
    memory: MemoryState,
    outcomes: list[bool],
    horizon_days: int,
) -> tuple[list[ReviewSample], float, int]:
    """Replay a single item through one scheduler.

    ``outcomes`` is a pre-drawn list of "would the learner recall it" coin
    flips (thresholded against the true recall prob inside the loop). Sharing
    the *same* coin flips across schedulers is not possible because the review
    *timing* differs, so instead we pass an ``outcomes`` deck of uniform draws
    and threshold per event — see :func:`run` for how the deck is built.

    Returns (samples, end_retention, review_count).
    """
    day = 0
    last_review_day = 0
    state = scheduler.init()
    mem = memory
    samples: list[ReviewSample] = []
    reviews = 0

    # Day 0 is the initial learning exposure (treated as a pass); it sets the
    # first interval but is not itself scored as a due-review sample.
    interval, state = scheduler.step(state, True)

    deck = iter(outcomes)
    while True:
        day += interval
        if day > horizon_days:
            break
        elapsed = day - last_review_day
        p = mem.recall_prob(elapsed)
        try:
            u = next(deck)
        except StopIteration:  # deck exhausted -> stop this item
            break
        passed = u < p
        samples.append(
            ReviewSample(recall_prob=p, passed=passed, interval_days=interval)
        )
        reviews += 1
        mem = mem.after_pass(p) if passed else mem.after_fail()
        interval, state = scheduler.step(state, passed)
        last_review_day = day

    end_retention = mem.recall_prob(horizon_days - last_review_day)
    return samples, end_retention, reviews


def run(
    *,
    seed: int = DEFAULT_SEED,
    n_learners: int = 30,
    n_puzzles: int = 40,
    horizon_days: int = 365,
    schedulers: tuple[Scheduler, ...] = ALL_SCHEDULERS,
) -> dict[str, SchedulerRun]:
    """Run the full evaluation deterministically.

    For every (learner, puzzle) pair we draw one ground-truth memory and one
    fixed "deck" of uniform(0,1) draws. Each scheduler replays the *same* pair
    (same memory, same deck) so differences come only from scheduling policy,
    not from luck. Because timing differs per scheduler, the deck is consumed
    event-by-event and thresholded against the true recall prob at that event.
    """
    rng = random.Random(seed)
    runs = {
        s.name: SchedulerRun(name=s.name, horizon_days=horizon_days) for s in schedulers
    }

    n_pairs = n_learners * n_puzzles
    # Upper bound on due-events per item is horizon (min interval is 1 day).
    deck_len = horizon_days + 1

    for _ in range(n_pairs):
        memory = _make_memory(rng)
        deck = [rng.random() for _ in range(deck_len)]
        for sched in schedulers:
            samples, end_ret, reviews = simulate_one(sched, memory, deck, horizon_days)
            r = runs[sched.name]
            r.samples.extend(samples)
            r.end_retention.append(end_ret)
            r.total_reviews += reviews
            r.n_items += 1

    return runs


# =========================================================================
# Metrics
# =========================================================================


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolation quantile (q in [0, 1]). Hand-checkable.

    For q=0.5 this matches ``statistics.median`` for the cases used in tests.
    """
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def summarize(run: SchedulerRun, target: float = TARGET_RETENTION) -> dict[str, float]:
    """Compute the comparison metrics for one scheduler run.

    Metrics (all "at due" means at the interval the scheduler chose):
      * ``median_recall_at_due`` / IQR: interval calibration — true recall prob
        when the item comes due. A well-calibrated scheduler holds this near the
        target.
      * ``frac_remembered_at_due``: fraction of due-reviews actually recalled.
      * ``median_calibration_error``: median |recall_at_due - target|; lower is
        better (predicted "due" aligns with the target-retention crossing).
      * ``reviews_per_item_per_year``: workload to sustain the horizon.
      * ``median_end_retention``: true recall across items at the horizon end.
    """
    recalls = [s.recall_prob for s in run.samples]
    passed = [1.0 if s.passed else 0.0 for s in run.samples]
    cal_err = [abs(s.recall_prob - target) for s in run.samples]
    per_item_year = (
        run.total_reviews / run.n_items * (365.0 / run.horizon_days)
        if run.n_items
        else float("nan")
    )
    return {
        "n_reviews": float(len(run.samples)),
        "median_recall_at_due": _quantile(recalls, 0.5),
        "recall_p25": _quantile(recalls, 0.25),
        "recall_p75": _quantile(recalls, 0.75),
        "frac_remembered_at_due": (
            (sum(passed) / len(passed)) if passed else float("nan")
        ),
        "median_calibration_error": _quantile(cal_err, 0.5),
        "reviews_per_item_per_year": per_item_year,
        "median_end_retention": _quantile(run.end_retention, 0.5),
    }


def evaluate(**kwargs) -> dict[str, dict[str, float]]:
    """Run the simulation and return {scheduler_name: metrics}. Deterministic."""
    target = kwargs.pop("target", TARGET_RETENTION)
    runs = run(**kwargs)
    return {name: summarize(r, target=target) for name, r in runs.items()}


# =========================================================================
# CLI reporting
# =========================================================================

_COLUMNS = [
    ("median_recall_at_due", "recall@due (med)"),
    ("recall_p25", "recall@due p25"),
    ("recall_p75", "recall@due p75"),
    ("frac_remembered_at_due", "remembered@due"),
    ("median_calibration_error", "calib err (med)"),
    ("reviews_per_item_per_year", "reviews/item/yr"),
    ("median_end_retention", "end retention"),
]


def format_table(results: dict[str, dict[str, float]], target: float) -> str:
    lines = []
    lines.append(f"Target retention: {target:.2f}")
    header = f"{'metric':<24}" + "".join(f"{name:>18}" for name in results)
    lines.append(header)
    lines.append("-" * len(header))
    for key, label in _COLUMNS:
        row = f"{label:<24}"
        for name in results:
            row += f"{results[name][key]:>18.3f}"
        lines.append(row)
    return "\n".join(lines)


def main() -> None:
    results = evaluate()
    print(format_table(results, TARGET_RETENTION))


if __name__ == "__main__":
    main()
