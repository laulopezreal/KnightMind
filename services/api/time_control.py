"""
Time control classification utility.

Maps raw Chess.com time control strings (e.g. "600", "180+2") to
standard categories: bullet, blitz, rapid.

Chess.com format: "base" or "base+increment" where base is in seconds.
Classification uses FIDE-style thresholds applied to estimated game duration:
    total_seconds = base + 40 * increment  (assuming ~40 moves per game)
    bullet:  total < 180s  (< 3 min)
    blitz:   180s <= total < 600s  (3–10 min)
    rapid:   total >= 600s (>= 10 min)
"""


def classify_time_control(raw: str) -> str | None:
    """
    Classify a raw Chess.com time_control string into bullet/blitz/rapid.

    Args:
        raw: Chess.com time control, e.g. "600", "180+2", "300+0"

    Returns:
        One of "bullet", "blitz", "rapid", or None for unrecognized formats
        (e.g. "daily", "1/259200").

    Examples:
        >>> classify_time_control("60")
        'bullet'
        >>> classify_time_control("180")
        'blitz'
        >>> classify_time_control("180+2")
        'blitz'
        >>> classify_time_control("300")
        'blitz'
        >>> classify_time_control("600")
        'rapid'
        >>> classify_time_control("600+5")
        'rapid'
        >>> classify_time_control("900")
        'rapid'
        >>> classify_time_control("daily") is None
        True
    """
    # Check if the raw string is already a known category
    normalized = raw.strip().lower()
    if normalized in ("bullet", "blitz", "rapid"):
        return normalized

    parts = raw.split("+")
    try:
        base = int(parts[0])
        increment = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None

    # Reject non-positive base times (e.g. "0" or negative values)
    if base <= 0:
        return None

    total = base + 40 * increment

    if total < 180:
        return "bullet"
    if total < 600:
        return "blitz"
    return "rapid"
