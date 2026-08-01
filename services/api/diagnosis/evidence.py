"""
Deterministic evidence extraction for mistake diagnosis.

Turns a stored puzzle into a typed, citable packet of chess facts. Pure: board
analysis only, no database, no network, and — deliberately — **no engine calls**.
The generator already confirmed ``eval_before`` / ``eval_after`` / ``swing`` /
``solution_pv`` at ``confirmed_depth`` (see ``puzzles/generator.py``); re-running
Stockfish here would multiply the cost of the whole feature for facts we already
persist.

Two invariants this module exists to hold:

**No identity crosses this boundary.** :class:`EvidencePacket` has no username
field anywhere in its tree, so the redaction promise for the AI stage is a
property of the type rather than of a prompt instruction or a scrubbing pass.
Callers resolve identity into ``user_is_white`` before calling in.

**None means unknown, never zero.** A game without ``[%clk]`` tags must not read
as a game played with no time on the clock, and an unparseable time control must
not read as a bullet game. Every optional fact is emitted only when measured.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass

import chess

from services.api.diagnosis.pgn_context import (
    EMPTY_GAME_CONTEXT,
    UNKNOWN_TIME_CONTROL,
    GameContext,
    TimeControl,
)

# Bumped whenever extraction changes shape or meaning. Folded into
# ``evidence_hash`` so cached diagnoses self-invalidate on a change, the same
# discipline the FEN eval cache uses for its conversion/engine versions.
#
# 2: opening family. The packet now carries which opening the game reached,
#    which changes what the rules see and what the model may cite, so every
#    stored diagnosis predates evidence that could have changed it.
EXTRACTION_VERSION = 2

_PIECE_VALUE = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,  # never traded; counting it would swamp every material sum
}

# Non-pawn, non-king material summed across BOTH sides. A full board is 62
# (2 x [Q9 + 2R10 + 2B6 + 2N6]). The endgame floor of 13 is the point below
# which a side has at most a queen, or a rook and a minor, left — the standard
# "queen-and-pawns or less" boundary. The opening ceiling requires that almost
# nothing has been traded yet, so a queenless early middlegame is not mislabeled.
_ENDGAME_MATERIAL_CEILING = 13
_OPENING_MATERIAL_FLOOR = 52
_OPENING_PLY_CEILING = 24

# A piece is "loose" when nothing of its own colour defends it. Pawns are
# excluded: an undefended pawn is ordinary and would drown the real signal.
_LOOSE_MIN_VALUE = 3

# Minimum value for an enemy piece to count as a target of a move. The king is
# always a target regardless of this floor — it carries no material value here
# (see ``_PIECE_VALUE``) but a knight forking king and queen is the archetypal
# fork, and a value-only test would miss exactly that.
_ATTACK_TARGET_MIN_VALUE = 3

# Time pressure is relative to the format, with an absolute floor so a long
# game's 10%% (60s of a 10-minute game) does not read as a crisis while 12
# seconds of a 2-minute game does.
_TIME_PRESSURE_FLOOR_SECONDS = 15.0
_TIME_PRESSURE_FRACTION = 0.1


class EvidenceUnavailable(Exception):
    """Raised when a puzzle cannot be analysed at all.

    Only for genuinely unusable input — an unparseable FEN, or moves that are
    illegal in it. Missing *optional* context (no PGN, no clock, no history)
    degrades the packet instead; it never raises. Callers record the reason and
    skip the puzzle rather than storing a diagnosis built on nothing.
    """


# ---------------------------------------------------------------------------
# Inputs — assembled by the caller from ORM rows, so this module stays DB-free
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PuzzleFacts:
    """The persisted puzzle, as this module needs it."""

    fen: str
    played_move_uci: str
    best_move_uci: str
    ply: int
    eval_before: float
    eval_after: float
    swing: float
    accept_moves_uci: tuple[str, ...] = ()
    solution_pv: tuple[str, ...] = ()
    confirmed_depth: int | None = None


@dataclass(frozen=True)
class GameFacts:
    """The source game. ``user_is_white`` is the caller's resolved identity."""

    user_is_white: bool
    time_control: TimeControl = UNKNOWN_TIME_CONTROL
    user_result: str | None = None  # "win" | "loss" | "draw" | None
    rated: bool = False


