"""Named mistake patterns — the coaching layer over raw causes.

A *cause* is a classification: ``loose_piece_awareness``. A *pattern* is that
cause plus the context it keeps happening in, given a name and a description a
person can act on: "Loose Piece Syndrome — you calculate your own threat first
and skip the check for what of yours is hanging."

Names come from a static table, not the model. A generated name that drifts
between runs makes Insights feel unreliable — the same weakness would be called
something different each week. The AI stage may personalise the *explanation* on
an individual diagnosis; the pattern's identity stays fixed.

Stability has a second half the table alone does not give you. A phase-specific
name is only selected when that phase holds a strict majority of the cause's
diagnoses (see ``_dominant`` in the diagnosis repository) — otherwise the
general name is used. Keying on a plurality would have reintroduced the drift
from the data side: at 4-3 a single new puzzle would rename a user's weakness
with no explanation.

No new tables. At this corpus size the grouping is a few hundred rows and is
computed on demand from ``puzzle_diagnoses``, the same way the cause breakdown
is. See the note at the bottom for what would justify persisting it.
"""

from dataclasses import dataclass

from services.api.diagnosis.causes import UNCLASSIFIED

# (cause, phase) -> (name, description). Phase is optional: a cause that plays
# out the same way everywhere gets one entry keyed on None.
#
# Descriptions are second person and describe the *habit*, not the position —
# the user already knows what happened on the board; what they need is the thing
# to change.
_PATTERNS: dict[tuple[str, str | None], tuple[str, str]] = {
    ("loose_piece_awareness", None): (
        "Loose Piece Syndrome",
        "You calculate your own threat first and skip the scan for which of "
        "your pieces are undefended. Tactics work against you because two "
        "things are hanging at once.",
    ),
    ("loose_piece_awareness", "opening"): (
        "Loose Pieces After Development",
        "Your pieces come out fast but end up undefended. Before each move in "
        "the opening, check what is protecting what.",
    ),
    ("forcing_move_blindness", None): (
        "Missed Forcing Moves",
        "You settle on a reasonable-looking quiet move before checking the "
        "checks and captures. Look at every forcing option first, even the "
        "ones that look absurd.",
    ),
    ("quiet_move_blindness", None): (
        "Forcing-Move Tunnel",
        "You reach for a check or capture when the position wanted a quiet "
        "move. When the forcing lines do not work, the answer is often a "
        "threat rather than a hit.",
    ),
    ("recapture_assumption", None): (
        "Automatic Recapture",
        "When something gets taken, you take back without stopping to look. A "
        "recapture is a move like any other — check the in-between options "
        "before playing it.",
    ),
    ("calculation_stopped_early", None): (
        "Calculation Stops One Move Early",
        "You find the right idea but stop before the end of the line. Push "
        "each candidate one move further than feels necessary.",
    ),
    ("king_safety_blindness", None): (
        "King Safety Blind Spot",
        "You miss danger building around your own king. When two or more "
        "enemy pieces point at it, count the attackers before continuing your "
        "own plan.",
    ),
    ("king_safety_blindness", "endgame"): (
        "Back Rank Neglect",
        "Your king stays boxed in behind its pawns while you play elsewhere. "
        "Give it an escape square before it costs you.",
    ),
    ("own_threat_tunnel_vision", None): (
        "Attack Tunnel Vision",
        "Once you are attacking, you stop looking at the opponent's replies. "
        "After choosing your move, ask what they get to do next.",
    ),
    ("missed_opponent_resource", None): (
        "Missed Opponent Resource",
        "You play as if the position were yours alone. Before each move, look "
        "for the opponent's best answer, not their most likely one.",
    ),
    ("endgame_technique_gap", None): (
        "Endgame Conversion",
        "You reach won endgames and let them slip. The winning technique, not "
        "the tactics, is what needs work here.",
    ),
    ("opening_pattern_gap", None): (
        "Opening Pattern Gap",
        "You go wrong early in positions that have a known handling. These are "
        "worth learning rather than calculating.",
    ),
    ("time_pressure_collapse", None): (
        "Time Pressure Collapse",
        "Your accuracy falls away when the clock does. This is a pacing "
        "problem more than a chess one.",
    ),
}


@dataclass(frozen=True)
class PatternIdentity:
    name: str
    description: str


def identify(cause: str, phase: str | None = None) -> PatternIdentity | None:
    """Name the pattern for a cause in a phase, if it has one.

    Falls back from the phase-specific entry to the general one, so a new phase
    never leaves a cause unnamed. Returns None for ``unclassified``: "we could
    not work out why" is an honest state, not a habit with a catchy name, and
    dressing it up as one is exactly the overreach this feature avoids.
    """
    if cause == UNCLASSIFIED:
        return None
    entry = _PATTERNS.get((cause, phase)) or _PATTERNS.get((cause, None))
    if entry is None:
        return None
    return PatternIdentity(name=entry[0], description=entry[1])


def priority_score(mistakes: int, accuracy: float | None, recent: int) -> float:
    """How much attention a pattern deserves, relative to this user's others.

    Three factors, deliberately simple and inspectable:

    * **frequency** — how much of the corpus it accounts for
    * **difficulty** — a pattern you already solve when retried needs less work
      than one you keep failing. Unknown accuracy counts as neutral rather than
      as either extreme, so an untested pattern neither jumps the queue nor
      gets buried.
    * **recency** — a habit still showing up lately matters more than one you
      may already have grown out of

    Not a probability, and not comparable between users. It orders one person's
    patterns against each other, nothing more.
    """
    # Measured difficulty spans [1.0, 2.0] — 1.0 when you always solve it,
    # 2.0 when you never do. Unknown takes the midpoint, not 1.0: anchoring it
    # at the bottom of the range would rank an untested pattern below one the
    # user demonstrably already handles, which is the opposite of neutral.
    difficulty = 1.5 if accuracy is None else 1.0 + (1.0 - accuracy)
    recency = 1.0 + (recent / mistakes if mistakes else 0.0)
    return round(mistakes * difficulty * recency, 3)


# Why there are no cluster tables
# ------------------------------
# The plan called for ``mistake_pattern_clusters`` plus a link table, populated
# by a batch job. At a few hundred diagnoses per user the grouping is a
# millisecond query, so those tables would add a migration, a job type and a
# staleness window in exchange for nothing measurable.
#
# What would justify revisiting: corpora large enough that the grouping shows up
# in request timings, or clustering that stops being derivable from
# (cause, phase) — embedding similarity over explanations, say, which cannot be
# recomputed on demand cheaply.
