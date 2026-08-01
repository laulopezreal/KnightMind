"""Opening baselines from the lichess opening explorer.

A line's own score answers "how did I do here"; it cannot answer "was that
good". Without something to compare against, the Opening Explorer is a mirror:
it reports 41% in the Najdorf and leaves the reader to guess whether 41% is a
problem worth working on or simply what that line costs. The lichess explorer
supplies the missing half — how players around the same rating actually score
from the same position.

Two deliberate constraints:

* **Positions, not move sequences.** Keyed by EPD, so a line reached by
  transposition gets the same answer as the mainline order. The same reason
  ``eco.py`` is keyed that way.
* **Nothing but public aggregates leaves the box.** The request carries a
  position and a rating band. No username, no game, no identifier.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Any

import httpx
import truststore

truststore.inject_into_ssl()
SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

EXPLORER_URL = "https://explorer.lichess.ovh/lichess"

DEFAULT_HEADERS = {
    "User-Agent": "KnightMind/1.0 (https://github.com/laulopezreal/KnightMind)"
}

# Bullet is excluded on purpose: its opening play is not evidence about an
# opening, and correspondence is not evidence about a human under a clock.
SPEEDS = ("blitz", "rapid", "classical")

# The band starts the explorer itself accepts. Anything else is a 400 upstream.
RATING_BANDS = (0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500)

# Bumped when the request shape or the stored aggregate changes, so old rows
# can never be read back under a new meaning. Same discipline as the FEN eval
# cache and the openings tree cache.
SCHEME_VERSION = "v1"

# The explorer's aggregates move at the speed of millions of games, so a month
# old is not meaningfully staler than an hour old. The TTL is here to let a
# correction propagate eventually, not to keep the numbers fresh.
CACHE_TTL_DAYS = 30

# Below this the aggregate is noise: a handful of games at one rating band says
# nothing about what to expect, and showing 100% off three games is worse than
# showing nothing.
MIN_GAMES_FOR_BASELINE = 50


@dataclass(frozen=True)
class RatingBand:
    """A band of the explorer's rating buckets, as a half-open range."""

    low: int
    high: int | None

    @property
    def label(self) -> str:
        if self.high is None:
            return f"{self.low}+"
        if self.low == 0:
            return f"under {self.high}"
        return f"{self.low}–{self.high}"

    @property
    def query(self) -> str:
        """Value for the explorer's `ratings` parameter."""
        return str(self.low)


def band_for_rating(rating: int | None) -> RatingBand | None:
    """The explorer band a rating falls in, or None when it is unknown.

    None is a real answer, not a failure: a user with no imported rating gets a
    baseline over all ratings, which is still far better than no baseline. It
    must not be silently turned into a band they are not in.
    """
    if rating is None:
        return None
    low = RATING_BANDS[0]
    for band in RATING_BANDS:
        if rating >= band:
            low = band
    index = RATING_BANDS.index(low)
    high = RATING_BANDS[index + 1] if index + 1 < len(RATING_BANDS) else None
    return RatingBand(low=low, high=high)


@dataclass(frozen=True)
class ExplorerStats:
    """Aggregate results from one position, from White's point of view."""

    white: int
    draws: int
    black: int

    @property
    def games(self) -> int:
        return self.white + self.draws + self.black

    def expected_score(self, color: str) -> float | None:
        """Score percentage a player of this colour can expect here.

        Chess score, not win rate: a draw is half a point. Returns None when
        there is too little data to say anything, which the caller must render
        as "no baseline" rather than as zero.
        """
        if self.games < MIN_GAMES_FOR_BASELINE:
            return None
        wins = self.white if color == "white" else self.black
        return round(((wins + 0.5 * self.draws) / self.games) * 100, 1)


def cache_key(epd: str, band: RatingBand | None) -> str:
    """Self-invalidating key: a scheme change cannot read old rows."""
    speeds = ",".join(SPEEDS)
    ratings = band.query if band else "all"
    return f"{SCHEME_VERSION}|{epd}|{speeds}|{ratings}"


def parse_stats(payload: Any) -> ExplorerStats:
    """Read the aggregate out of an explorer response.

    Tolerant of missing keys on purpose: the explorer answers an unseen
    position with a body that simply has no counts, and that is "no data",
    not an error to propagate.
    """
    if not isinstance(payload, dict):
        raise ValueError("explorer response was not an object")

    def count(key: str) -> int:
        value = payload.get(key, 0)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"explorer response had a bad {key!r} count")
        return value

    return ExplorerStats(
        white=count("white"), draws=count("draws"), black=count("black")
    )


class ExplorerUnavailable(Exception):
    """The explorer could not be reached, or answered with something unusable.

    Deliberately distinct from "this position has no data": one is our problem
    and may resolve on retry, the other is a fact about the position.
    """


async def fetch_stats(
    epd: str,
    band: RatingBand | None,
    *,
    timeout: float = 6.0,
) -> ExplorerStats:
    """Ask the explorer about one position.

    The timeout is short by design. This sits behind a page that is already
    rendered and useful; a baseline that takes ten seconds to arrive is worth
    less than one that gives up and says nothing.
    """
    params: dict[str, str] = {
        "variant": "standard",
        "fen": epd,
        "speeds": ",".join(SPEEDS),
        "topGames": "0",
        "recentGames": "0",
        "moves": "0",
    }
    if band is not None:
        params["ratings"] = band.query

    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            verify=SSL_CONTEXT,
            follow_redirects=True,
        ) as client:
            response = await client.get(EXPLORER_URL, params=params)
    except httpx.HTTPError as exc:
        raise ExplorerUnavailable(f"explorer request failed: {exc}") from exc

    if response.status_code != 200:
        raise ExplorerUnavailable(f"explorer answered {response.status_code}")

    try:
        return parse_stats(response.json())
    except ValueError as exc:
        raise ExplorerUnavailable(str(exc)) from exc