@dataclass(frozen=True)
class HistoryFacts:
    """Prior review outcomes. All counts, no identity.

    ``motif_fail_rate_30d`` is None below a usable sample; the caller decides
    the threshold using ``analytics_confidence``, so this module never invents
    one of its own.
    """

    puzzle_attempts: int = 0
    puzzle_fail_count: int = 0
    motif: str | None = None
    motif_fail_rate_30d: float | None = None
    motif_sample_30d: int = 0


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoosePiece:
    square: str
    piece: str
    value: int
    attacked_by_opponent: bool


@dataclass(frozen=True)
class PositionFacts:
    fen: str
    side_to_move: str
    ply: int
    move_number: int
    phase: str  # "opening" | "middlegame" | "endgame"
    non_pawn_material: int


@dataclass(frozen=True)
class MoveFacts:
    uci: str
    san: str
    piece: str
    is_capture: bool
    is_check: bool
    is_promotion: bool
    is_quiet: bool
    captured_value: int


@dataclass(frozen=True)
class AttackFacts:
    """What the moved piece attacks once it lands.

    Measured on the position *after* the move, because that is what makes a
    fork a fork: the geometry that matters is the one the move creates, not the
    one it left behind.
    """

    targets: tuple[str, ...]
    target_count: int
    # Targets that nothing defends once the move has landed — the ones that
    # actually fall. The king is excluded: "undefended" is meaningless for it.
    loose_target_count: int
    includes_king: bool
    is_fork: bool


@dataclass(frozen=True)
class PlayedMoveFacts:
    move: MoveFacts
    # True when the played move recaptures on the square the opponent just
    # captured on — the signature of an automatic, unexamined reply.
    is_recapture: bool
    # What the user's own move threatened. Distinguishes "they were chasing
    # their own idea and missed the reply" from "they saw nothing at all".
    attacks: AttackFacts


@dataclass(frozen=True)
class BestMoveFacts:
    move: MoveFacts
    # The solution is forcing but ignores the square the opponent just captured
    # on: an in-between move the user never looked for.
    is_zwischenzug_like: bool
    # A single accepted solution means there was one path and the user missed
    # it; several means the position was forgiving and they still went astray.
    is_only_move: bool
    pv_length: int
    attacks: AttackFacts
    # The solution simply takes something nothing can recapture — the "it was
    # hanging and you didn't take it" case. Measured after the move, so an
    # x-ray defender that the capture removes is accounted for.
    captures_undefended: bool


@dataclass(frozen=True)
class LooseFacts:
    own: tuple[LoosePiece, ...]
    opponent: tuple[LoosePiece, ...]
    own_value: int
    own_count: int


@dataclass(frozen=True)
class ThreatFacts:
    legal_checks: int
    legal_captures: int
    # Forcing replies the opponent has *after* the played move — how sharply
    # the mistake was punishable.
    opponent_forcing_replies: int


@dataclass(frozen=True)
class KingFacts:
    user_castled: bool
    king_square: str
    ring_attackers: int
    escape_squares: int
    back_rank_boxed: bool


@dataclass(frozen=True)
class ClockFacts:
    # The reading the time-pressure judgement is made from, and the one the
    # citable evidence item quotes. Deriving both from ONE field is what stops
    # them diverging: an earlier version judged from a fallback but only
    # emitted an item for the after-move reading, so a diagnosis could claim
    # time pressure while carrying no clock fact to back it up.
    seconds_left: float | None
    seconds_left_before_move: float | None
    seconds_left_after_move: float | None
    move_time_seconds: float | None
    increment_seconds: int
    is_time_pressure: bool | None  # None when the clock is unknown


