"""
Rule-based mistake-cause classification.

Reads an :class:`~services.api.diagnosis.evidence.EvidencePacket` and returns
the causes the evidence actually supports, each with the ids of the facts that
support it. Pure: no database, no network, no model.

Why rules come first
--------------------
This layer, not the AI stage, is what *originates* a cause. The AI stage may
only rank within the candidates produced here and write the prose (see
``docs/mistake-cause-intelligence-plan.md``, decision D1). Three consequences
that are worth stating because they are the point of the design:

* The feature works with no API key, no cost, and no provider dependency.
* Disagreement between these rules and the model is measurable, so a prompt or
  model regression shows up as a number rather than as vibes.
* A cause can always be traced to the facts that produced it.

On ``strength``
---------------
``strength`` is a fixed, hand-assigned prior for how *diagnostic* a pattern is
when it fires. It is **not** a probability, not a calibrated confidence, and
must never be rendered to a user as a percentage. It exists to order
candidates. The user-facing confidence is a separate, later concern that folds
in sample size the way ``analytics_confidence`` does for the read-side
analytics.

Nothing fired
-------------
When no rule fires the assessment is :data:`UNCLASSIFIED` with
``insufficient_evidence=True``. That is a real, honest answer — "we can see the
motif but not a clear cause" — and it is rendered as such rather than being
padded out with the least-bad guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from services.api.diagnosis.evidence import EvidencePacket

# Bumped whenever a rule's predicate or strength changes. Persisted per
# diagnosis so a rule change invalidates cached rows, matching the
# version-folding discipline used by the FEN eval cache and ``evidence_hash``.
RULE_VERSION = 1

UNCLASSIFIED = "unclassified"

CAUSE_LABELS: dict[str, str] = {
    "loose_piece_awareness": "Loose piece awareness",
    "forcing_move_blindness": "Forcing move blindness",
    "quiet_move_blindness": "Quiet move blindness",
    "recapture_assumption": "Recapture assumption",
    "calculation_stopped_early": "Calculation stopped early",
    "king_safety_blindness": "King safety blindness",
    "own_threat_tunnel_vision": "Tunnel vision on your own threat",
    "missed_opponent_resource": "Missed opponent resource",
    "endgame_technique_gap": "Endgame technique gap",
    "opening_pattern_gap": "Opening pattern gap",
    "time_pressure_collapse": "Time pressure collapse",
    UNCLASSIFIED: "Cause unclear",
}

# Causes that describe the *conditions* a mistake was made under rather than
# the misjudgement itself. They may accompany a cause but can never be the
# headline: "you were low on time" explains nothing about what was missed, and
# promoting it would let every scramble absorb a real, fixable blind spot.
MODULATOR_CAUSES = frozenset({"time_pressure_collapse"})

# Taxonomy entries from the plan that are deliberately NOT implemented yet,
# because the evidence layer cannot support them honestly:
#
#   alignment_blindness          needs piece-geometry facts (majors sharing a
#                                rank/file/diagonal with the king)
#   pawn_structure_misunderstanding
#                                needs pawn-structure evaluation (chains,
#                                backward/isolated pawns, colour complexes)
#
# Shipping them as weak guesses would put unfalsifiable labels in front of the
# user, which is the exact failure this whole design exists to avoid.
DEFERRED_CAUSES = frozenset({"alignment_blindness", "pawn_structure_misunderstanding"})

# A candidate below this adds noise rather than insight, so it is dropped
# rather than listed.
_SECONDARY_FLOOR = 0.4
_MAX_SECONDARY = 3


@dataclass(frozen=True)
class CauseCandidate:
    """One cause the evidence supports, with the facts that support it.

    ``evidence_ids`` must all exist in ``to_evidence_items(packet)``. That is
    enforced by test rather than by convention: a rule citing a fact the packet
    does not carry would produce an explanation the user cannot check.
    """

    cause: str
    strength: float
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CauseAssessment:
    primary_cause: str
    secondary_causes: tuple[str, ...]
    candidates: tuple[CauseCandidate, ...]
    insufficient_evidence: bool
    rule_version: int = RULE_VERSION

    @property
    def supported_causes(self) -> frozenset[str]:
        """The set the AI stage may choose from. Anything else is a rejection."""
        return frozenset(c.cause for c in self.candidates)

    def evidence_ids_for(self, cause: str) -> tuple[str, ...]:
        for candidate in self.candidates:
            if candidate.cause == cause:
                return candidate.evidence_ids
        return ()


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _loose_piece_awareness(packet: EvidencePacket) -> CauseCandidate | None:
    """Two flavours of the same blind spot: undefended pieces went unscanned.

    The strong form is a missed punishment — the solution attacked two loose
    pieces at once. The weaker form is self-inflicted: the user's *own* pieces
    were loose, which is what let the refutation work.
    """
    attacks = packet.best.attacks
    if attacks.loose_target_count >= 2:
        ids = ["best.loose_targets", "best.attacks"]
        if attacks.is_fork:
            ids.append("best.is_fork")
        return CauseCandidate("loose_piece_awareness", 0.9, tuple(ids))

    if packet.best.captures_undefended and packet.best.move.captured_value >= 3:
        # "It was hanging and you didn't take it" — the loudest form, and the
        # one the motif layer already calls hanging_piece/hanging_queen.
        return CauseCandidate(
            "loose_piece_awareness",
            0.8,
            ("best.captures_undefended", "loose.opponent"),
        )

    if attacks.loose_target_count == 1 and attacks.is_fork:
        return CauseCandidate(
            "loose_piece_awareness", 0.75, ("best.loose_targets", "best.is_fork")
        )

    if packet.loose.own_count >= 2 and packet.threats.opponent_forcing_replies >= 3:
        return CauseCandidate(
            "loose_piece_awareness",
            0.6,
            ("loose.own", "loose.own_value", "threats.opponent_forcing_replies"),
        )
    return None


def _forcing_move_blindness(packet: EvidencePacket) -> CauseCandidate | None:
    """The solution was a check or a capture; the user played something quiet."""
    if not (packet.played.move.is_quiet and not packet.best.move.is_quiet):
        return None
    ids = ["best.move", "played.move"]
    # Cite the counter that actually corresponds to the missed move type —
    # quoting "0 checks available" under a missed capture would be noise.
    if packet.best.move.is_check:
        ids.append("threats.legal_checks")
    if packet.best.move.is_capture:
        ids.append("threats.legal_captures")
    # Deliberately below the specific rules. "You missed a forcing move" is
    # true of most tactical misses, so when a sharper cause also fires — the
    # piece was hanging, the solution forked two loose pieces — that one is
    # the more useful headline and must outrank this.
    return CauseCandidate("forcing_move_blindness", 0.65, tuple(ids))


def _quiet_move_blindness(packet: EvidencePacket) -> CauseCandidate | None:
    """The inverse: the user reached for a check or capture; the answer was quiet."""
    if packet.best.move.is_quiet and not packet.played.move.is_quiet:
        return CauseCandidate("quiet_move_blindness", 0.7, ("best.move", "played.move"))
    return None


def _recapture_assumption(packet: EvidencePacket) -> CauseCandidate | None:
    """Recapturing because a capture happened, not because it was best."""
    if not packet.played.is_recapture:
        return None
    if packet.best.is_zwischenzug_like:
        return CauseCandidate(
            "recapture_assumption",
            0.8,
            ("played.is_recapture", "best.is_zwischenzug_like", "best.move"),
        )
    return CauseCandidate(
        "recapture_assumption", 0.6, ("played.is_recapture", "best.move")
    )


def _calculation_stopped_early(packet: EvidencePacket) -> CauseCandidate | None:
    """The solution needed a line, not a move."""
    if packet.best.pv_length >= 5:
        return CauseCandidate(
            "calculation_stopped_early", 0.6, ("best.pv_length", "best.move")
        )
    if packet.best.pv_length >= 3:
        return CauseCandidate(
            "calculation_stopped_early", 0.5, ("best.pv_length", "best.move")
        )
    return None


def _king_safety_blindness(packet: EvidencePacket) -> CauseCandidate | None:
    if packet.king.ring_attackers >= 2:
        return CauseCandidate(
            "king_safety_blindness",
            0.65,
            ("king.ring_attackers", "threats.opponent_forcing_replies"),
        )
    if packet.king.back_rank_boxed and packet.threats.opponent_forcing_replies >= 1:
        return CauseCandidate(
            "king_safety_blindness",
            0.6,
            ("king.back_rank_boxed", "threats.opponent_forcing_replies"),
        )
    return None


def _own_threat_tunnel_vision(packet: EvidencePacket) -> CauseCandidate | None:
    """They were pursuing their own idea and stopped looking at the opponent's."""
    if (
        packet.played.attacks.target_count >= 1
        and packet.threats.opponent_forcing_replies >= 2
    ):
        return CauseCandidate(
            "own_threat_tunnel_vision",
            0.6,
            ("played.attacks", "threats.opponent_forcing_replies"),
        )
    return None


