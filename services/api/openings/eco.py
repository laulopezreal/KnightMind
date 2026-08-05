"""
ECO classification for opening lines.

The explorer used to show bare SAN — `1. e4 c5 2. Nf3 d6 3. d4` — which is
correct and unreadable. A repertoire you cannot name is one you cannot search
for, study, or talk about, so every node is annotated with the opening it
belongs to.

Data: the lichess `chess-openings` table (CC0), vendored as ``eco.tsv``.

**Keyed by position, not by move order.** Keying on the SAN sequence looked
right and was wrong: `1.d4 Nf6 2.c4 e6 3.Nc3 Bb4` found the Nimzo-Indian while
the identical position via `1.c4 e6 2.Nc3 Bb4 3.d4 Nf6` found nothing.
Transposition is ordinary chess, not an edge case, so each entry is replayed
once at load and stored under its EPD — the same key the tree builder can
produce from the board it already maintains.

Naming is longest-prefix: a position with no entry of its own inherits the
nearest named ancestor, because twenty plies into the Najdorf you are still in
the Najdorf.
"""

from __future__ import annotations

import csv
import logging
import re
from functools import lru_cache
from pathlib import Path

import chess

logger = logging.getLogger(__name__)

ECO_DATA = Path(__file__).with_name("eco.tsv")

# "1." / "1..." — move numbers in the vendored PGN column, not moves.
_MOVE_NUMBER = re.compile(r"^\d+\.+$")


def _position_key(pgn: str) -> str | None:
    """Replay a vendored line and return the EPD it reaches, or None if it will not play."""
    board = chess.Board()
    for token in pgn.split():
        if _MOVE_NUMBER.match(token):
            continue
        try:
            board.push_san(token)
        except ValueError:
            return None
    return board.epd() if board.move_stack else None


@lru_cache(maxsize=1)
def max_book_ply() -> int:
    """
    Depth of the deepest vendored line (36 at the time of writing).

    A position past it cannot match any entry, so computing its key is pure
    waste — and deeper nodes inherit their ancestor's name regardless, which is
    the designed behaviour. Derived from the data rather than hardcoded so a
    refreshed table cannot silently outgrow the cutoff.
    """
    return max(
        (len(entry) for entry in _entry_plies()),
        default=0,
    )


@lru_cache(maxsize=1)
def _entry_plies() -> tuple[tuple[str, ...], ...]:
    """Move lists of every vendored entry, for depth statistics."""
    if not ECO_DATA.exists():
        return ()
    with ECO_DATA.open(encoding="utf-8", newline="") as handle:
        return tuple(
            tuple(t for t in row.get("pgn", "").split() if not _MOVE_NUMBER.match(t))
            for row in csv.DictReader(handle, delimiter="\t")
        )


@lru_cache(maxsize=1)
def eco_table() -> dict[str, tuple[str, str]]:
    """
    Position EPD -> (ECO code, opening name).

    Replaying ~3,800 lines costs roughly a third of a second, paid once per
    process and cached, against a lookup consulted for every node of every tree.
    """
    table: dict[str, tuple[str, str]] = {}
    if not ECO_DATA.exists():
        # Loud rather than silent: an empty table leaves every opening unnamed,
        # which looks like "we could not classify these lines" instead of "the
        # data file did not ship".
        logger.error("ECO data missing at %s — openings will be unnamed", ECO_DATA)
        return table

    unplayable = 0
    with ECO_DATA.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            key = _position_key(row.get("pgn", ""))
            if key is None:
                unplayable += 1
                continue
            table.setdefault(key, (row["eco"], row["name"]))

    if unplayable:
        logger.warning("ECO data: %d entries could not be replayed", unplayable)
    return table


def classify(epd: str | None) -> tuple[str, str] | None:
    """Exact-position lookup. Callers inherit from the parent for longest-prefix."""
    if not epd:
        return None
    return eco_table().get(epd)


# Depth -> the fewest games a line must have to be worth returning.
#
# Measured on 800 games: a 40-ply tree is 96% lines played exactly once —
# 20,546 nodes and 3.8 MB of JSON against 392 nodes and 71 KB once those are
# dropped. The floor lives here, server-side, because the server is what pays
# for the blowup: a client-only rule protects nobody holding a URL.
_MIN_GAMES_FLOOR = ((32, 3), (16, 2))


def min_games_floor(max_ply: int) -> int:
    """Smallest `min_games` worth serving at this depth."""
    for depth, floor in _MIN_GAMES_FLOOR:
        if max_ply >= depth:
            return floor
    return 1


def warm() -> None:
    """
    Build the table up front.

    ~370 ms of python-chess replay, paid once per process at startup instead of
    by whichever user happens to arrive first after a deploy. ``lru_cache``
    makes the *result* shared, not the work, so without this several concurrent
    first-requests would each replay the table before one of them won the race.
    """
    eco_table()
    max_book_ply()