@dataclass(frozen=True)
class GameMetaFacts:
    time_control: str | None
    user_color: str
    user_result: str | None
    rated: bool
    plies_in_game: int
    # True when the PGN replay disagreed with the puzzle's stored FEN. All
    # PGN-derived facts are then dropped rather than attributed to the wrong
    # position — a desync is surfaced, never silently analysed.
    pgn_desync: bool
    # Opening family the game reached before the mistake, e.g. "Sicilian
    # Defense". None when the position left book unclassified, which is
    # ordinary for irregular openings — it never means "we did not look".
    # Dropped on desync along with every other PGN-derived fact: the whole
    # context is replaced with EMPTY_GAME_CONTEXT above, so no separate guard
    # is needed here and adding one would imply the drop is per-field.
    opening_family: str | None = None


@dataclass(frozen=True)
class EvidencePacket:
    """Everything the cause rules and the AI stage are allowed to reason from.

    Contains no username, account id, or email — see the module docstring.
    """

    position: PositionFacts
    played: PlayedMoveFacts
    best: BestMoveFacts
    loose: LooseFacts
    threats: ThreatFacts
    king: KingFacts
    clock: ClockFacts
    game: GameMetaFacts
    history: HistoryFacts
    eval_before: float
    eval_after: float
    swing: float
    confirmed_depth: int | None
    extraction_version: int = EXTRACTION_VERSION


@dataclass(frozen=True)
class EvidenceItem:
    """One citable fact. ``id`` is stable — the AI stage's citations are
    validated against these ids, so renaming one is a breaking change and must
    come with an ``EXTRACTION_VERSION`` bump."""

    id: str
    label: str
    value: str


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_evidence(
    puzzle: PuzzleFacts,
    game: GameFacts,
    context: GameContext = EMPTY_GAME_CONTEXT,
    history: HistoryFacts | None = None,
) -> EvidencePacket:
    """Build the evidence packet for one mistake.

    Raises:
        EvidenceUnavailable: the FEN is unparseable, or the played/best move is
            not legal in it.
    """
    try:
        board = chess.Board(puzzle.fen)
    except ValueError as exc:
        raise EvidenceUnavailable(f"unparseable FEN: {exc}") from exc

    played = _parse_move(board, puzzle.played_move_uci, "played")
    best = _parse_move(board, puzzle.best_move_uci, "best")

    # A PGN that does not replay to the stored FEN describes a different
    # position; its clock and previous-move facts would be actively misleading.
    # Drop them wholesale and record that we did.
    desync = (
        context.fen_before_move is not None and context.fen_before_move != board.fen()
    )
    if desync:
        context = EMPTY_GAME_CONTEXT

    user = board.turn
    history = history or HistoryFacts()

    return EvidencePacket(
        position=_position_facts(board, puzzle.ply),
        played=_played_facts(board, played, context),
        best=_best_facts(board, best, puzzle, context),
        loose=_loose_facts(board, user),
        threats=_threat_facts(board, played),
        king=_king_facts(board, user, context),
        clock=_clock_facts(context, game.time_control),
        game=GameMetaFacts(
            time_control=game.time_control.raw,
            user_color="white" if game.user_is_white else "black",
            user_result=game.user_result,
            rated=game.rated,
            plies_in_game=context.plies_in_game,
            pgn_desync=desync,
            opening_family=context.opening_family,
        ),
        history=history,
        eval_before=puzzle.eval_before,
        eval_after=puzzle.eval_after,
        swing=puzzle.swing,
        confirmed_depth=puzzle.confirmed_depth,
    )


def _parse_move(board: chess.Board, uci: str | None, which: str) -> chess.Move:
    if not uci:
        raise EvidenceUnavailable(f"missing {which} move")
    try:
        move = chess.Move.from_uci(uci)
    except ValueError as exc:
        raise EvidenceUnavailable(f"unparseable {which} move {uci!r}") from exc
    if move not in board.legal_moves:
        raise EvidenceUnavailable(f"illegal {which} move {uci!r} for position")
    return move


def _position_facts(board: chess.Board, ply: int) -> PositionFacts:
    material = _non_pawn_material(board)
    return PositionFacts(
        fen=board.fen(),
        side_to_move="white" if board.turn == chess.WHITE else "black",
        ply=ply,
        move_number=(ply + 1) // 2 if ply > 0 else board.fullmove_number,
        phase=_phase(ply, material),
        non_pawn_material=material,
    )


