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

from services.api.diagnosis.causes import CAUSE_LABELS, UNCLASSIFIED

# Human labels for the phase values the diagnosis layer records. Anything
# unrecognised falls back to the raw value rather than being dropped, so a new
# phase shows up as itself instead of silently vanishing from the sentence.
_PHASE_LABEL = {
    "opening": "the opening",
    "middlegame": "the middlegame",
    "endgame": "the endgame",
}


# Motif values that carry no tactical information. ``assign_primary_motif``
# returns "blunder" when no specific motif can be identified, so it is the
# unknown sentinel wearing a motif's clothes — on the live corpus it is 45% of
# diagnoses (65% of puzzle_stats). Matching two puzzles because both say
# "blunder" would report a tactical match that was never made, and produce the
# sentence "…on a blunder in the middlegame."
_NON_MOTIFS = frozenset({"blunder"})


class MatchTier(str, Enum):
    """How closely a sibling puzzle matches, widest last.

    Ordered by how much the match actually tells the user. ``CAUSE_ONLY`` still
    earns its place: two puzzles sharing only ``calculation_stopped_early`` are
    genuinely the same blindspot even when the tactic and phase differ.

    ``CAUSE_AND_PHASE`` exists because phase is recorded on every diagnosed
    puzzle while a real motif is recorded on about a quarter of them. Without
    it, the majority with no usable motif fell straight to ``CAUSE_ONLY`` and
    were told their siblings came from "a different kind of position" — when in
    fact they shared the phase, and nothing had checked whether the position
    differed at all.
    """

    EXACT = "exact"
    CAUSE_AND_MOTIF = "cause_and_motif"
    CAUSE_AND_PHASE = "cause_and_phase"
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


def usable_motif(motif: str | None) -> str | None:
    """The motif if it says something, else None.

    ``assign_primary_motif`` returns "blunder" when no tactic is identified, so
    a row saying "blunder" is a row with no motif — 45% of diagnoses, 65% of
    ``puzzle_stats``. Shared with the API layer so the sentence and the row
    beneath it cannot disagree about whether a motif exists: the reason line
    omitting the motif while the card tagged every sibling "Blunder" was
    exactly that disagreement.

    Case-insensitive on the check, case-preserving on the value — the motif is
    used verbatim as a SQL equality predicate elsewhere.
    """
    if not motif or motif.strip().lower() in _NON_MOTIFS:
        return None
    return motif


def key_for(
    cause: str | None, motif: str | None, phase: str | None
) -> ClusterKey | None:
    """Build a cluster key, or None when there is nothing to group by.

    Callers pass the *effective* cause — the user's correction where one
    exists, the computed cause otherwise — because that is what every other
    cause-facing surface uses, and a cluster that disagreed with Insights would
    just look broken.

    ``unclassified`` is not a weakness, it is the classifier declining to name
    one, and every other surface excludes it (``patterns.identify`` returns
    None for it; the mistake-causes endpoint skips it). Grouping by it would
    collect every unexplained mistake into one bucket and call it a shared
    weakness — the meaningless bucket this module exists to avoid.
    """
    if not cause or cause == UNCLASSIFIED:
        return None
    return ClusterKey(cause=cause, motif=usable_motif(motif), phase=phase or None)


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
    if key.phase:
        tiers.append(MatchTier.CAUSE_AND_PHASE)
    tiers.append(MatchTier.CAUSE_ONLY)
    return tiers


def humanise_cause(cause: str) -> str:
    """The user-facing name for a cause.

    Defers to ``CAUSE_LABELS``, which every other cause surface already uses —
    including the diagnosis card that renders directly above this one on the
    puzzle detail page. Owning a second translation here put "Loose piece
    awareness" and "loose piece awareness" on the same screen.

    The slug fallback is for a cause the label map has not caught up with, so a
    new taxonomy entry degrades to readable words rather than raw snake_case.
    """
    return CAUSE_LABELS.get(cause, cause.replace("_", " ").strip())


def humanise_motif(motif: str) -> str:
    """The readable form of a motif key, for use inside a sentence.

    Motif keys are snake_case (``hanging_piece``, ``back_rank``) and were being
    interpolated raw — "on a hanging_piece in the middlegame" — for 37% of the
    anchors that reach a motif tier. Mirrors the frontend's ``formatMotifName``,
    lowercased because this lands mid-sentence rather than as a label.

    Deliberately not ``MOTIF_TITLES``: those are headlines ("The Fork", "Pinned
    and Lost") and would render "on a The Fork".
    """
    return motif.replace("_", " ").strip().lower()


def describe(key: ClusterKey, tier: MatchTier) -> str:
    """One sentence saying why these puzzles are grouped together.

    Phrased as what the puzzles have in common, not as advice. The card states
    the shared weakness; deciding what to do about it belongs to the training
    surfaces, which have the scheduling context this module does not.
    """
    cause = humanise_cause(key.cause)
    motif = humanise_motif(key.motif or "")
    phase = _PHASE_LABEL.get(key.phase or "", key.phase or "")
    if tier is MatchTier.EXACT:
        return f"Same mistake — {cause} — on a {motif} in {phase}."
    if tier is MatchTier.CAUSE_AND_MOTIF:
        return f"Same mistake — {cause} — on a {motif}."
    if tier is MatchTier.CAUSE_AND_PHASE:
        return f"Same mistake — {cause} — in {phase}."
    # Deliberately does not claim the positions *differ*. This tier is reached
    # whenever nothing tighter matched, which includes puzzles whose motif was
    # simply never recorded — asserting a difference nothing checked was the
    # previous wording's error.
    return f"Same mistake — {cause} — across different positions."
