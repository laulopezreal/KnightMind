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

from typing import Literal

Confidence = Literal["low", "medium", "high"]

# Minimum observations before a direction (up/down) may be reported instead of
# "steady". Below these, callers set insufficient_data=True and stay neutral.
MIN_REVIEWS_FOR_FORM_TREND = 8
MIN_REVIEWS_FOR_MOTIF_TREND = 8
MIN_ATTEMPTS_FOR_MOTIF_RANK = 5
MIN_GAMES_FOR_RATING_DRIVERS = 5

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