def _non_pawn_material(board: chess.Board) -> int:
    total = 0
    for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        count = len(board.pieces(piece_type, chess.WHITE)) + len(
            board.pieces(piece_type, chess.BLACK)
        )
        total += count * _PIECE_VALUE[piece_type]
    return total


def _phase(ply: int, non_pawn_material: int) -> str:
    """Classify game phase from material first, ply second.

    Material leads because it is what actually changes how a position must be
    played: a queenless position at move 12 is an endgame regardless of the
    move number, while a 40-move theoretical line is still an opening.
    """
    if non_pawn_material <= _ENDGAME_MATERIAL_CEILING:
        return "endgame"
    if ply <= _OPENING_PLY_CEILING and non_pawn_material >= _OPENING_MATERIAL_FLOOR:
        return "opening"
    return "middlegame"


def _move_facts(board: chess.Board, move: chess.Move) -> MoveFacts:
    piece = board.piece_at(move.from_square)
    is_capture = board.is_capture(move)
    is_check = board.gives_check(move)
    is_promotion = move.promotion is not None
    return MoveFacts(
        uci=move.uci(),
        san=board.san(move),
        piece=chess.piece_name(piece.piece_type) if piece else "unknown",
        is_capture=is_capture,
        is_check=is_check,
        is_promotion=is_promotion,
        is_quiet=not (is_capture or is_check or is_promotion),
        captured_value=_captured_value(board, move),
    )


def _captured_value(board: chess.Board, move: chess.Move) -> int:
    if not board.is_capture(move):
        return 0
    if board.is_en_passant(move):
        return _PIECE_VALUE[chess.PAWN]
    captured = board.piece_at(move.to_square)
    return _PIECE_VALUE.get(captured.piece_type, 0) if captured else 0


def _played_facts(
    board: chess.Board, move: chess.Move, context: GameContext
) -> PlayedMoveFacts:
    is_recapture = (
        context.previous_move_was_capture
        and context.previous_capture_square is not None
        and move.to_square == context.previous_capture_square
    )
    return PlayedMoveFacts(
        move=_move_facts(board, move),
        is_recapture=is_recapture,
        attacks=_attack_facts(board, move),
    )


def _attack_facts(board: chess.Board, move: chess.Move) -> AttackFacts:
    """Enumerate the valuable enemy pieces the moved piece attacks after landing."""
    mover = board.turn
    after = board.copy(stack=False)
    after.push(move)
    landing = move.to_square

    targets: list[str] = []
    loose_targets = 0
    includes_king = False

    for square in after.attacks(landing):
        piece = after.piece_at(square)
        if piece is None or piece.color == mover:
            continue
        if piece.piece_type == chess.KING:
            includes_king = True
        elif _PIECE_VALUE.get(piece.piece_type, 0) < _ATTACK_TARGET_MIN_VALUE:
            continue
        targets.append(
            f"{chess.piece_name(piece.piece_type)} on {chess.square_name(square)}"
        )
        # ``not mover`` is the target's own colour, so its attackers are its
        # defenders.
        if piece.piece_type != chess.KING and not after.attackers(not mover, square):
            loose_targets += 1

    return AttackFacts(
        targets=tuple(sorted(targets)),
        target_count=len(targets),
        loose_target_count=loose_targets,
        includes_king=includes_king,
        is_fork=len(targets) >= 2,
    )


def _best_facts(
    board: chess.Board,
    move: chess.Move,
    puzzle: PuzzleFacts,
    context: GameContext,
) -> BestMoveFacts:
    facts = _move_facts(board, move)
    zwischenzug = (
        (facts.is_check or facts.is_capture)
        and context.previous_move_was_capture
        and context.previous_capture_square is not None
        and move.to_square != context.previous_capture_square
    )
    # accept_moves is empty on legacy rows; a single-element set and an empty
    # one both mean "one known solution", so treat <= 1 as the only move.
    return BestMoveFacts(
        move=facts,
        is_zwischenzug_like=zwischenzug,
        is_only_move=len(puzzle.accept_moves_uci) <= 1,
        pv_length=len(puzzle.solution_pv),
        attacks=_attack_facts(board, move),
        captures_undefended=_captures_undefended(board, move),
    )


