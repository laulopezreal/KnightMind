# Spaced-repetition scheduler evaluation (SCORECARD dim 16)

**Status:** evidence-gathering analysis. No production behaviour changed by this
work. Any change is proposed as a separate follow-up.

## Why this exists

KnightMind schedules puzzle reviews with an SM2-ish rule in
[`services/api/storage/spaced_repetition.py`](../services/api/storage/spaced_repetition.py)
(`calculate_next_interval`):

- **FAIL** → `interval = 1`, `ease = max(1.3, ease - 0.2)`
- **PASS** → `interval`: `None → 1`, `1 → 3`, else `round(interval * ease)`;
  `ease = min(2.8, ease + 0.05)` (start ease `2.0`, bounds `[1.3, 2.8]`)
- `next_due_at = reviewed_at + interval days`

These rules were never evaluated. Dim 16 asks for an **evidence-based
comparison**, not a rewrite. This document reports one.

## What was built

A deterministic, offline harness:
[`services/api/analysis/scheduler_eval.py`](../services/api/analysis/scheduler_eval.py)
(tests in `services/api/test_scheduler_eval.py`). It touches no database and no
production state. Run it with:

```bash
KNIGHTMIND_DEV_SQLITE=1 python -m services.api.analysis.scheduler_eval
```

### Method

1. **Synthetic learner (ground truth).** Each `(learner, puzzle)` pair gets a
   latent memory with a half-life forgetting curve: recall probability `t` days
   after a review is `p(t) = 2^(-t / S)`, where `S` (the half-life, in days) is
   the memory *stability*. Each item has a `difficulty ∈ [0.05, 0.95]` combining
   an intrinsic component and the learner's ability. On a **pass** stability
   grows (bounded), more for easy items and more when the recall was *effortful*
   — the desirable-difficulty effect. On a **fail** stability resets toward a
   small relearn floor. This is a standard memory-model shape (cf. Duolingo
   Half-Life Regression, Settles & Meeder, ACL 2016; FSRS).

2. **Replay.** Each pair is replayed through three schedulers against the *same*
   ground-truth memory and the *same* fixed deck of uniform random draws, so
   differences come from policy, not luck. At each due event we compute the true
   recall probability at the scheduler's chosen interval, draw pass/fail from it,
   then update both the ground truth and the scheduler's own state.

3. **Determinism.** A single `random.Random(seed)` (fixed seed `20260720`) drives
   every draw; the clock is an integer day counter — no wall-clock time. Same
   seed → identical numbers (locked by a test).

### Schedulers compared

- **`current`** — the shipped rule, exercised through the *real* imported
  `calculate_next_interval` (so the harness measures shipped behaviour).
- **`tuned_sm2`** — same SM2 skeleton with three small Anki-style tweaks: start
  ease `2.5`, graduate `1 → 6` days, and a *soft* lapse (`interval × 0.5`, min 1)
  instead of a hard reset to 1.
- **`target_retention`** — an FSRS / half-life-regression-style policy: track an
  estimated stability and pick the interval that lands predicted recall on a
  target `R` (`interval = Ŝ · (−log₂ R)`, `R = 0.9`). The estimator is
  deliberately *misspecified* — it does **not** know per-item difficulty — which
  stands in for a first deployment without trained parameters.

### Metrics (median + spread, all "at due" = at the chosen interval)

- **recall@due** — true recall probability when an item comes due (interval
  calibration; a well-tuned scheduler holds this near the target).
- **remembered@due** — fraction of due-reviews actually recalled.
- **calibration error** — median `|recall@due − target|` (lower = "due" aligns
  with the target-retention crossing).
- **reviews/item/yr** — workload to sustain the horizon.
- **end retention** — true recall across items at the horizon end.

## Results

Seed `20260720`, 30 learners × 40 puzzles (1,200 pairs), 365-day horizon,
target retention `0.90`:

| metric                     | current | tuned_sm2 | target_retention |
| -------------------------- | ------: | --------: | ---------------: |
| recall@due (median)        |   0.386 |     0.212 |        **0.474** |
| recall@due p25 – p75       | 0.32–0.43 | 0.10–0.41 |    0.37–0.70 |
| remembered@due             |   0.374 |     0.253 |        **0.537** |
| calibration error (median) |   0.514 |     0.688 |        **0.426** |
| reviews / item / yr        | 134.4 |     120.8 |         **84.2** |
| end retention (median)     | **0.942** |   0.684 |            0.910 |

