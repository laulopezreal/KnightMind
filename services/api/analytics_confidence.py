"""
Central uncertainty contract for read-side analytics.

One place to define "how much data is enough" before a computed delta may be
presented as a directional trend, and a shared confidence label. Keeping these
thresholds together stops each analytics endpoint from inventing its own
silent, undocumented minimum.

Descriptive vs causal
---------------------
All analytics metrics here are **descriptive** summaries of observed results.
They are not causal claims. Callers must never phrase a below-threshold delta
as a cause ("drove the drop", "offset losses") — below the minimum sample the
honest signal is ``insufficient_data``.
"""

from functools import partial
from typing import Literal

from services.api.envutil import env_int

Confidence = Literal["low", "medium", "high"]

# Thresholds must be positive: a typo in deployment config can never silently
# disable a gate (a threshold of 0 would let a single noisy observation
# through). Blank, unset, non-integer, or non-positive values fall back to the
# default, so the minimum-sample thresholds below can be tuned against real
# data without a code change while keeping behaviour identical when unset.
_env_threshold = partial(env_int, min_value=1)


# Minimum observations before a direction (up/down) may be reported instead of
# "steady". Below these, callers set insufficient_data=True and stay neutral.
#
# Each threshold is env-overridable (tune against real data without a deploy);
# the default is the sample size at which the metric stops being noise-dominated.
# The defaults are deliberate product-data choices — override to tune, but the
# baselines are chosen for the rationale documented per line below.

# Recent-form trend splits the last N reviews in half and compares pass-rates.
# Below 8 each half is <=3 reviews, where one pass/fail flip moves the half's
# rate by >=0.33 — a swing indistinguishable from noise, so no direction shown.
MIN_REVIEWS_FOR_FORM_TREND = _env_threshold("ANALYTICS_MIN_REVIEWS_FORM_TREND", 8)

# Per-motif trend compares first vs last accuracy across the window. Motif
# samples are thinner than overall form, so we require the same 8 reviews before
# a start-to-end delta is reported as up/down rather than a two-attempt artefact.
MIN_REVIEWS_FOR_MOTIF_TREND = _env_threshold("ANALYTICS_MIN_REVIEWS_MOTIF_TREND", 8)

# Motif accuracy rank (pass/attempts). At 5 attempts the 95% Wilson interval on
# a proportion is still ~+/-0.35 wide, so below 5 a rank label would mostly
# reflect sampling luck; 5 is the floor where a rank carries any signal.
MIN_ATTEMPTS_FOR_MOTIF_RANK = _env_threshold("ANALYTICS_MIN_ATTEMPTS_MOTIF_RANK", 5)

# A cause is only presented as a ranked weakness once it has been diagnosed at
# least this many times. Below it, one bad afternoon looks identical to a
# habit — and "you have a loose-piece problem" said on the strength of a single
# game is the kind of confident-but-unfounded claim this codebase exists to
# avoid. Four is the point where a repeat stops being coincidence.
MIN_DIAGNOSES_FOR_CAUSE_RANK = _env_threshold("ANALYTICS_MIN_DIAGNOSES_CAUSE_RANK", 4)

# Distinct puzzles that must have been attempted before a per-cause pass rate
# is reported. Guards a failure mode the attempt count alone cannot see:
# repeated attempts on a single puzzle are not independent observations.
# Solving the same position six times shows you remember that position, not
# that you have stopped making the mistake — so a rate drawn from one puzzle
# would read as competence at the cause. Two is the smallest sample that is
# not that.
MIN_PUZZLES_FOR_CAUSE_ACCURACY = _env_threshold("ANALYTICS_MIN_PUZZLES_CAUSE_ACC", 2)

# Rating-driver attribution needs enough rated games that the Elo delta vs the
# expected score is not dominated by per-game variance (a single game swings
# rating by ~10-30 pts). Below 5 rated games we stay descriptive and neutral.
MIN_GAMES_FOR_RATING_DRIVERS = _env_threshold("ANALYTICS_MIN_GAMES_RATING_DRIVERS", 5)

# Confidence-label breakpoints for the rating-explain window (rated games).
# Mirrors the values the frontend badge used so the server is now canonical.
RATING_LOW_CONFIDENCE_BELOW = 10
RATING_HIGH_CONFIDENCE_AT = 20


def rating_confidence(rated_games: int) -> Confidence:
    """Confidence label for a rating-explain window from its rated-game count."""
    if rated_games < RATING_LOW_CONFIDENCE_BELOW:
        return "low"
    if rated_games < RATING_HIGH_CONFIDENCE_AT:
        return "medium"
    return "high"