def _captures_undefended(board: chess.Board, move: chess.Move) -> bool:
    """True when the move takes a piece the opponent cannot recapture.

    Checked on the position *after* the capture, matching the motif classifier
    in ``puzzles/identity.py``: what makes a piece hanging is that no recapture
    exists once it has been taken, not merely that it looked undefended before.
    """
    if not board.is_capture(move):
        return False
    mover = board.turn
    after = board.copy(stack=False)
    after.push(move)
    return not after.attackers(not mover, move.to_square)


def _loose_facts(board: chess.Board, user: chess.Color) -> LooseFacts:
    own = _undefended_pieces(board, user)
    opponent = _undefended_pieces(board, not user)
    return LooseFacts(
        own=own,
        opponent=opponent,
        own_value=sum(p.value for p in own),
        own_count=len(own),
    )


def _undefended_pieces(
    board: chess.Board, color: chess.Color
) -> tuple[LoosePiece, ...]:
    loose: list[LoosePiece] = []
    for square, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        value = _PIECE_VALUE.get(piece.piece_type, 0)
        if value < _LOOSE_MIN_VALUE:
            continue
        if board.attackers(color, square):
            continue
        loose.append(
            LoosePiece(
                square=chess.square_name(square),
                piece=chess.piece_name(piece.piece_type),
                value=value,
                attacked_by_opponent=bool(board.attackers(not color, square)),
            )
        )
    loose.sort(key=lambda p: (-p.value, p.square))
    return tuple(loose)


def _threat_facts(board: chess.Board, played: chess.Move) -> ThreatFacts:
    checks = 0
    captures = 0
    for move in board.legal_moves:
        if board.gives_check(move):
            checks += 1
        if board.is_capture(move):
            captures += 1

    after = board.copy(stack=False)
    after.push(played)
    opponent_forcing = 0
    if not after.is_game_over():
        for move in after.legal_moves:
            if after.gives_check(move) or after.is_capture(move):
                opponent_forcing += 1

    return ThreatFacts(
        legal_checks=checks,
        legal_captures=captures,
        opponent_forcing_replies=opponent_forcing,
    )


def _king_facts(
    board: chess.Board, user: chess.Color, context: GameContext
) -> KingFacts:
    king_square = board.king(user)
    if king_square is None:  # not reachable in a legal position; stay defensive
        return KingFacts(
            user_castled=context.user_castled,
            king_square="",
            ring_attackers=0,
            escape_squares=0,
            back_rank_boxed=False,
        )

    ring = set(board.attacks(king_square)) | {king_square}
    attackers: set[int] = set()
    for square in ring:
        attackers |= set(board.attackers(not user, square))

    escapes = sum(1 for move in board.legal_moves if move.from_square == king_square)

    return KingFacts(
        user_castled=context.user_castled,
        king_square=chess.square_name(king_square),
        ring_attackers=len(attackers),
        escape_squares=escapes,
        back_rank_boxed=_back_rank_boxed(board, user, king_square),
    )


def _back_rank_boxed(board: chess.Board, color: chess.Color, king_square: int) -> bool:
    """Classic back-rank shape: king on its own first rank, every forward
    neighbour blocked by one of its own pawns.

    Deliberately stricter than "the king has no escape squares" — a king walled
    in by enemy pieces is a different problem with a different cause, and
    conflating them would let the back-rank label absorb every trapped king.
    """
    back_rank = 0 if color == chess.WHITE else 7
    if chess.square_rank(king_square) != back_rank:
        return False

    forward = 1 if color == chess.WHITE else -1
    file_index = chess.square_file(king_square)
    neighbours = [
        chess.square(f, chess.square_rank(king_square) + forward)
        for f in (file_index - 1, file_index, file_index + 1)
        if 0 <= f <= 7
    ]
    return all(
        (piece := board.piece_at(square)) is not None
        and piece.color == color
        and piece.piece_type == chess.PAWN
        for square in neighbours
    )


