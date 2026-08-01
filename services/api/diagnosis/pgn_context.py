"""
PGN-replay facts for mistake diagnosis.

Pure functions: PGN text in, frozen dataclasses out. No database, no engine, no
network. Everything here is recoverable only by replaying the game — a puzzle's
stored FEN is a snapshot and cannot tell us:

* **What the opponent just played.** Needed to distinguish "the user recaptured
  on autopilot" from "the user chose a move". A FEN has no history.
* **How much clock the user had.** Chess.com PGNs carry ``[%clk H:MM:SS.f]`` per
  move, so the single most-cited excuse for a blunder ("I was low on time") is
  already sitting in ``Game.pgn_blob`` and can be checked rather than guessed.
* **Whether the user had castled.** Castling *rights* in a FEN are lost both by
  castling and by moving the king or rook, so absence of rights is not evidence
  of a castled king.

Ply convention matches the generator (``puzzles/generator.py``): plies are
1-based over the mainline, and ``Puzzle.ply`` is the index of the *user's*
mistake move, with ``Puzzle.fen`` being the position immediately before it.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import chess
import chess.pgn

from services.api.openings.eco import classify as eco_classify
from services.api.openings.eco import max_book_ply

# Chess.com time-control strings: "600" (10m), "600+5" (10m + 5s), "1/86400"
# (correspondence, one move per N seconds). Anything else is treated as unknown
# rather than guessed at.
_TC_INCREMENT_RE = re.compile(r"^(\d+)\+(\d+)$")
_TC_PLAIN_RE = re.compile(r"^(\d+)$")
_TC_CORRESPONDENCE_RE = re.compile(r"^\d+/(\d+)$")


@dataclass(frozen=True)
class TimeControl:
    """A parsed chess.com time-control string.

    ``base_seconds`` is None when the control could not be parsed, which is a
    real state (unknown), not a zero. Callers must not treat None as "no time".
    """

    raw: str | None
    base_seconds: int | None
    increment_seconds: int
    is_correspondence: bool

    @property
    def is_known(self) -> bool:
        return self.base_seconds is not None


UNKNOWN_TIME_CONTROL = TimeControl(
    raw=None, base_seconds=None, increment_seconds=0, is_correspondence=False
)


def parse_time_control(raw: str | None) -> TimeControl:
    """Parse a chess.com ``TimeControl`` value.

    Unrecognised formats return :data:`UNKNOWN_TIME_CONTROL` rather than a
    fabricated default — a wrong base time would silently corrupt every
    time-pressure judgement downstream.
    """
    if not raw:
        return UNKNOWN_TIME_CONTROL

    value = raw.strip()

    match = _TC_INCREMENT_RE.match(value)
    if match:
        return TimeControl(
            raw=value,
            base_seconds=int(match.group(1)),
            increment_seconds=int(match.group(2)),
            is_correspondence=False,
        )

    match = _TC_PLAIN_RE.match(value)
    if match:
        return TimeControl(
            raw=value,
            base_seconds=int(match.group(1)),
            increment_seconds=0,
            is_correspondence=False,
        )

    match = _TC_CORRESPONDENCE_RE.match(value)
    if match:
        # Correspondence: days-per-move, not a thinking clock. Recorded so the
        # time-pressure rule can exclude it outright — "low on time" is
        # meaningless at one move per day.
        return TimeControl(
            raw=value,
            base_seconds=int(match.group(1)),
            increment_seconds=0,
            is_correspondence=True,
        )

    return TimeControl(
        raw=value, base_seconds=None, increment_seconds=0, is_correspondence=False
    )


@dataclass(frozen=True)
class GameContext:
    """Replay-derived facts about one ply of one game.

    Every field is either a measured fact or None. None always means "the PGN
    did not carry this", never zero or false — a game without ``[%clk]`` tags
    must not read as a game played with no time left.
    """

    # The opponent's move immediately before the mistake (ply - 1).
    previous_move_uci: str | None
    previous_move_was_capture: bool
    # Destination square of that move when it was a capture. This is the square
    # a "natural" recapture would target, so it is what the recapture-assumption
    # rule compares against. For en passant it is the arrival square (not the
    # captured pawn's square), which is the correct square for that comparison.
    previous_capture_square: int | None

    # True if the user played a castling move at any earlier ply.
    user_castled: bool

    # Clock readings in seconds. ``[%clk]`` records time remaining *after* the
    # move, so "before" is read from the user's previous move (ply - 2).
    clock_before_move_seconds: float | None
    clock_after_move_seconds: float | None
    # Wall time the user spent choosing the mistake, increment-adjusted. Only
    # computable when both readings exist.
    move_time_seconds: float | None

    # Position reached after replaying ply - 1 moves. Compared against the
    # puzzle's stored FEN to catch a PGN/puzzle desync rather than silently
    # analysing the wrong position.
    fen_before_move: str | None
    plies_in_game: int

    # Opening family the game reached before the mistake, e.g. "Sicilian
    # Defense". Coarser than the full ECO name on purpose: "Sicilian Defense:
    # Najdorf Variation, English Attack" is a line, not a family, and grouping
    # a user's mistakes by line would split every cluster into ones.
    #
    # None means the position left book before it was ever classified, which is
    # ordinary for irregular openings. It never means "we did not look".
    opening_family: str | None = None
    opening_eco: str | None = None


EMPTY_GAME_CONTEXT = GameContext(
    previous_move_uci=None,
    previous_move_was_capture=False,
    previous_capture_square=None,
    user_castled=False,
    clock_before_move_seconds=None,
    clock_after_move_seconds=None,
    move_time_seconds=None,
    fen_before_move=None,
    plies_in_game=0,
)


def extract_game_context(
    pgn: str | None,
    ply: int,
    user_is_white: bool,
    time_control: TimeControl | None = None,
) -> GameContext:
    """Replay ``pgn`` and collect the facts surrounding the mistake at ``ply``.

    Args:
        pgn: The stored PGN blob. May be None, empty, or corrupt.
        ply: 1-based mainline index of the user's mistake move.
        user_is_white: Which side the user played.
        time_control: Used only to increment-adjust ``move_time_seconds``.

    Returns:
        A :class:`GameContext`. Missing or unparseable PGN yields
        :data:`EMPTY_GAME_CONTEXT` — an absent PGN degrades the diagnosis to the
        facts a FEN can carry, it does not fail it.
    """
    if not pgn or ply < 1:
        return EMPTY_GAME_CONTEXT

    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
    except (ValueError, UnicodeDecodeError):
        return EMPTY_GAME_CONTEXT
    if game is None:
        return EMPTY_GAME_CONTEXT

    user_color = chess.WHITE if user_is_white else chess.BLACK
    # Only positions at or before the mistake describe how the game got there.
    book_ply = min(ply, max_book_ply())
    increment = time_control.increment_seconds if time_control else 0

    board = game.board()
    opening_name: str | None = None
    opening_eco: str | None = None
    previous_move_uci: str | None = None
    previous_move_was_capture = False
    previous_capture_square: int | None = None
    user_castled = False
    clock_before: float | None = None
    clock_after: float | None = None
    fen_before_move: str | None = None
    plies = 0

    # Walk the whole mainline rather than breaking at ``ply``: it costs one pass
    # over a few hundred moves and yields plies_in_game without a second walk.
    for index, node in enumerate(game.mainline(), start=1):
        move = node.move
        if move is None:  # defensive: a malformed node
            break
        plies = index

        # ``board`` is the position *before* ``move``.
        if index == ply:
            fen_before_move = board.fen()

        if index == ply - 1:
            previous_move_uci = move.uci()
            previous_move_was_capture = board.is_capture(move)
            previous_capture_square = (
                move.to_square if previous_move_was_capture else None
            )

        if index < ply and board.turn == user_color and board.is_castling(move):
            user_castled = True

        # Longest-prefix classification, taken from the deepest position that
        # matches rather than the first. First-match would label every 1.e4
        # game "King's Pawn Game" and lose the family that actually mattered.
        # Bounded by max_book_ply so this stops walking the table mid-game.
        if index <= book_ply:
            hit = eco_classify(board.epd())
            if hit is not None:
                opening_eco, opening_name = hit

        clock = _node_clock(node)
        if clock is not None:
            if index == ply:
                clock_after = clock
            elif index == ply - 2:
                clock_before = clock

        board.push(move)

    # No user move preceded this one, so the clock stood at the base time.
    if clock_before is None and ply <= 2 and time_control and time_control.is_known:
        clock_before = float(time_control.base_seconds or 0)

    move_time: float | None = None
    if clock_before is not None and clock_after is not None:
        # Increment is credited after the move, so it is added back to recover
        # the wall time actually spent thinking. Clamped at zero: a negative
        # value means the PGN's clock tags are inconsistent, and reporting
        # "spent -3 seconds" as evidence would be worse than reporting nothing.
        spent = clock_before - clock_after + increment
        move_time = spent if spent >= 0 else None

    family = opening_name.split(":", 1)[0].strip() if opening_name else None

    return GameContext(
        previous_move_uci=previous_move_uci,
        previous_move_was_capture=previous_move_was_capture,
        previous_capture_square=previous_capture_square,
        user_castled=user_castled,
        clock_before_move_seconds=clock_before,
        clock_after_move_seconds=clock_after,
        move_time_seconds=move_time,
        fen_before_move=fen_before_move,
        plies_in_game=plies,
        opening_family=family,
        opening_eco=opening_eco,
    )


def _node_clock(node: chess.pgn.GameNode) -> float | None:
    """Read a node's ``[%clk]`` value, tolerating malformed comments.

    python-chess raises on some malformed clock comments rather than returning
    None; a single bad tag must not lose the whole game's context.
    """
    try:
        return node.clock()
    except (ValueError, TypeError):
        return None
