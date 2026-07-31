"""What to train today, and the honest reason for it.

The Insights page already tells a user *what* their habits are. This module
answers the next question — "so what do I do about it?" — by picking one
pattern to work on and saying why it was picked.

Three rules keep the recommendation honest:

1. **It recommends only what it can justify.** A pattern that has not cleared
   the ranking threshold is not a tendency yet (see ``analytics_confidence``),
   and a recommendation built on it would be a guess wearing a plan's clothes.
   When nothing qualifies, this returns None rather than the least-bad option.

2. **It never invents work.** The focus is chosen from patterns the user
   actually has, and the session that acts on it re-orders the *already
   trainable* puzzles rather than pulling in extras. A focus cannot make a
   not-yet-due puzzle due — see ``get_adaptive_puzzles``.

3. **It says what would change its mind.** ``rationale`` names the numbers the
   choice rests on, so the user can disagree with it on the evidence rather
   than on vibes.
"""

from dataclasses import dataclass

from services.api.diagnosis.patterns import PatternIdentity, identify, priority_score
from services.api.storage.diagnosis_repository import CauseStat


@dataclass(frozen=True)
class TrainingFocus:
    """The one habit worth working on right now.

    ``cause`` is what the training session filters on; ``name`` and
    ``description`` are what the user reads. ``runner_up`` exists so the card
    can say "and after that…" without a second round trip, and so a focus that
    barely won does not look like a landslide.
    """

    cause: str
    name: str
    description: str
    mistakes: int
    recent_mistakes: int
    accuracy: float | None
    priority: float
    rationale: str
    runner_up: str | None = None


def _rationale(stat: CauseStat, identity: PatternIdentity) -> str:
    """Why this pattern, in the user's terms.

    Deliberately assembled from the same fields the score is computed from. A
    rationale written independently of the score is free to flatter it, and
    would drift the first time the scoring changed.
    """
    parts = [f"{stat.mistakes} diagnosed mistakes"]

    if stat.recent_mistakes > 0:
        parts.append(f"{stat.recent_mistakes} of them recent")

    if stat.accuracy is not None:
        parts.append(f"{round(stat.accuracy * 100)}% solved when retried")
    else:
        # Absence of a rate is itself informative, and saying nothing would let
        # the user assume one was measured and merely omitted.
        parts.append("not enough verified retries to measure a rate")

    if stat.dominant_phase:
        parts.append(f"mostly in the {stat.dominant_phase}")

    return "; ".join(parts) + "."


def plan_focus(stats: list[CauseStat]) -> TrainingFocus | None:
    """Pick today's focus from a user's cause breakdown, or nothing.

    Returns None — rather than a weak suggestion — when no pattern has come up
    often enough to be called a tendency, or when no qualifying cause has a
    written pattern to point at. "We don't know yet" is a state this product
    already commits to showing.

    Ties break on cause name so the focus is stable between page loads; a
    recommendation that changed on refresh would read as arbitrary, which is
    exactly what it must not be.
    """
    ranked: list[tuple[float, str, CauseStat, PatternIdentity]] = []

    for stat in stats:
        if stat.insufficient_data:
            continue
        identity = identify(stat.cause, stat.dominant_phase)
        if identity is None:
            # Either "unclassified" or a cause with no pattern written for it.
            # Neither is something to hand a user as a training plan.
            continue
        score = priority_score(stat.mistakes, stat.accuracy, stat.recent_mistakes)
        ranked.append((score, stat.cause, stat, identity))

    if not ranked:
        return None

    ranked.sort(key=lambda r: (-r[0], r[1]))
    score, _cause, stat, identity = ranked[0]

    return TrainingFocus(
        cause=stat.cause,
        name=identity.name,
        description=identity.description,
        mistakes=stat.mistakes,
        recent_mistakes=stat.recent_mistakes,
        accuracy=stat.accuracy,
        priority=score,
        rationale=_rationale(stat, identity),
        runner_up=ranked[1][3].name if len(ranked) > 1 else None,
    )