def _missed_opponent_resource(packet: EvidencePacket) -> CauseCandidate | None:
    """No idea of their own, and the reply was sharp — they simply did not look.

    Mutually exclusive with tunnel vision by construction (that rule requires
    the played move to threaten something, this one requires it not to), so the
    two never double-count the same mistake.
    """
    if (
        packet.played.attacks.target_count == 0
        and packet.threats.opponent_forcing_replies >= 3
    ):
        return CauseCandidate(
            "missed_opponent_resource",
            0.5,
            ("threats.opponent_forcing_replies", "played.move"),
        )
    return None


def _endgame_technique_gap(packet: EvidencePacket) -> CauseCandidate | None:
    """Not merely "it was an endgame" — a *conversion* failure.

    The rule requires that the user was winning and stopped being so. A bare
    phase test would fire on every endgame puzzle and would say nothing.
    """
    if (
        packet.position.phase == "endgame"
        and packet.eval_before >= 1.0
        and packet.eval_after < 0.5
    ):
        return CauseCandidate(
            "endgame_technique_gap",
            0.55,
            ("position.phase", "eval.before", "eval.after"),
        )
    return None


def _opening_pattern_gap(packet: EvidencePacket) -> CauseCandidate | None:
    """A big, early mistake with a short solution: a pattern that was not known.

    The short-solution condition is what separates "did not know this" from
    "did not calculate this" — a mistake needing a five-ply refutation is a
    calculation failure that happens to occur early.
    """
    if (
        packet.position.phase == "opening"
        and packet.swing >= 3.0
        and packet.best.pv_length <= 2
    ):
        return CauseCandidate(
            "opening_pattern_gap", 0.45, ("position.phase", "eval.swing")
        )
    return None


