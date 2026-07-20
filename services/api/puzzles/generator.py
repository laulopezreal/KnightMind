"""
Puzzle generation logic.

Analyzes user games to find blunders and generate puzzles.
Uses bounded compute to work on Render and similar platforms.
"""

import io
import logging
import os
from dataclasses import dataclass
from enum import Enum

import chess
import chess.pgn
from sqlalchemy.exc import SQLAlchemyError

from services.api.db import SessionLocal
from services.api.engine import (
    MATE_EVALUATION,
    StockfishEngineDeadError,
    StockfishError,
    StockfishNotFoundError,
    close_engine,
    create_engine,
    get_or_compute_eval,
    get_search_depth,
    get_top_moves,
)
from services.api.storage import GameRepository, PuzzleRepository

logger = logging.getLogger(__name__)


# How often (in plies) to heartbeat / check cancellation *within* a single
# game. The per-game check between games isn't enough on its own: one very deep
# game (or the initial bulk PGN load + first game) could otherwise outlast the
# crash-recovery lease and be falsely reset. Checking every N plies bounds the
# gap between heartbeats to N positions regardless of game length.
HEARTBEAT_PLY_INTERVAL = 10


def get_swing_threshold() -> float:
    """Get the minimum evaluation swing to consider a move a blunder."""
    return float(os.environ.get("SWING_THRESHOLD", "2.0"))


def get_equiv_tolerance() -> float:
    """Max eval gap (in pawns) for a move to count as equivalent to the best.

    Moves within this tolerance of the top move form the puzzle's acceptance
    set, so a solver is not marked wrong for a genuinely equal alternative.
    """
    return float(os.environ.get("PUZZLE_EQUIV_TOLERANCE", "0.3"))


def get_ply_range() -> tuple[int, int]:
    """Get the range of plies to analyze."""
    start = int(os.environ.get("MAX_PLIES_START", "8"))
    end = int(os.environ.get("MAX_PLIES_END", "80"))
    return start, end


def get_confirm_depth() -> int:
    """Depth for the puzzle-stability confirmation pass.

    A candidate flagged at the shallow scan depth (``STOCKFISH_DEPTH``, default
    12) is re-analyzed at this deeper depth (``STOCKFISH_CONFIRM_DEPTH``,
    default 18) before it is accepted, so engine noise at shallow depth cannot
    emit a low-quality puzzle whose best move flips or whose swing collapses
    under deeper search.

    Guard: the confirmation pass must never be *shallower* than the base scan,
    or it would "confirm" with a weaker analysis than the one that flagged the
    candidate. We clamp to ``max(configured, base_depth)``.
    """
    base = get_search_depth()
    raw = os.environ.get("STOCKFISH_CONFIRM_DEPTH")
    confirm = base + 6
    if raw:
        try:
            confirm = int(raw)
        except ValueError:
            logger.warning(
                "Invalid STOCKFISH_CONFIRM_DEPTH=%r, using default %s", raw, confirm
            )
    return max(confirm, base)


def get_pv_max_plies() -> int:
    """Maximum length (in plies) of the persisted solution principal variation.

    The generator walks the engine's best line from the puzzle FEN and stores it
    so the puzzle can be trained move-by-move. The walk is bounded by this cap
    (``PUZZLE_PV_MAX_PLIES``, default 8) and stops early at a mate / terminal
    position, so a single confirmed puzzle costs at most this many extra deep
    evals — most of which hit the version-aware cache (#199). Longer lines mean
    richer training but more generation compute; 8 plies (4 full moves) covers
    the overwhelming majority of forcing tactical combinations.
    """
    raw = os.environ.get("PUZZLE_PV_MAX_PLIES")
    if raw:
        try:
            value = int(raw)
            if value >= 1:
                return value
        except ValueError:
            logger.warning("Invalid PUZZLE_PV_MAX_PLIES=%r, using default 8", raw)
    return 8


