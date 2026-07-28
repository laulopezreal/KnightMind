"""
ECO classification for opening lines.

The explorer used to show bare SAN — `1. e4 c5 2. Nf3 d6 3. d4` — which is
correct and unreadable. A repertoire you cannot name is one you cannot search
for, study, or talk about, so every node is annotated with the opening it
belongs to.

Data: the lichess `chess-openings` table (CC0), vendored as ``eco.tsv``. Its
`pgn` column is a SAN move list with move numbers, e.g. "1. e4 c5 2. Nf3"; the
numbers are stripped at load so a lookup key is exactly the tuple of moves the
tree builder already walks.

Naming is longest-prefix: a position deep in a line takes the most specific
entry at or above it. That is why nodes inherit their parent's name when their
own exact path is not in the table — 20 plies into the Najdorf you are still in
the Najdorf, and reporting "unnamed" there would be worse than useless.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

ECO_DATA = Path(__file__).with_name("eco.tsv")

# "1." / "1..." — move numbers in the vendored PGN column, not moves.
_MOVE_NUMBER = re.compile(r"^\d+\.+$")


def _moves(pgn: str) -> tuple[str, ...]:
    return tuple(t for t in pgn.split() if not _MOVE_NUMBER.match(t))


@lru_cache(maxsize=1)
def eco_table() -> dict[tuple[str, ...], tuple[str, str]]:
    """
    Move path -> (ECO code, opening name).

    Cached for the life of the process: ~3,800 rows parse in a few
    milliseconds, but the tree builder consults this once per node.
    """
    table: dict[tuple[str, ...], tuple[str, str]] = {}
    if not ECO_DATA.exists():  # pragma: no cover - vendored alongside this file
        return table

    with ECO_DATA.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            path = _moves(row.get("pgn", ""))
            if not path:
                continue
            # First entry wins: the file is sorted, so a shorter, more general
            # line is never overwritten by a longer one sharing its prefix.
            table.setdefault(path, (row["eco"], row["name"]))
    return table


def classify(path: tuple[str, ...]) -> tuple[str, str] | None:
    """Exact-path lookup. Callers inherit from the parent for longest-prefix."""
    return eco_table().get(path)
