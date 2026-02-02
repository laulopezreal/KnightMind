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


def classify_time_control(raw: str) -> str:
    """
    Classify a raw Chess.com time_control string into bullet/blitz/rapid.

    Args:
        raw: Chess.com time control, e.g. "600", "180+2", "300+0"

    Returns:
        One of "bullet", "blitz", or "rapid".

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
    """
    parts = raw.split("+")
    try:
        base = int(parts[0])
        increment = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        # If we can't parse, check if the raw string is already a category
        normalized = raw.strip().lower()
        if normalized in ("bullet", "blitz", "rapid"):
            return normalized
        return "rapid"  # safe default

    total = base + 40 * increment

    if total < 180:
        return "bullet"
    if total < 600:
        return "blitz"
    return "rapid"