def _compute_solution_pv(
    *,
    fen: str,
    first_move_uci: str,
    engine,
    depth: int,
    cache_stats: dict,
    max_plies: int,
) -> list[str]:
    """Walk the engine's best line from ``fen`` into a bounded UCI solution PV.

    The line always starts with ``first_move_uci`` (the confirmed solution move);
    each subsequent ply is the deep best move for the resulting position, played
    out until a mate/terminal position, an illegal/empty engine move, or the
    ``max_plies`` cap. Deep evals reuse ``get_or_compute_eval`` at the
    confirmation depth, so they hit the same version-aware cache the confirmation
    pass just populated.

    Best-effort by contract (mirrors ``_deep_top_moves``): ANY failure while
    walking — engine error, a mocked engine running out of scripted evals, an
    illegal move — simply stops the walk and returns the line built so far. It
    never raises, so PV enrichment can never break generation. Returns a list of
    at least one move (the solution); the caller stores it only when it is a real
    multi-ply line.
    """
    board = chess.Board(fen)
    try:
        first = chess.Move.from_uci(first_move_uci)
    except ValueError:
        return []
    if first not in board.legal_moves:
        return []

    line = [first_move_uci]
    board.push(first)

    for _ in range(max_plies - 1):
        if board.is_game_over():
            break
        try:
            result = get_or_compute_eval(
                board.fen(), engine=engine, cache_stats=cache_stats, depth=depth
            )
        except (
            Exception
        ) as e:  # noqa: BLE001 - best-effort, must never break generation
            logger.debug("PV walk eval failed for %s: %s", board.fen()[:40], e)
            break
        next_uci = result.best_move_uci
        if not next_uci:
            break
        try:
            move = chess.Move.from_uci(next_uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        line.append(next_uci)
        board.push(move)

    return line


def get_uniqueness_margin() -> float:
    """Minimum pawn advantage the solution must hold over the next NON-equivalent
    move for a candidate to be a real puzzle rather than a toss-up.

    Moves within ``PUZZLE_EQUIV_TOLERANCE`` of the best form the acceptance set
    (all correct). The *first move outside* that set must be at least this much
    worse (``PUZZLE_UNIQUENESS_MARGIN``, default 0.5 pawns), otherwise the
    position has no clearly-superior idea and solving it is a coin-flip.
    """
    return float(os.environ.get("PUZZLE_UNIQUENESS_MARGIN", "0.5"))


class GenerationStatus(str, Enum):
    """Outcome of a puzzle-generation run.

    Distinguishes conditions that previously all collapsed to "0 puzzles":
        - SUCCESS: engine ran and produced puzzles.
        - NO_MISTAKES: engine ran fine, no qualifying blunders were found.
        - ENGINE_UNAVAILABLE: the engine could not be created at all.
        - ALL_FAILED: every analyzed position failed to evaluate.
        - PARTIAL: some positions failed to evaluate (degraded run), whether or
          not any puzzles were produced.
    """

    SUCCESS = "success"
    NO_MISTAKES = "no_mistakes"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    ALL_FAILED = "all_failed"
    PARTIAL = "partial"


@dataclass
class GenerationResult:
    """Result of puzzle generation."""

    generated: int  # Number of new puzzles created
    skipped: int  # Number of duplicates skipped
    analyzed_positions: int  # Total positions analyzed
    cache_hits: int = 0  # Number of cache hits
    cache_misses: int = 0  # Number of cache misses
    failed_positions: int = 0  # Positions that raised a hard eval error
    # Positions whose evaluation TIMED OUT (engine subprocess killed). Tracked
    # separately from failed_positions because a timeout kills the shared batch
    # engine and is recovered by recreating it, but it is still a failure to
    # evaluate -- so it must feed PARTIAL/ALL_FAILED, never be silently dropped
    # into a fake NO_MISTAKES on a wedged host where every eval times out.
    timed_out: int = 0
    # --- Confirmation / stability metrics (audit gate 10) ---
    # A "candidate" is a position whose swing cleared the shallow threshold.
    # It becomes a puzzle only if the deeper confirmation pass upholds it.
    candidates_found: int = 0  # Cleared the shallow swing threshold
    candidates_confirmed: int = 0  # Passed every confirmation check
    discarded_unstable: int = 0  # Swing collapsed / best move flipped at depth
    discarded_low_margin: int = 0  # Solution was a toss-up (no uniqueness margin)
    # Machine-readable outcome so callers can tell engine-unavailable /
    # all-failed / no-mistakes / partial / success apart. Stored as a plain
    # string (via GenerationStatus.value) so it is JSON-serializable.
    status: str = GenerationStatus.SUCCESS.value

    @property
    def validity_rate(self) -> float:
        """Fraction of shallow candidates that survived confirmation.

        1.0 when no candidate was ever flagged (nothing invalid was emitted);
        otherwise confirmed / found. A low rate means the shallow scan is noisy
        relative to the confirmation depth.
        """
        if self.candidates_found == 0:
            return 1.0
        return self.candidates_confirmed / self.candidates_found

    @staticmethod
    def engine_unavailable() -> "GenerationResult":
        """A run that could not start because the engine was unavailable."""
        return GenerationResult(
            generated=0,
            skipped=0,
            analyzed_positions=0,
            status=GenerationStatus.ENGINE_UNAVAILABLE.value,
        )


def _terminal_eval_after(board: chess.Board) -> tuple[float, int | None]:
    """
    Evaluate a position that is game-over *after* the user's move, from the
    side-to-move (opponent) perspective, without invoking the engine.

    Returns (eval_pawns, mate_in). Checkmate means the user just delivered mate
    (opponent is mated -> -MATE for the opponent); stalemate / draw -> 0.0.
    This lets us capture "stalemated a won game" and "walked into mate"
    blunders that used to be dropped when the engine raised on a terminal FEN.
    """
    if board.is_checkmate():
        return -MATE_EVALUATION, 0
    return 0.0, None


def _build_accept_set(fen: str, engine, best_move_uci: str) -> list[str]:
    """
    Build the acceptance set of near-equivalent best moves for a position.

    Uses multi-PV so a solver who plays a move that is just as good as the
    stored best move is not marked wrong. Degrades gracefully to just the
    single best move if multi-PV is unavailable.
    """
    accept = [best_move_uci]
    try:
        top = get_top_moves(fen, engine=engine, k=3)
    except Exception as e:
        # Acceptance-set enrichment is best-effort: any multi-PV failure must
        # degrade to the single best move, never break generation.
        logger.debug("Multi-PV unavailable for %s: %s", fen[:40], e)
        return accept
    if not top:
        return accept

    tolerance = get_equiv_tolerance()
    best_eval = top[0].eval
    for candidate in top:
        if best_eval - candidate.eval <= tolerance and candidate.uci not in accept:
            accept.append(candidate.uci)
    return accept


@dataclass
class ConfirmationOutcome:
    """Result of the deeper stability/uniqueness confirmation of a candidate.

    ``accepted`` is True only when every check passed. On rejection ``reason``
    is one of ``"unstable"`` (swing collapsed or the best move flipped under
    deeper search) or ``"low_margin"`` (the solution is a toss-up). The eval /
    swing / accept-set fields reflect the *confirmation depth*, so a saved
    puzzle's provenance matches the analysis that vetted it -- not the shallow
    scan that first flagged it.
    """

    accepted: bool
    reason: str | None
    eval_before: float
    eval_after: float  # From the side-to-move (opponent) perspective, as evaluated
    swing: float
    best_move_uci: str | None
    accept_moves: list[str]
    confirmed_depth: int


def _deep_top_moves(fen: str, engine, depth: int, k: int = 3):
    """Best-effort deep multi-PV for the uniqueness check.

    Mirrors ``_build_accept_set``'s degrade-don't-break contract: any multi-PV
    failure (terminal position, engine quirk, mocked engine in tests) returns an
    empty list rather than raising, so an unavailable multi-PV never discards a
    genuine blunder -- the uniqueness check treats "can't assess" as unique.
    """
    try:
        return get_top_moves(fen, engine=engine, k=k, depth=depth) or []
    except Exception as e:  # noqa: BLE001 - best-effort, must never break generation
        logger.debug("Deep multi-PV unavailable for %s: %s", fen[:40], e)
        return []


def _assess_uniqueness(
    deep_top, best_move_uci: str | None, tolerance: float, margin: float
) -> tuple[list[str], bool]:
    """Derive the confirmed acceptance set and whether the puzzle is unique.

    The acceptance set is every deep move within ``tolerance`` of the top eval
    (all equally correct). The puzzle is a real one -- not a toss-up -- only if
    the FIRST move *outside* that set is at least ``margin`` pawns worse than
    the best. If no non-equivalent move is seen (multi-PV empty, or every
    returned move is equally good), we cannot prove a toss-up, so we treat the
    position as unique and accept the equivalent cluster.
    """
    accept: list[str] = []
    if best_move_uci:
        accept.append(best_move_uci)
    if not deep_top:
        return accept, True

    best_eval = deep_top[0].eval
    first_non_equiv_gap: float | None = None
    for candidate in deep_top:
        gap = best_eval - candidate.eval
        if gap <= tolerance:
            if candidate.uci not in accept:
                accept.append(candidate.uci)
        else:
            first_non_equiv_gap = gap
            break

    if first_non_equiv_gap is None:
        return accept, True
    return accept, first_non_equiv_gap >= margin


def _confirm_candidate(
    *,
    fen_before: str,
    fen_after: str,
    after_is_terminal: bool,
    board_after: chess.Board,
    engine,
    shallow_accept: list[str],
    swing_threshold: float,
    confirm_depth: int,
    uniqueness_margin: float,
    equiv_tolerance: float,
    cache_stats: dict,
) -> ConfirmationOutcome:
    """
    Re-analyze a shallow-flagged candidate at ``confirm_depth`` and decide
    whether it is a high-quality, stable puzzle.

    Confirms ALL of:
      (a) the swing is still >= ``swing_threshold`` at the confirmation depth;
      (b) the deep best move is STABLE -- it is still one of the shallow
          acceptance-set moves (the solution did not flip to an unrelated move);
      (c) a UNIQUENESS MARGIN -- the best move beats the next non-equivalent
          move by >= ``uniqueness_margin`` (the puzzle is not a toss-up).

    The deeper evals go through ``get_or_compute_eval`` with an explicit depth,
    so they reuse the version-aware cache (#199): a repeated confirmation pass
    on the same position is a cache hit and never clobbers the shallow entry.

    May raise ``StockfishEngineDeadError`` / ``StockfishError`` from the deep
    evals; the caller recreates the engine (dead) or discards the candidate.
    """
    # (1) Deep re-analysis of the MISTAKE position (cache-friendly). Gives the
    #     deep best move (stability) and deep eval_before (swing).
    deep_before = get_or_compute_eval(
        fen_before, engine=engine, cache_stats=cache_stats, depth=confirm_depth
    )
    deep_eval_before = deep_before.eval
    deep_best = deep_before.best_move_uci

    # (2) Deep eval of the resulting position for the confirmed swing. A
    #     terminal after-position is depth-independent (mate/draw is exact), so
    #     derive it from the board rather than paying an engine call.
    if after_is_terminal:
        deep_eval_after, _ = _terminal_eval_after(board_after)
    else:
        deep_eval_after = get_or_compute_eval(
            fen_after, engine=engine, cache_stats=cache_stats, depth=confirm_depth
        ).eval

    # eval is from side-to-move POV; flip eval_after (opponent's POV) to get the
    # swing in the original player's terms (same convention as the shallow scan).
    confirmed_swing = deep_eval_before - (-deep_eval_after)

    # (a) Swing must survive the deeper analysis.
    if confirmed_swing < swing_threshold:
        return ConfirmationOutcome(
            accepted=False,
            reason="unstable",
            eval_before=deep_eval_before,
            eval_after=deep_eval_after,
            swing=confirmed_swing,
            best_move_uci=deep_best,
            accept_moves=[],
            confirmed_depth=confirm_depth,
        )

    # (b) The deep best move must not flip to a move outside the shallow set.
    if deep_best is not None and deep_best not in shallow_accept:
        return ConfirmationOutcome(
            accepted=False,
            reason="unstable",
            eval_before=deep_eval_before,
            eval_after=deep_eval_after,
            swing=confirmed_swing,
            best_move_uci=deep_best,
            accept_moves=[],
            confirmed_depth=confirm_depth,
        )

    # (c) Uniqueness margin + confirmed acceptance set from the deep multi-PV.
    deep_top = _deep_top_moves(fen_before, engine, confirm_depth)
    accept_moves, is_unique = _assess_uniqueness(
        deep_top, deep_best, equiv_tolerance, uniqueness_margin
    )
    if not is_unique:
        return ConfirmationOutcome(
            accepted=False,
            reason="low_margin",
            eval_before=deep_eval_before,
            eval_after=deep_eval_after,
            swing=confirmed_swing,
            best_move_uci=deep_best,
            accept_moves=accept_moves,
            confirmed_depth=confirm_depth,
        )

    return ConfirmationOutcome(
        accepted=True,
        reason=None,
        eval_before=deep_eval_before,
        eval_after=deep_eval_after,
        swing=confirmed_swing,
        best_move_uci=deep_best,
        accept_moves=accept_moves,
        confirmed_depth=confirm_depth,
    )


def _recreate_batch_engine():
    """
    Recreate the shared batch engine after it died mid-batch (an eval timed out
    and its subprocess was killed). Returns a fresh engine, or None if it could
    not be recreated (in which case the caller should abort remaining work).
    """
    logger.warning(
        "Stockfish engine died mid-batch (timeout); recreating before continuing"
    )
    try:
        return create_engine()
    except Exception:
        logger.error(
            "Failed to recreate Stockfish engine; aborting remaining puzzle generation"
        )
        return None


def _is_user_move(
    board: chess.Board, username: str, white_username: str, black_username: str
) -> bool:
    """
    Check if it's the user's turn to move.

    Args:
        board: Current board position
        username: Username of the player
        white_username: White player's username
        black_username: Black player's username

    Returns:
        True if it's the user's turn
    """
    username_lower = username.lower()
    if board.turn == chess.WHITE:
        return white_username.lower() == username_lower
    else:
        return black_username.lower() == username_lower


def generate_puzzles(
    username: str,
    max_games: int = 30,
    max_puzzles: int = 30,
    cancellation_check: callable = None,
) -> GenerationResult:
    """
    Generate puzzles from a user's imported games by detecting blunders.

    Args:
        username: Username to generate puzzles for
        max_games: Maximum number of recent games to analyze
        max_puzzles: Maximum number of puzzles to generate

    Returns:
        GenerationResult with counts

    Raises:
        ValueError: If user has no games
    """
    with SessionLocal() as db:
        game_repository = GameRepository(db)
        puzzle_repository = PuzzleRepository(db)

        # Get user's games
        all_metadata = game_repository.get_all_metadata(username)
        if not all_metadata:
            raise ValueError(f"No games found for user '{username}'")

        # Take most recent games (already sorted by end_time descending)
        recent_games = all_metadata[:max_games]

        # Get configuration
        swing_threshold = get_swing_threshold()
        ply_start, ply_end = get_ply_range()
        confirm_depth = get_confirm_depth()
        uniqueness_margin = get_uniqueness_margin()
        equiv_tolerance = get_equiv_tolerance()
        pv_max_plies = get_pv_max_plies()

        # Create engine instance for the whole batch
        try:
            engine = create_engine()
        except Exception as e:
            # Engine could not be created. This is NOT the same as "found no
            # blunders": surface a distinct status so the job result does not
            # masquerade as a successful zero-puzzle run.
            logger.error("Engine unavailable, cannot generate puzzles: %s", e)
            return GenerationResult.engine_unavailable()

        # Ensure the batch engine subprocess is always terminated, even on
        # early return, exception, or cancellation.
        try:
            # Bulk-load PGNs keyed by game_id (one query per 1000 ids instead of
            # one per game). A dict rather than iter_pgns because each PGN must
            # stay paired with its game_id for save_puzzle(source_game_id=...);
            # memory is bounded by max_games PGN blobs (capped at the endpoint
            # and worker). Fetched only after the engine is known to be usable.
            pgns_by_game_id = game_repository.get_pgns(
                username, [game.game_id for game in recent_games]
            )

            generated = 0
            skipped = 0
            analyzed_positions = 0
            failed_positions = 0
            timed_out = 0
            candidates_found = 0
            candidates_confirmed = 0
            discarded_unstable = 0
            discarded_low_margin = 0
            cache_stats = {"hits": 0, "misses": 0}  # Track cache performance

            for game_meta in recent_games:
                # A prior game's engine died mid-batch and could not be
                # recreated. The inner move loop already broke out; end the whole
                # run cleanly here rather than iterating the remaining games with
                # a dead engine=None (which silently abandoned each game's plies
                # and spawned throwaway engines on every subsequent position).
                if engine is None:
                    break

                # Check for cancellation before processing each game
                if cancellation_check and cancellation_check():
                    logger.info(f"Puzzle generation canceled for {username}")
                    break

                if generated >= max_puzzles:
                    break

                # Load PGN (bulk-fetched above)
                pgn_text = pgns_by_game_id.get(game_meta.game_id)
                if not pgn_text:
                    continue

                # Parse game
                pgn_io = io.StringIO(pgn_text)
                game = chess.pgn.read_game(pgn_io)
                if not game:
                    continue

                # Get player usernames
                white_username = game.headers.get("White", "")
                black_username = game.headers.get("Black", "")

                # Walk through the game
                board = game.board()
                ply = 0

                for move in game.mainline_moves():
                    ply += 1

                    # Heartbeat + cancellation *within* the game so no single
                    # deep game can exceed the crash-recovery lease. The
                    # per-game check above only fires between games; this fires
                    # every HEARTBEAT_PLY_INTERVAL plies. On cancel we break the
                    # inner loop; the per-game check then ends the outer loop too.
                    if (
                        cancellation_check
                        and ply % HEARTBEAT_PLY_INTERVAL == 0
                        and cancellation_check()
                    ):
                        logger.info(f"Puzzle generation canceled for {username}")
                        break

                    # Skip moves outside the target range
                    if ply < ply_start or ply > ply_end:
                        board.push(move)
                        continue

                    # Only analyze when it's the user's move
                    if not _is_user_move(
                        board, username, white_username, black_username
                    ):
                        board.push(move)
                        continue

                    analyzed_positions += 1

                    # Get FEN before the move
                    fen_before = board.fen()
                    side_to_move = "white" if board.turn == chess.WHITE else "black"
                    played_move_uci = move.uci()

                    # Evaluate position before the move (with cache).
                    # fen_before is never terminal -- the user has a move to make.
                    # A dead engine (eval timeout) is recreated and the position
                    # skipped; a genuine engine failure is counted. In both cases
                    # the board is advanced so the rest of the game stays in sync
                    # -- we never swallow a failure into a fake success.
                    try:
                        eval_result_before = get_or_compute_eval(
                            fen_before, engine=engine, cache_stats=cache_stats
                        )
                    except StockfishEngineDeadError:
                        # The shared batch engine is dead; recreate before
                        # continuing, else every remaining position runs against
                        # a corpse (each stalling until the timeout fires again).
                        # Count the timeout so an all-timeout run reports
                        # ALL_FAILED, never a fake NO_MISTAKES.
                        timed_out += 1
                        engine = _recreate_batch_engine()
                        if engine is None:
                            break
                        board.push(move)
                        continue
                    except (StockfishError, StockfishNotFoundError) as e:
                        failed_positions += 1
                        logger.warning(
                            f"Failed to evaluate FEN {fen_before}", exc_info=e
                        )
                        board.push(move)
                        continue

                    eval_before = eval_result_before.eval
                    best_move_uci = eval_result_before.best_move_uci

                    # Make the user's move (board now advanced for the rest of
                    # the game).
                    board.push(move)
                    fen_after = board.fen()

                    # Evaluate position after the move. If the move ends the game
                    # (checkmate delivered, or a won game thrown away by
                    # stalemate), derive the eval directly from the board rather
                    # than asking the engine -- the engine raises on terminal
                    # FENs and those errors used to be swallowed, silently
                    # dropping the biggest blunders.
                    after_is_terminal = board.is_game_over()
                    try:
                        if after_is_terminal:
                            eval_after, _ = _terminal_eval_after(board)
                        else:
                            eval_after = get_or_compute_eval(
                                fen_after, engine=engine, cache_stats=cache_stats
                            ).eval
                    except StockfishEngineDeadError:
                        # Board is already advanced; recreate and move on. Count
                        # the timeout so a degraded/all-timeout run is surfaced
                        # (PARTIAL/ALL_FAILED) rather than swallowed.
                        timed_out += 1
                        engine = _recreate_batch_engine()
                        if engine is None:
                            break
                        continue
                    except (StockfishError, StockfishNotFoundError) as e:
                        failed_positions += 1
                        logger.warning(
                            f"Failed to evaluate FEN {fen_after}", exc_info=e
                        )
                        continue

                    # Calculate swing.
                    # eval is always from side-to-move perspective, so flip the
                    # sign of eval_after (it is from the opponent's perspective).
                    swing = eval_before - (-eval_after)

                    # Not a blunder at the shallow scan depth -> nothing to do.
                    if swing < swing_threshold:
                        continue

                    # A shallow candidate. Do NOT emit it yet: a single shallow
                    # pass is noisy, so re-analyze at a deeper depth and only
                    # keep candidates whose mistake+solution are STABLE and
                    # whose solution is clearly best (a real puzzle, not a
                    # toss-up). This is the audit-gate-10 quality gate.
                    candidates_found += 1

                    # Shallow acceptance set: the deep best move must stay within
                    # it, or the solution "flipped" to an unrelated move.
                    shallow_accept = _build_accept_set(
                        fen_before, engine, best_move_uci
                    )

                    try:
                        outcome = _confirm_candidate(
                            fen_before=fen_before,
                            fen_after=fen_after,
                            after_is_terminal=after_is_terminal,
                            board_after=board,
                            engine=engine,
                            shallow_accept=shallow_accept,
                            swing_threshold=swing_threshold,
                            confirm_depth=confirm_depth,
                            uniqueness_margin=uniqueness_margin,
                            equiv_tolerance=equiv_tolerance,
                            cache_stats=cache_stats,
                        )
                    except StockfishEngineDeadError:
                        # Confirmation eval killed the shared engine; recreate
                        # and move on (board already advanced). The candidate is
                        # left unconfirmed rather than emitted unvetted. Count the
                        # timeout too, so a run whose confirmations all time out
                        # is surfaced as degraded, not a clean NO_MISTAKES.
                        timed_out += 1
                        engine = _recreate_batch_engine()
                        if engine is None:
                            break
                        discarded_unstable += 1
                        continue
                    except (StockfishError, StockfishNotFoundError) as e:
                        # Could not confirm stability -> discard conservatively,
                        # never emit an unvetted puzzle.
                        discarded_unstable += 1
                        logger.warning(
                            "Confirmation failed for FEN %s", fen_before, exc_info=e
                        )
                        continue

                    if not outcome.accepted:
                        if outcome.reason == "low_margin":
                            discarded_low_margin += 1
                        else:
                            discarded_unstable += 1
                        continue

                    candidates_confirmed += 1

                    # Persist the CONFIRMED analysis (deeper depth): the deep
                    # best move, the confirmed swing/evals, the refined accept
                    # set, and confirmed_depth for auditability/reproducibility.
                    confirmed_best = outcome.best_move_uci or best_move_uci
                    accept_csv = ",".join(outcome.accept_moves) or None

                    # Persist the full forcing line (principal variation) so the
                    # puzzle trains move-by-move, not just move 1. Walk the deep
                    # best line from the puzzle FEN starting at the confirmed
                    # solution move. Store it only when it is a real multi-ply
                    # line (>= 2 plies: the solution + at least the forced reply);
                    # a lone solution move is legacy single-move training, so it
                    # stays NULL. Best-effort — a walk failure yields a short line
                    # and never blocks the save.
                    solution_line = _compute_solution_pv(
                        fen=fen_before,
                        first_move_uci=confirmed_best,
                        engine=engine,
                        depth=confirm_depth,
                        cache_stats=cache_stats,
                        max_plies=pv_max_plies,
                    )
                    solution_pv = (
                        " ".join(solution_line) if len(solution_line) >= 2 else None
                    )
                    # A DB error here must skip this one puzzle, not abort the
                    # whole batch (save_puzzle handles duplicate rows).
                    try:
                        is_new, _ = puzzle_repository.save_puzzle(
                            username=username,
                            source_game_id=game_meta.game_id,
                            ply=ply,
                            fen=fen_before,
                            side_to_move=side_to_move,
                            played_move_uci=played_move_uci,
                            best_move_uci=confirmed_best,
                            eval_before=outcome.eval_before,
                            # From the original player's POV (flip opponent POV).
                            eval_after=-outcome.eval_after,
                            swing=outcome.swing,
                            accept_moves_uci=accept_csv,
                            confirmed_depth=outcome.confirmed_depth,
                            solution_pv=solution_pv,
                        )
                    except SQLAlchemyError as e:
                        logger.warning(
                            "Failed to save puzzle for FEN %s",
                            fen_before,
                            exc_info=e,
                        )
                        continue

                    if is_new:
                        generated += 1
                        if generated >= max_puzzles:
                            break
                    else:
                        skipped += 1

            # Derive a machine-readable outcome from the counters. Any
            # evaluation failure -- a hard error OR a timeout that killed the
            # engine -- is surfaced (ALL_FAILED / PARTIAL) so a degraded run is
            # never silently reported as a clean NO_MISTAKES. Counting timeouts
            # here is what stops a fully-wedged host (every eval times out) from
            # masquerading as "no mistakes found".
            total_failed = failed_positions + timed_out
            if analyzed_positions > 0 and total_failed >= analyzed_positions:
                status = GenerationStatus.ALL_FAILED
            elif total_failed > 0:
                # Some positions failed/timed out (whether or not any puzzles
                # were produced); the run is degraded, not a clean pass.
                status = GenerationStatus.PARTIAL
            elif generated > 0:
                status = GenerationStatus.SUCCESS
            else:
                status = GenerationStatus.NO_MISTAKES

            return GenerationResult(
                generated=generated,
                skipped=skipped,
                analyzed_positions=analyzed_positions,
                cache_hits=cache_stats["hits"],
                cache_misses=cache_stats["misses"],
                failed_positions=failed_positions,
                timed_out=timed_out,
                candidates_found=candidates_found,
                candidates_confirmed=candidates_confirmed,
                discarded_unstable=discarded_unstable,
                discarded_low_margin=discarded_low_margin,
                status=status.value,
            )
        finally:
            close_engine(engine)
