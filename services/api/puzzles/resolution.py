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


def motif_is_visible(
    *,
    resolved: bool,
    puzzle_motif: str | None,
    requested_motif: str | None,
) -> bool:
    """Whether a puzzle's motif may be shown, after §5's intent overrides.

    Themed practice and spoiler-freeness are the same knob: "practise your
    forks" and "don't tell me it's a fork" cannot both hold. So naming a motif
    relaxes the gate for **that motif**, on the grounds that the user just
    typed it and cannot be spoiled by being told what they asked for.

    The override grants **only what it names**, which is the property a
    per-session mode could never give. `?motif=fork` reveals `fork` on the
    puzzles that have it; every other puzzle in the same session stays gated,
    so a forged parameter or a shared link is worth exactly one motif rather
    than unlocking the queue.

    It does NOT reveal the nickname, and no intent in §5 does. The nickname is
    the recall label -- it exists to be distinctive about what happened, which
    is the answer -- so unlocking it on a themed session would hand over the
    tactic the theme merely categorises. Only resolution reveals a nickname.
    """
    if resolved:
        return True
    if not requested_motif or not puzzle_motif:
        return False
    return puzzle_motif.strip().lower() == requested_motif.strip().lower()


def focus_is_visible(
    *,
    resolved: bool,
    focus_name: str | None,
    requested_focus: str | None,
) -> bool:
    """Whether ``queue_reason``'s diagnosed cause/opening may be named.

    Same rule as the motif, for the same reason: the focus is in the request,
    so echoing it back reveals nothing the caller did not supply. And the same
    limit -- it names one cause, so it unlocks one cause.
    """
    if resolved:
        return True
    if not requested_focus or not focus_name:
        return False
    return focus_name.strip().lower() == requested_focus.strip().lower()
