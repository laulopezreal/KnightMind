"""The resolution gate: what may be seen about a puzzle before it is attempted.

Design: ``docs/design-puzzle-identity-and-modes.md`` §4 and §4.1.

A puzzle's *revealing* fields -- nickname, motif, diagnosis prose, queue reason
-- describe what happens in the position, so serving them before an attempt
hands over the tactic. The gate is a property of the **puzzle**, not of the
request: one predicate, applied in the serializers every puzzle route already
goes through, which is why it survives deep links, a second tab, and a session
resumed days later by construction rather than by each endpoint remembering.

Resolution is **per exposure, not lifetime**, and that distinction is the whole
point (§4.1). The obvious rule -- ``attempts > 0`` -- never reverts, and a
nickname only ever exists for a puzzle that has been failed and rescheduled. So
under a lifetime rule every puzzle that *has* a nickname has an open gate, and
the review card shows a recall-optimised name immediately before the repeat
attempt. In a spaced-repetition library the repeat attempt is the product. "A
nickname is valuable after the attempt" is true of attempt 1 and false of every
attempt after, because after attempt 1 is before attempt 2.

So the puzzle re-closes when it next comes due::

    attempts > 0 AND now() < next_due_at

Note this compares against **now**, not against the last attempt. An earlier
revision of the design proposed ``last_reviewed_at < next_due_at``, which is
never false: ``spaced_repetition`` writes both in the same statement with the
interval floored at one day, so it holds for every attempted puzzle in both of
the states the predicate exists to separate.
"""

import os
from datetime import datetime, timezone

# Rollout flag -- KNIGHTMIND_RESOLUTION_GATE, default OFF. Same shape as
# KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS, and for the same reason: the gate changes
# what every puzzle surface shows, so it must be revertible without a deploy.
#
# OFF (default): nothing is withheld and every response is byte-identical to
# what it was before this module existed.
#
# Do NOT turn it ON before rollout step 4 lands. Step 4 adds the intent
# overrides in §5, and without them a themed session (`?motif=fork`) returns
# puzzles with the motif hidden -- the API withholding the very thing the user
# filtered on.
RESOLUTION_GATE_ENV = "KNIGHTMIND_RESOLUTION_GATE"


def resolution_gate_enabled() -> bool:
    """Whether revealing fields are withheld for unresolved puzzles."""
    return os.environ.get(RESOLUTION_GATE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_resolved(stats, *, now: datetime | None = None) -> bool:
    """Has this ``(user, puzzle)`` been resolved *for its current exposure*?

    Returns True for everything while the gate is off, so every caller can ask
    unconditionally and the flag is checked in exactly one place.

    ``stats`` is the user's ``PuzzleStats`` row, or None for a puzzle they have
    never touched -- which is unresolved by definition.
    """
    if not resolution_gate_enabled():
        return True
    if stats is None:
        return False

    attempts = getattr(stats, "attempts", 0) or 0
    if attempts <= 0:
        return False

    next_due_at = getattr(stats, "next_due_at", None)
    if next_due_at is None:
        # Attempted but never scheduled. Not reachable today -- `attempts` and
        # `next_due_at` are written in the same statement -- but if it happens,
        # the puzzle cannot have come due again, so it is still resolved. Fail
        # open here rather than hiding the outcome the user just earned.
        return True

    moment = now or datetime.now(timezone.utc)
    # The column is naive UTC; compare like with like rather than letting
    # Postgres cast through the session TimeZone, the bug the worker heartbeat
    # already paid for once.
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    if next_due_at.tzinfo is not None:  # defensive: an aware value from a caller
        next_due_at = next_due_at.astimezone(timezone.utc).replace(tzinfo=None)

    return moment < next_due_at
