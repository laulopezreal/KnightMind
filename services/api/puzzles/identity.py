"""
Puzzle identity logic.

Handles motif assignment, title generation, and backfilling identity data.
"""

import logging

import chess
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from services.api.models import PuzzleStats

logger = logging.getLogger(__name__)

# Motif to Title mapping
MOTIF_TITLES = {
    "back_rank": "Back Rank Panic",
    "hanging_queen": "The Hanging Queen",
    "hanging_piece": "Loose Piece",
    "fork": "The Fork",
    "pin": "Pinned and Lost",
    "mate_threat": "Missed Mate",
    "blunder": "The Missed Win",
}

# Piece values (in pawns) used for fork / hanging-piece heuristics.
_PIECE_VALUE = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,
}


def _classify_from_position(fen: str, best_move_uci: str) -> str:
    """
    Classify the tactical motif of the *solution* (best_move_uci) in a position.

    This is a deliberately basic, dependency-free classifier built only from
    the data we already persist (FEN before the mistake + the engine's best
    move). It inspects what the correct move accomplishes:

      - mate_threat: the best move is checkmate or forces the king into check
        as the decisive idea.
      - back_rank:   a rook/queen delivers mate/check on the opponent's back
        rank while the king is boxed in by its own pawns.
      - fork:        after the best move, a single piece attacks two or more
        higher-or-equal-value enemy pieces (including the king).
      - hanging_queen / hanging_piece: the best move captures an undefended
        enemy piece (queen gets its own bucket, it is the loudest blunder).
      - pin:         after the best move an enemy piece is pinned to its king.

    Falls back to "blunder" when nothing specific is detected, so the taxonomy
    is honest rather than dead code.
    """
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(best_move_uci)
    except (ValueError, TypeError):
        return "blunder"

    if move not in board.legal_moves:
        return "blunder"

    mover = board.turn
    captured = board.piece_at(move.to_square)
    board.push(move)

    # 1. Mate / mate threat -- the loudest motif, check first.
    if board.is_checkmate():
        if _is_back_rank_mate(board, mover):
            return "back_rank"
        return "mate_threat"

    # 2. Capturing an undefended enemy piece (the "loose piece" / hanging queen).
    if captured is not None:
        # After our capture it is the opponent's move; if they cannot recapture
        # on that square, the piece we took was hanging.
        if not board.attackers(not mover, move.to_square):
            if captured.piece_type == chess.QUEEN:
                return "hanging_queen"
            if captured.piece_type != chess.PAWN:
                return "hanging_piece"

    # 3. Fork: the moved piece now attacks two or more valuable enemy pieces.
    if _is_fork(board, move.to_square, mover):
        return "fork"

    # 4. Pin against the enemy king.
    if _creates_pin(board, mover):
        return "pin"

    return "blunder"


def _is_back_rank_mate(board: chess.Board, mover: chess.Color) -> bool:
    """True if the mated king sits on its back rank (a classic back-rank mate)."""
    mated = not mover  # side to move is the one checkmated
    king_sq = board.king(mated)
    if king_sq is None:
        return False
    back_rank = 0 if mated == chess.WHITE else 7
    return chess.square_rank(king_sq) == back_rank


def _is_fork(board: chess.Board, from_square: int, mover: chess.Color) -> bool:
    """True if the piece on ``from_square`` attacks 2+ valuable enemy pieces."""
    attacked_value_targets = 0
    for target in board.attacks(from_square):
        piece = board.piece_at(target)
        if piece is not None and piece.color != mover:
            if _PIECE_VALUE.get(piece.piece_type, 0) >= 3:
                attacked_value_targets += 1
    return attacked_value_targets >= 2


def _creates_pin(board: chess.Board, mover: chess.Color) -> bool:
    """True if any enemy piece is pinned to its king after our move."""
    enemy = not mover
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if (
            piece is not None
            and piece.color == enemy
            and piece.piece_type != chess.KING
        ):
            if board.is_pinned(enemy, square):
                return True
    return False


def assign_primary_motif(puzzle_data) -> str:
    """
    Assign a primary motif to a puzzle based on its position and solution.

    Args:
        puzzle_data: Puzzle object (or dict) exposing ``fen`` and
            ``best_move_uci``.

    Returns:
        The primary motif string (one of ``MOTIF_TITLES``). Defaults to
        "blunder" when no specific tactical motif can be identified or when the
        required fields are missing.
    """
    if puzzle_data is None:
        return "blunder"

    if isinstance(puzzle_data, dict):
        fen = puzzle_data.get("fen")
        best_move_uci = puzzle_data.get("best_move_uci")
    else:
        fen = getattr(puzzle_data, "fen", None)
        best_move_uci = getattr(puzzle_data, "best_move_uci", None)

    if not fen or not best_move_uci:
        return "blunder"

    return _classify_from_position(fen, best_move_uci)


def generate_puzzle_title(primary_motif: str) -> str:
    """
    Generate a human-readable title from a primary motif.

    Args:
        primary_motif: The primary motif string.

    Returns:
        The generated title string.
    """
    return MOTIF_TITLES.get(primary_motif, "Puzzle")


def backfill_puzzle_identity(db: Session):
    """
    Backfill missing identity data (title, primary_motif) for existing puzzles.
    Only updates records where title is NULL.
    """
    logger.info("Starting puzzle identity backfill check...")

    from services.api.storage import PuzzleRepository

    try:
        # query all stats where title is NULL
        stmt = select(PuzzleStats).where(PuzzleStats.title.is_(None))
        stats_to_update = db.scalars(stmt).all()
    except OperationalError:
        # Table may not exist yet (e.g. in test environments or before migrations)
        logger.warning("puzzle_stats table not available, skipping backfill.")
        db.rollback()
        return

    if not stats_to_update:
        logger.info("No puzzles need identity backfill.")
        return

    count = 0
    puzzle_repository = PuzzleRepository(db)

    # Imported here, not at module scope: position_names reads MOTIF_TITLES
    # from this module, so a top-level import would close the cycle.
    from services.api.puzzles.position_names import (
        PositionFacts,
        compose_position_name,
    )

    for stats in stats_to_update:
        # Load puzzle data to (potentially) determine motif
        puzzle = puzzle_repository.get_puzzle(stats.username, stats.puzzle_id)

        # Determine motif
        motif = assign_primary_motif(puzzle)

        # Name from the position rather than from the motif alone. The motif
        # table has seven strings in it, so naming from it gave every puzzle
        # that fell through to the default motif the same title.
        title = compose_position_name(
            PositionFacts(
                fen=getattr(puzzle, "fen", "") or "",
                best_move_uci=getattr(puzzle, "best_move_uci", "") or "",
                primary_motif=motif,
                move_number=(getattr(puzzle, "ply", 0) or 0) // 2 + 1,
            )
        )

        # Update DB
        stats.primary_motif = motif
        stats.title = title
        stats.title_source = "position"
        count += 1

    db.commit()
    logger.info(f"Backfilled identity for {count} puzzles.")