def _clock_facts(context: GameContext, time_control: TimeControl) -> ClockFacts:
    """Reduce the two clock readings to the one the diagnosis reasons from.

    Prefers the after-move reading: it is what the player was actually left
    with, which is the number that says whether they were in the danger zone.

    Known limitation: this cannot distinguish a player who was already
    scrambling from one who had ample time and burned it down to the same
    reading on this single move. ``move_time_seconds`` is the fact that
    separates them and is carried alongside; sharpening the rule to use it is
    tracked as follow-up work. The exposure is bounded because
    ``time_pressure_collapse`` is a modulator and can never be a primary cause.
    """
    remaining = context.clock_after_move_seconds
    if remaining is None:
        remaining = context.clock_before_move_seconds

    is_pressure: bool | None = None
    if remaining is not None and time_control.is_known:
        if time_control.is_correspondence:
            # Days per move: "low on time" is not a thing worth diagnosing.
            is_pressure = False
        else:
            threshold = max(
                _TIME_PRESSURE_FLOOR_SECONDS,
                (time_control.base_seconds or 0) * _TIME_PRESSURE_FRACTION,
            )
            is_pressure = remaining < threshold

    return ClockFacts(
        seconds_left=remaining,
        seconds_left_before_move=context.clock_before_move_seconds,
        seconds_left_after_move=context.clock_after_move_seconds,
        move_time_seconds=context.move_time_seconds,
        increment_seconds=time_control.increment_seconds,
        is_time_pressure=is_pressure,
    )


# ---------------------------------------------------------------------------
# Citable view + cache key
# ---------------------------------------------------------------------------


