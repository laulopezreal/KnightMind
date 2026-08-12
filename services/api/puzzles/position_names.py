"""The deterministic namer: a puzzle's name derived from its own position.

Why this exists
---------------
``generate_puzzle_title`` mapped a motif to one of seven fixed strings, so the
name carried no information the motif did not already carry. On the real corpus
that produced ``The Missed Win`` 150 times — 150 puzzles whose motif classifier
fell through to its ``blunder`` default, all collapsing onto one title.

This module names from the *position*: which piece, which square, what it takes.
Two forks on different squares get different names, which is the property the
motif table could not have.

It is not the primary namer — ``ai_naming`` is. This is what runs when the model
is unavailable, and what every puzzle gets at creation time so the write path
never blocks on an API call. Being deterministic and total is the whole point:
it must produce a name for any position, without a network, without raising.
"""

from dataclasses import dataclass

import chess

from services.api.puzzles.identity import MOTIF_TITLES

# Short, readable piece names. Deliberately not python-chess's ``piece_name``,
# which is lowercase and would need title-casing at every call site.
PIECE_NAMES = {
    chess.PAWN: "Pawn",
    chess.KNIGHT: "Knight",
    chess.BISHOP: "Bishop",
    chess.ROOK: "Rook",
    chess.QUEEN: "Queen",
    chess.KING: "King",
}

# Names are rendered in a library card next to a board thumbnail. Longer than
# this wraps to a third line and the grid loses its rhythm.
MAX_NAME_CHARS = 48


@dataclass(frozen=True)
class PositionFacts:
    """What the deterministic namer is allowed to see.

    Carries no username and no opponent handle — the same identity boundary
    ``ai_naming.NameFacts`` enforces, kept here too so the two namers cannot
    drift into disagreeing about what a name may be built from.
    """

    fen: str
    best_move_uci: str
    primary_motif: str | None = None
    move_number: int | None = None


def _describe(facts: PositionFacts) -> tuple[str | None, str | None, str | None]:
    """Return (mover piece name, target square, captured piece name).

    Any unparseable input yields ``(None, None, None)`` rather than raising:
    a malformed FEN in one old row must not be able to fail a backfill over
    the whole corpus.
    """
    try:
        board = chess.Board(facts.fen)
        move = chess.Move.from_uci(facts.best_move_uci)
    except (ValueError, IndexError, TypeError):
        return None, None, None

    mover = board.piece_at(move.from_square)
    victim = board.piece_at(move.to_square)
    return (
        PIECE_NAMES.get(mover.piece_type) if mover else None,
        chess.square_name(move.to_square),
        PIECE_NAMES.get(victim.piece_type) if victim else None,
    )


def compose_position_name(facts: PositionFacts) -> str:
    """Name one puzzle from its position. Total: always returns something.

    The motif chooses the template; the position fills it in. When the position
    cannot be read at all we fall back to the old motif title, which is worse
    but never wrong — and is the only branch that can still repeat itself.
    """
    piece, square, victim = _describe(facts)
    motif = facts.primary_motif or "blunder"

    if square is None:
        # Unreadable position. The motif table is all that is left.
        return MOTIF_TITLES.get(motif, "Puzzle")

    if motif == "fork" and piece:
        return f"The {square} {piece} Fork"
    if motif == "pin":
        return f"The {square} Pin"
    if motif == "hanging_queen":
        return f"The Queen on {square}"
    if motif == "hanging_piece":
        return f"The Loose {victim or 'Piece'} on {square}"
    if motif == "back_rank":
        return f"Back Rank on {square}"
    if motif == "mate_threat":
        return f"Mate on {square}"

    # The default motif — the one 150 puzzles landed on. Naming from the move
    # is what stops them all being called the same thing.
    if victim:
        return f"The {victim} on {square}"
    if piece:
        return f"The {piece} to {square}"
    return f"The Move to {square}"


def _with_suffix(base: str, suffix: str) -> str:
    """``base + suffix``, trimming the base so the whole thing fits the card.

    The suffix is the part that carries the meaning here (it is what makes the
    name unique), so the base is what gives way.
    """
    room = MAX_NAME_CHARS - len(suffix)
    return base[:room].rstrip(" ,") + suffix


def disambiguate(name: str, used: set[str], move_number: int | None) -> str:
    """Make ``name`` unique against ``used``, cheapest suffix first.

    Two puzzles really can share a position-derived name — the same fork square
    in two different games. Rather than let the library show a duplicate, add
    the move number, then a counter. The caller owns ``used`` and is expected to
    add the returned value to it.

    The result never exceeds ``MAX_NAME_CHARS``. An accepted AI name may be
    exactly at the cap, and appending ", move 199" to it would otherwise push
    the card past the budget both namers are written to.
    """
    name = name[:MAX_NAME_CHARS].rstrip(" ,")
    if name not in used:
        return name

    if move_number is not None:
        candidate = _with_suffix(name, f", move {move_number}")
        if candidate not in used:
            return candidate

    n = 2
    while _with_suffix(name, f" ({n})") in used:
        n += 1
    return _with_suffix(name, f" ({n})")