Numbers are stable across seeds `{1, 2, 3, 20260720, 777}` (e.g.
`reviews/item/yr` for `target_retention` ranges 84.2–89.5; `current` 132–137;
medians move in the third decimal). See the harness for reproduction.

### Reading the numbers

- **`target_retention` dominates the two decision-relevant axes.** It sustains
  the horizon at roughly the same end retention as `current` (0.91 vs 0.94)
  while doing **~37% fewer reviews** (84 vs 134 per item-year), and its intervals
  track memory far better (calibration error 0.43 vs 0.51).
- **`tuned_sm2` is worse, not better.** Bundling a longer graduation step with a
  soft lapse over-extends intervals in this model: lowest recall@due, worst
  calibration, and end retention collapses to 0.68. This is a useful negative
  result — the "obvious" Anki-flavoured tweaks do **not** transfer here.
- **`current` over-extends but churns.** Its intervals put recall at due around
  0.39 — well under the 0.90 target — yet it still costs the *most* reviews,
  because every lapse hard-resets the interval to 1 and the item re-climbs the
  ladder from scratch. High end retention (0.94) is partly an artifact: short
  post-lapse intervals mean the last review usually sits close to the horizon.

## Honesty caveats (please read before acting)

1. **This is a model, not real data.** The forgetting curve, the
   desirable-difficulty growth rule, and the difficulty distribution are
   assumptions. The **absolute** recall numbers are pessimistic (even the
   retention-targeting scheduler lands at ~0.47 median recall@due, because its
   estimator can't see per-item difficulty). Trust the **ordering and the
   relative gaps**, not the absolute levels.
2. **The calibration metric partly favours the retention-targeting family.** A
   scheduler that explicitly aims for retention `R` is structurally advantaged on
   a metric that rewards landing near `R`. The robust, non-circular claim is the
   **workload reduction at equal-or-better end retention**, not the calibration
   delta alone.
3. **One ground-truth shape.** Only a half-life exponential curve with one growth
   rule was tested. A power-law forgetting curve or a different lapse model could
   shift the gaps.

## Recommendation

**Keep the current scheduler in place for now. Do not adopt the `tuned_sm2`
tweaks — the evidence says they make retention worse.**

The one policy that improves on `current` under this model is
**target-retention (FSRS/HLR-style) scheduling**, which cut simulated workload
~37% at comparable end retention. That is enough to justify a *follow-up
pilot*, but not enough to ship blind, because (a) it needs a per-item stability
estimate the current schema doesn't track, and (b) the win should be confirmed
on real review logs before any user-facing change.

**Smallest safe first step (for a separate PR, not implemented here):** if a
minimal change is wanted *within the current architecture*, the highest-leverage
single tweak is to **soften the FAIL branch** — replace the hard
`interval = 1` reset with `interval = max(1, round(interval * 0.5))` while
leaving the PASS ladder and ease bounds untouched. In this model the hard reset
is the dominant workload driver (churn after lapses). This is one line in
`calculate_next_interval`, is trivially reversible, and keeps all existing
bounds. It should still be gated behind the real-data replay below, and shipped
behind a flag with an A/B on due-count and pass-rate.

> Note: the bundled `tuned_sm2` variant *also* softened the lapse but paired it
> with a `1 → 6` graduation that over-extended intervals, so its poor result does
> **not** condemn the lapse change in isolation — that is exactly the kind of
> single-variable question the real-data replay should settle.

## What a real-data follow-up would add

- **Replace the synthetic learner with real review sequences** from
  `PuzzleReview` (timestamps + pass/fail per user/puzzle). Fit each item's
  forgetting curve from actual recall-vs-elapsed data instead of assuming one.
- **Backtest on held-out reviews:** for each real review, ask each scheduler what
  interval it *would* have chosen and compare predicted recall to the observed
  outcome (log-loss / calibration curve). This removes caveats (1) and (2) —
  the ground truth is then observed, not modelled.
- **Estimate the real forgetting rate**, which fixes the absolute-level
  uncertainty and tells us whether `current`'s intervals genuinely overshoot.
- **Measure true workload and retention** per policy on the real population,
  including cold-start (new items) and heavy-lapse learners, before any rollout.
