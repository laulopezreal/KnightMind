"""Concept-level grouping of puzzles that share a weakness.

A cluster answers "what else did I get wrong for *this* reason", which is a
different question from "what should I train next" — the queue already answers
that from tier, due date and focus.

**Derived, not stored.** The plan this implements called for a persisted
``similarity_cluster_id``, written when the diagnosis is written. That design
predates ``user_confirmed_cause``: a user can relabel a puzzle's cause at any
time via ``/puzzles/{id}/diagnosis/confirm``, and every read in the codebase
coalesces the correction over the computed value. A cluster id frozen at
diagnosis time would be silently wrong from the moment someone disagreed with
the classifier — exactly the users whose feedback is most worth honouring. The
key is a pure function of fields already on the row, so deriving it at query
time cannot drift, needs no migration, and no backfill.

**Tiered, because the corpus is small.** An exact match on cause + motif +
phase is the most meaningful grouping, but on a few dozen puzzles it is often
empty, and an empty "more like this" section teaches nothing. So matching
widens in defined steps and the caller is told which step answered, rather
than being handed a loose match dressed up as a tight one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Human labels for the phase values the diagnosis layer records. Anything
# unrecognised falls back to the raw value rather than being dropped, so a new
# phase shows up as itself instead of silently vanishing from the sentence.
_PHASE_LABEL = {
    "opening": "the opening",
    "middlegame": "the middlegame",
    "endgame": "the endgame",
}


class MatchTier(str, Enum):
    """How closely a sibling puzzle matches, widest last.

    Ordered by how much the match actually tells the user. ``CAUSE_ONLY`` still
    earns its place: two puzzles sharing only ``calculation_stopped_early`` are
    genuinely the same blindspot even when the tactic and phase differ.
    """

    EXACT = "exact"
    CAUSE_AND_MOTIF = "cause_and_motif"
    CAUSE_ONLY = "cause_only"


@dataclass(frozen=True)
class ClusterKey:
    """The weakness coordinates of one diagnosed puzzle.

    ``cause`` is the only required leg. A puzzle with no cause has no weakness
    to group by, and pretending otherwise would cluster every unclassified
    mistake into one meaningless bucket.
    """

    cause: str
    motif: str | None = None
    phase: str | None = None

    @property
    def is_groupable(self) -> bool:
        return bool(self.cause)


def key_for(
    cause: str | None, motif: str | None, phase: str | None
) -> ClusterKey | None:
    """Build a cluster key, or None when there is nothing to group by.

    Callers pass the *effective* cause — the user's correction where one
    exists, the computed cause otherwise — because that is what every other
    cause-facing surface uses, and a cluster that disagreed with Insights would
    just look broken.
    """
    if not cause:
        return None
    return ClusterKey(cause=cause, motif=motif or None, phase=phase or None)


def tiers_for(key: ClusterKey) -> list[MatchTier]:
    """The match tiers worth trying for this key, tightest first.

    Tiers that would restate a narrower one are skipped: with no motif
    recorded, ``CAUSE_AND_MOTIF`` is the same query as ``CAUSE_ONLY``, and
    running both would report a wide match as though it were a narrow one.
    """
    tiers: list[MatchTier] = []
    if key.motif and key.phase:
        tiers.append(MatchTier.EXACT)
    if key.motif:
        tiers.append(MatchTier.CAUSE_AND_MOTIF)
    tiers.append(MatchTier.CAUSE_ONLY)
    return tiers


def humanise_cause(cause: str) -> str:
    """Turn a taxonomy slug into something readable in a sentence.

    The taxonomy is written for the classifier (``calculation_stopped_early``),
    not for a person reading a card, and the UI should not have to own that
    translation in two places.
    """
    return cause.replace("_", " ").strip().lower()


def describe(key: ClusterKey, tier: MatchTier) -> str:
    """One sentence saying why these puzzles are grouped together.

    Phrased as what the puzzles have in common, not as advice. The card states
    the shared weakness; deciding what to do about it belongs to the training
    surfaces, which have the scheduling context this module does not.
    """
    cause = humanise_cause(key.cause)
    if tier is MatchTier.EXACT:
        phase = _PHASE_LABEL.get(key.phase or "", key.phase or "")
        return (
            f"Same mistake — {cause} — on a {key.motif} in {phase}."
            if phase
            else f"Same mistake — {cause} — on a {key.motif}."
        )
    if tier is MatchTier.CAUSE_AND_MOTIF:
        return f"Same mistake — {cause} — on a {key.motif}."
    return f"Same mistake — {cause} — in a different kind of position."
