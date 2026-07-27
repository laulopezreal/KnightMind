"""Shared environment-variable parsing helpers.

Consolidates the near-identical integer readers that grew independently in
``ratelimit``, ``analytics_confidence``, and the Stockfish engine config.
Their subtle semantic differences (is zero allowed? is a bad value logged?)
are now explicit parameters instead of three diverging copies.
"""

import logging
import os

logger = logging.getLogger(__name__)


def env_int(
    name: str,
    default: int,
    *,
    min_value: int | None = None,
    log_invalid: bool = False,
) -> int:
    """Read an integer from the environment, falling back to ``default``.

    Unset or blank values fall back silently — an absent override is normal,
    not an error. Values that fail to parse, or parse below ``min_value``,
    also fall back to ``default``, with a warning when ``log_invalid`` is set
    so a deployment typo is visible in logs.

    ``min_value`` makes each call site's range contract explicit: pass ``1``
    where the value must be positive (analytics thresholds, engine options —
    a 0 would silently disable a gate or misconfigure the engine), and leave
    it ``None`` where any integer is meaningful (rate limits, where 0
    deliberately disables a limiter).
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        if log_invalid:
            logger.warning("Invalid %s=%r, using default %s", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        if log_invalid:
            logger.warning(
                "Out-of-range %s=%r (minimum %s), using default %s",
                name,
                raw,
                min_value,
                default,
            )
        return default
    return value