def to_evidence_items(packet: EvidencePacket) -> tuple[EvidenceItem, ...]:
    """Flatten the packet into the fact list the AI stage may cite.

    Only *measured* facts appear: an unknown clock produces no clock item, so a
    citation can never point at an absence. This is what makes citation
    validation meaningful rather than decorative.
    """
    items: list[EvidenceItem] = [
        EvidenceItem("position.phase", "Game phase", packet.position.phase),
        EvidenceItem(
            "position.move_number", "Move number", str(packet.position.move_number)
        ),
        EvidenceItem(
            "played.move",
            "Move played",
            f"{packet.played.move.san} "
            f"({'quiet' if packet.played.move.is_quiet else 'forcing'})",
        ),
        EvidenceItem(
            "best.move",
            "Best move",
            f"{packet.best.move.san} "
            f"({'quiet' if packet.best.move.is_quiet else 'forcing'})",
        ),
        EvidenceItem("eval.swing", "Evaluation swing (pawns)", f"{packet.swing:.2f}"),
        EvidenceItem(
            "eval.before",
            "Evaluation before the move (pawns)",
            f"{packet.eval_before:.2f}",
        ),
        EvidenceItem(
            "eval.after",
            "Evaluation after the move (pawns)",
            f"{packet.eval_after:.2f}",
        ),
        EvidenceItem(
            "threats.legal_checks",
            "Checks available to the user",
            str(packet.threats.legal_checks),
        ),
        EvidenceItem(
            "threats.legal_captures",
            "Captures available to the user",
            str(packet.threats.legal_captures),
        ),
        EvidenceItem(
            "threats.opponent_forcing_replies",
            "Forcing replies available to the opponent after the played move",
            str(packet.threats.opponent_forcing_replies),
        ),
        EvidenceItem(
            "king.ring_attackers",
            "Enemy pieces attacking the king's ring",
            str(packet.king.ring_attackers),
        ),
    ]

    if packet.game.opening_family:
        # Only when the game was actually classified. An unclassified position
        # emits nothing rather than "unknown", so a citation can never point at
        # an opening the game never reached.
        items.append(
            EvidenceItem(
                "game.opening_family",
                "Opening family",
                packet.game.opening_family,
            )
        )

    if packet.loose.own:
        items.append(
            EvidenceItem(
                "loose.own",
                "Undefended own pieces",
                ", ".join(f"{p.piece} on {p.square}" for p in packet.loose.own),
            )
        )
        items.append(
            EvidenceItem(
                "loose.own_value",
                "Total value of undefended own pieces",
                str(packet.loose.own_value),
            )
        )
    if packet.loose.opponent:
        items.append(
            EvidenceItem(
                "loose.opponent",
                "Undefended opponent pieces",
                ", ".join(f"{p.piece} on {p.square}" for p in packet.loose.opponent),
            )
        )
    if packet.best.attacks.target_count:
        items.append(
            EvidenceItem(
                "best.attacks",
                "Pieces the solution attacks",
                ", ".join(packet.best.attacks.targets),
            )
        )
    if packet.best.attacks.is_fork:
        items.append(
            EvidenceItem(
                "best.is_fork",
                "The solution attacks two or more pieces at once",
                "true",
            )
        )
    if packet.best.captures_undefended:
        items.append(
            EvidenceItem(
                "best.captures_undefended",
                "The solution captures a piece that cannot be recaptured",
                f"{packet.best.move.san} (value {packet.best.move.captured_value})",
            )
        )
    if packet.best.attacks.loose_target_count:
        items.append(
            EvidenceItem(
                "best.loose_targets",
                "Undefended pieces the solution attacks",
                str(packet.best.attacks.loose_target_count),
            )
        )
    if packet.played.attacks.target_count:
        items.append(
            EvidenceItem(
                "played.attacks",
                "Pieces the played move attacks",
                ", ".join(packet.played.attacks.targets),
            )
        )
    if packet.played.is_recapture:
        items.append(
            EvidenceItem(
                "played.is_recapture",
                "The played move was an immediate recapture",
                "true",
            )
        )
    if packet.best.is_zwischenzug_like:
        items.append(
            EvidenceItem(
                "best.is_zwischenzug_like",
                "The solution is an in-between move rather than the natural recapture",
                "true",
            )
        )
    if packet.best.pv_length:
        items.append(
            EvidenceItem(
                "best.pv_length",
                "Length of the solution line (plies)",
                str(packet.best.pv_length),
            )
        )
    if packet.king.back_rank_boxed:
        items.append(
            EvidenceItem(
                "king.back_rank_boxed",
                "The user's king is boxed in on the back rank by its own pawns",
                "true",
            )
        )
    # Emitted from the same field the time-pressure judgement reads, so
    # ``is_time_pressure`` can never be decided from a reading the packet does
    # not also make citable.
    if packet.clock.seconds_left is not None:
        items.append(
            EvidenceItem(
                "clock.seconds_left",
                "Seconds left on the user's clock",
                f"{packet.clock.seconds_left:.0f}",
            )
        )
    if packet.clock.move_time_seconds is not None:
        items.append(
            EvidenceItem(
                "clock.move_time",
                "Seconds spent on the move",
                f"{packet.clock.move_time_seconds:.0f}",
            )
        )
    if packet.history.puzzle_fail_count:
        items.append(
            EvidenceItem(
                "history.puzzle_fails",
                "Times this puzzle was failed in training",
                str(packet.history.puzzle_fail_count),
            )
        )
    if packet.history.motif_fail_rate_30d is not None:
        items.append(
            EvidenceItem(
                "history.motif_fail_rate",
                f"30-day failure rate for {packet.history.motif or 'this motif'}"
                f" over {packet.history.motif_sample_30d} attempts",
                f"{packet.history.motif_fail_rate_30d:.0%}",
            )
        )

    return tuple(items)


def evidence_hash(packet: EvidencePacket) -> str:
    """Stable content hash of the packet, used as part of the diagnosis cache key.

    Folds in ``EXTRACTION_VERSION`` so a change to what a field *means* — not
    just its value — invalidates cached diagnoses, matching the version-aware
    keying used by the FEN eval cache.
    """
    payload = json.dumps(
        dataclasses.asdict(packet), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(f"{EXTRACTION_VERSION}:{payload}".encode("utf-8")).hexdigest()
