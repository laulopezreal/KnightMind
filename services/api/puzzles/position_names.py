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
    # The move the player PLAYED, not the engine's. Naming from the winning
    # move meant every deterministic name carried the answer's destination
    # square — "The h1 Pin", "The Queen to h1" — which is the one thing the
    # model's own names are gated against. The fallback was leaking what the
    # gate refuses, and it is the branch that runs on every puzzle at creation.
    #
    # The played move is never the solution (verified across the corpus:
    # played_move_uci = best_move_uci in zero rows), so it identifies the
    # position without pointing at the answer.
    played_move_uci: str
    primary_motif: str | None = None
    move_number: int | None = None

    # The square the WINNING move lands on. Never used to build a name — it is
    # here so the composer can refuse to name a square that happens to be it.
    #
    # Naming from the played move is not sufficient on its own: the two moves
    # differ, but their DESTINATIONS coincide on 17 of 348 live puzzles (4.9%)
    # — recaptures, and wrong-piece-right-square. "Queen to h1" for a puzzle
    # whose answer lands on h1 is exactly the title class this namer was
    # changed to stop producing. The earlier fix verified whole-move
    # inequality, which is the wrong invariant.
    answer_square: str | None = None


def answer_square_of(best_move_uci: str | None) -> str | None:
    """The square the winning move lands on, or None if unreadable.

    Passed to PositionFacts so the composer can refuse to name it. Shared
    rather than re-derived per call site: four places build these facts, and a
    site that forgets it silently reopens the leak.
    """
    uci = best_move_uci or ""
    if len(uci) < 4:
        return None
    square = uci[2:4]
    if square[0] in "abcdefgh" and square[1] in "12345678":
        return square
    return None


def _describe(
    facts: PositionFacts,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (mover piece name, target square, captured piece, origin square).

    Any unparseable input yields ``(None, None, None)`` rather than raising:
    a malformed FEN in one old row must not be able to fail a backfill over
    the whole corpus.
    """
    try:
        board = chess.Board(facts.fen)
        move = chess.Move.from_uci(facts.played_move_uci)
    except (ValueError, IndexError, TypeError):
        return None, None, None, None

    mover = board.piece_at(move.from_square)
    victim = board.piece_at(move.to_square)
    return (
        PIECE_NAMES.get(mover.piece_type) if mover else None,
        chess.square_name(move.to_square),
        PIECE_NAMES.get(victim.piece_type) if victim else None,
        chess.square_name(move.from_square),
    )


def compose_position_name(facts: PositionFacts) -> str:
    """Name one puzzle from the move its player actually made. Always returns.

    Deliberately says nothing about the tactic. The motif describes the
    SOLUTION, so pairing a motif word with the played move's square would be
    both misleading ("The h6 Pin" when the pin is elsewhere) and a hint. This
    names what the player did, which is theirs to know.

    When the position cannot be read at all we fall back to the old motif
    title: worse, but never wrong, and the only branch that can still repeat.
    """
    piece, square, victim, origin = _describe(facts)

    if square is None:
        # Unreadable position. The motif table is all that is left.
        return MOTIF_TITLES.get(facts.primary_motif or "blunder", "Puzzle")

    if facts.answer_square and square == facts.answer_square:
        # The played move lands where the winning move lands. Naming that
        # square would hand over the answer, so name where the piece came
        # FROM instead — the origin of a move that was not the solution says
        # nothing about the solution's destination, and it keeps the name
        # distinct rather than collapsing it to a generic phrase.
        if piece and origin:
            return f"{piece} Left {origin}"
        if origin:
            return f"The Move From {origin}"
        return MOTIF_TITLES.get(facts.primary_motif or "blunder", "Puzzle")

    if victim and piece:
        return f"{piece} Takes {victim} on {square}"
    if piece:
        return f"{piece} to {square}"
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
