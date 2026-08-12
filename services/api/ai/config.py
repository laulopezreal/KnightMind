"""Configuration for AI diagnosis: the flag, the model, and the budget ceilings.

One place for everything an operator can turn without a deploy, and the one
place to look during an incident.
"""

import os

from services.api.envutil import env_int

# Ships ON, unlike KNIGHTMIND_REQUIRE_AUTH and KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS.
# Because there is no dark-launch period, the safety properties a quiet rollout
# would have provided are built into the feature instead — see is_enabled(),
# the call-time key check, and the caps below.
_FLAG = "KNIGHTMIND_AI_DIAGNOSIS"

# Pinned, and written to every diagnosis row. A model change must therefore be
# a deliberate edit here, and it invalidates cached diagnoses through
# model_version rather than silently changing what stored rows mean.
MODEL = "claude-opus-5"

# Low effort suits the shape of this task: the model selects from a candidate
# set the rules already computed and writes two short sentences. It is not
# solving the position. Effort is the primary cost lever — thinking is on by
# default on this model and bills as output tokens.
EFFORT = "low"

# Generous enough for the structured payload plus low-effort thinking. A
# truncated response is treated as a rejection rather than salvaged, so this
# being too small shows up as a rejection rate, not as corrupt data.
MAX_TOKENS = 8192

REQUEST_TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2

# Daily ceilings, counted from the audit log (see client.spend_today). Sized
# against the real corpus — 238 puzzles, so a full backfill costs single-digit
# dollars and fits inside one day's per-user allowance with room to spare. The
# global cap is the backstop against a runaway loop across users.
DAILY_CAP_PER_USER = env_int("KNIGHTMIND_AI_DAILY_CAP_USER", 500, min_value=1)
DAILY_CAP_GLOBAL = env_int("KNIGHTMIND_AI_DAILY_CAP_GLOBAL", 1000, min_value=1)

# Prompts and responses are kept for debugging and incident review, not
# forever. Swept by the existing session-cleanup loop.
AUDIT_RETENTION_DAYS = env_int("KNIGHTMIND_AI_AUDIT_RETENTION_DAYS", 30, min_value=1)

# Responses are truncated before storage so one pathological reply cannot bloat
# the table. The flag on the row records that truncation happened.
AUDIT_RESPONSE_MAX_CHARS = 16_384


# --- Puzzle naming -----------------------------------------------------------
#
# Naming is a second, independent kind of model call. It gets its own flag,
# model pin and budget rather than sharing diagnosis's, because the two fail
# and cost differently: diagnosis is enrichment on a page the user is looking
# at, naming is a bulk pass over a whole library. A runaway backfill must not
# be able to spend the day's diagnosis allowance.
#
# Ships OFF. Unlike diagnosis this one writes a user-visible string to a column
# that already has a value, so the first run should be a deliberate one.
_NAMING_FLAG = "KNIGHTMIND_AI_NAMING"

NAMING_MODEL = "claude-opus-5"

# Naming is a shorter task than diagnosis — one line, from facts already
# extracted — so it gets the same low effort and a much smaller token ceiling.
NAMING_EFFORT = "low"
NAMING_MAX_TOKENS = 2048

# Sized against the real corpus: 318 puzzles, so a full backfill fits inside
# one day's per-user allowance with room to re-run after a prompt change.
NAMING_DAILY_CAP_PER_USER = env_int("KNIGHTMIND_AI_NAMING_CAP_USER", 500, min_value=1)
NAMING_DAILY_CAP_GLOBAL = env_int("KNIGHTMIND_AI_NAMING_CAP_GLOBAL", 1000, min_value=1)


def naming_is_enabled() -> bool:
    """Whether AI naming should be attempted at all.

    Default OFF, the opposite of ``is_enabled``. Naming overwrites a column
    users already see, so it is opt-in: set ``KNIGHTMIND_AI_NAMING=1``. With it
    unset, every puzzle is named deterministically from its position and no
    model call is made.
    """
    raw = os.environ.get(_NAMING_FLAG)
    if raw is None or raw.strip() == "":
        return False
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def is_enabled() -> bool:
    """Whether AI enrichment should be attempted at all.

    Default ON: only an explicit falsey value turns it off. Setting
    ``KNIGHTMIND_AI_DIAGNOSIS=0`` is a complete kill switch — no model call is
    made, no audit row is written, and diagnosis falls back to rules-only.
    """
    raw = os.environ.get(_FLAG)
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def api_key() -> str | None:
    """The API key, read at call time — never at import or startup.

    A missing key must not stop the API from starting. Diagnosis is enrichment,
    not a load-bearing dependency like DATABASE_URL, so its absence degrades the
    feature to rules-only rather than taking the service down.
    """
    raw = os.environ.get("ANTHROPIC_API_KEY")
    return raw.strip() or None if raw else None