def _time_pressure_collapse(packet: EvidencePacket) -> CauseCandidate | None:
    """Modulator only — see :data:`MODULATOR_CAUSES`."""
    if packet.clock.is_time_pressure is not True:
        return None
    ids = ["clock.seconds_left"]
    if packet.clock.move_time_seconds is not None:
        ids.append("clock.move_time")
    return CauseCandidate("time_pressure_collapse", 0.6, tuple(ids))


Rule = Callable[[EvidencePacket], "CauseCandidate | None"]

RULES: tuple[Rule, ...] = (
    _loose_piece_awareness,
    _forcing_move_blindness,
    _quiet_move_blindness,
    _recapture_assumption,
    _calculation_stopped_early,
    _king_safety_blindness,
    _own_threat_tunnel_vision,
    _missed_opponent_resource,
    _endgame_technique_gap,
    _opening_pattern_gap,
    _time_pressure_collapse,
)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_causes(packet: EvidencePacket) -> CauseAssessment:
    """Run every rule and rank what fired.

    The primary cause is the strongest non-modulator candidate. Modulators can
    appear as secondary but never as primary, so a time scramble can colour a
    diagnosis without ever replacing it.
    """
    fired = [candidate for rule in RULES if (candidate := rule(packet)) is not None]
    # Sort by strength, then by cause name so ties are stable across runs —
    # an unstable order would make ``evidence_hash``-keyed caching pointless
    # and diffs between two runs unreadable.
    fired.sort(key=lambda c: (-c.strength, c.cause))

    substantive = [c for c in fired if c.cause not in MODULATOR_CAUSES]

    if not substantive:
        # Modulators alone are not a diagnosis: "you were low on time" does not
        # say what was missed. They are still returned as candidates so the
        # context survives into the explanation.
        return CauseAssessment(
            primary_cause=UNCLASSIFIED,
            secondary_causes=(),
            candidates=tuple(fired),
            insufficient_evidence=True,
        )

    primary = substantive[0]
    secondary = tuple(
        c.cause
        for c in fired
        if c.cause != primary.cause and c.strength >= _SECONDARY_FLOOR
    )[:_MAX_SECONDARY]

    return CauseAssessment(
        primary_cause=primary.cause,
        secondary_causes=secondary,
        candidates=tuple(fired),
        insufficient_evidence=False,
    )
