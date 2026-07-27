"""Background diagnosis job.

Walks a user's puzzles — every one of which is a blunder they played in a real
game — extracts the deterministic evidence packet, classifies the likely cause,
and stores the result.

No engine calls and no model calls: this is pure board analysis over data
already persisted, so a full-corpus backfill costs CPU and nothing else. The AI
stage layers on top later and enriches these rows rather than replacing them.

Resumability comes from the storage predicate, not from progress state — see
``storage/diagnosis_repository.py``. A run that is canceled, crashes, or stops
early simply leaves rows undone, and the next run finds them again.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from services.api.analytics_confidence import MIN_ATTEMPTS_FOR_MOTIF_RANK
from services.api.db import SessionLocal
from services.api.diagnosis.causes import classify_causes
from services.api.diagnosis.evidence import (
    EvidenceUnavailable,
    GameFacts,
    HistoryFacts,
    PuzzleFacts,
    evidence_hash,
    extract_evidence,
    to_evidence_items,
)
from services.api.diagnosis.pgn_context import (
    EMPTY_GAME_CONTEXT,
    extract_game_context,
    parse_time_control,
)
from services.api.models import (
    DiagnosisStatus,
    Game,
    Puzzle,
    PuzzleResult,
    PuzzleReview,
    PuzzleStats,
)
from services.api.storage.diagnosis_repository import (
    DiagnosisRepository,
    DiagnosisWrite,
)
from services.api.storage.game_repository import MANUAL_GAME_ID

logger = logging.getLogger(__name__)

# Puzzles analysed per job run. The default keeps a run short enough to stay
# responsive to cancellation; a backfill of a large corpus is expected to take
# several runs, which is fine because every run leaves the corpus strictly
# better diagnosed than it found it.
DIAGNOSIS_BATCH_DEFAULT = 200
DIAGNOSIS_BATCH_MAX = 5000

# Heartbeat/progress cadence. Both write to the jobs table, so doing either per
# puzzle would generate more write traffic than the analysis itself — the
# generator bounds them the same way.
HEARTBEAT_INTERVAL = 25

# Commit cadence. Without it the whole run is one transaction, so a crash at
# puzzle 4,999 of 5,000 would discard every diagnosis it had produced —
# "resumable" would then mean "starts over". Committing in chunks makes
# progress real and bounds how much a crash can cost.
COMMIT_INTERVAL = 50

MOTIF_HISTORY_DAYS = 30


def run_diagnosis(ctx) -> dict:
    """Job handler for :attr:`JobType.DIAGNOSIS`.

    Args:
        ctx: The worker's ``JobContext``. ``params`` accepts ``limit``.

    Returns:
        A result dict persisted to ``Job.result_json``.
    """
    limit = _clamp(
        ctx.params.get("limit", DIAGNOSIS_BATCH_DEFAULT), 1, DIAGNOSIS_BATCH_MAX
    )
    username = ctx.username

    diagnosed = unchanged = unavailable = 0
    canceled = False

    with SessionLocal() as db:
        repo = DiagnosisRepository(db)
        pending = repo.pending_puzzle_ids(username, limit)
        total = len(pending)
        if not total:
            return _result(username, db, repo, 0, 0, 0, False)

        motif_rates = _motif_fail_rates(db, username)

        for index, puzzle_id in enumerate(pending):
            # Bounded cadence: the lease still refreshes often enough that
            # crash recovery cannot mistake a live run for a dead one.
            if index % HEARTBEAT_INTERVAL == 0:
                if ctx.heartbeat():
                    canceled = True
                    logger.info("Diagnosis canceled for %s", username)
                    break
                ctx.progress(index, total)

            outcome = _diagnose_one(db, repo, username, puzzle_id, motif_rates)
            if outcome == "diagnosed":
                diagnosed += 1
            elif outcome == "unchanged":
                unchanged += 1
            else:
                unavailable += 1

            if (index + 1) % COMMIT_INTERVAL == 0:
                db.commit()

        # Flush whatever the last partial chunk produced, including on the
        # cancellation path — a cancel must keep the work already done, not
        # roll it back.
        db.commit()
        return _result(username, db, repo, diagnosed, unchanged, unavailable, canceled)


def _result(username, db, repo, diagnosed, unchanged, unavailable, canceled) -> dict:
    return {
        "username": username,
        "diagnosed": diagnosed,
        "unchanged": unchanged,
        "unavailable": unavailable,
        "remaining": repo.pending_count(username),
        "canceled": canceled,
    }


def _clamp(value, low: int, high: int) -> int:
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return low


def _diagnose_one(
    db: Session,
    repo: DiagnosisRepository,
    username: str,
    puzzle_id: str,
    motif_rates: dict[str, tuple[float, int]],
) -> str:
    # Puzzle and PuzzleStats are keyed on puzzle_id alone (a puzzle id belongs
    # to exactly one user by construction); Game is keyed on (game_id, username)
    # because the same canonical game is imported once per participant.
    puzzle = db.get(Puzzle, puzzle_id)
    if puzzle is None or puzzle.username != username:
        # Deleted between the scan and now, or never this user's to begin with.
        return "unavailable"

    stats = db.get(PuzzleStats, puzzle_id)
    motif = stats.primary_motif if stats else None
    game = db.get(Game, (puzzle.source_game_id, username))

    try:
        packet = extract_evidence(
            _puzzle_facts(puzzle),
            _game_facts(game, puzzle),
            _game_context(game, puzzle),
            _history_facts(stats, motif, motif_rates),
        )
    except EvidenceUnavailable as exc:
        # Record the negative result. Without a row, every future run would
        # re-attempt this same un-analysable puzzle forever.
        repo.upsert(
            DiagnosisWrite(
                puzzle_id=puzzle_id,
                username=username,
                status=DiagnosisStatus.UNAVAILABLE,
                error=str(exc),
                primary_motif=motif,
                insufficient_evidence=True,
            )
        )
        return "unavailable"

    digest = evidence_hash(packet)
    assessment = classify_causes(packet)
    primary = next(
        (c for c in assessment.candidates if c.cause == assessment.primary_cause),
        None,
    )
    _, changed = repo.upsert(
        DiagnosisWrite(
            puzzle_id=puzzle_id,
            username=username,
            status=DiagnosisStatus.OK,
            primary_motif=motif,
            primary_cause=assessment.primary_cause,
            secondary_causes=assessment.secondary_causes,
            primary_strength=primary.strength if primary else None,
            insufficient_evidence=assessment.insufficient_evidence,
            phase=packet.position.phase,
            evidence=tuple(
                {"id": item.id, "label": item.label, "value": item.value}
                for item in to_evidence_items(packet)
            ),
            evidence_hash=digest,
        )
    )
    return "diagnosed" if changed else "unchanged"


def _puzzle_facts(puzzle: Puzzle) -> PuzzleFacts:
    return PuzzleFacts(
        fen=puzzle.fen,
        played_move_uci=puzzle.played_move_uci,
        best_move_uci=puzzle.best_move_uci,
        ply=puzzle.ply,
        eval_before=puzzle.eval_before,
        eval_after=puzzle.eval_after,
        swing=puzzle.swing,
        accept_moves_uci=tuple(
            m for m in (puzzle.accept_moves_uci or "").split(",") if m
        ),
        solution_pv=tuple((puzzle.solution_pv or "").split()),
        confirmed_depth=puzzle.confirmed_depth,
    )


def _game_facts(game: Game | None, puzzle: Puzzle) -> GameFacts:
    """Assemble the game-level facts.

    ``user_is_white`` comes from the PUZZLE, not from matching the username
    against the game's players. A puzzle is by construction a position where it
    was the user's turn, so ``side_to_move`` is the authoritative answer, and it
    stays correct where the game row is not: manually added puzzles all record
    ``white_username = <the user>`` regardless of the position, so a
    black-to-move manual puzzle was being labelled as played with white — a fact
    contradicting its own FEN.
    """
    user_is_white = puzzle.side_to_move == "white"
    if game is None or game.game_id == MANUAL_GAME_ID:
        # No real game behind this position: no clock, no result, not rated.
        # The FEN still carries everything the board analysis needs, so the
        # diagnosis degrades rather than failing.
        return GameFacts(user_is_white=user_is_white)
    return GameFacts(
        user_is_white=user_is_white,
        time_control=parse_time_control(game.time_control),
        user_result=_user_result(game, user_is_white),
        rated=bool(game.rated),
    )


def _user_result(game: Game, user_is_white: bool) -> str | None:
    """ "win" / "loss" / "draw" from the pair of chess.com result strings.

    Chess.com marks exactly one side "win" in a decisive game and neither in a
    draw, so the outcome follows from that alone — no enumeration of the draw
    reasons ("agreed", "repetition", "stalemate", ...) is needed here.
    """
    mine = game.white_result if user_is_white else game.black_result
    theirs = game.black_result if user_is_white else game.white_result
    if mine == "win":
        return "win"
    if theirs == "win":
        return "loss"
    if mine and theirs:
        return "draw"
    return None


def _game_context(game: Game | None, puzzle: Puzzle):
    if game is None or not game.pgn_blob:
        return EMPTY_GAME_CONTEXT
    return extract_game_context(
        game.pgn_blob,
        ply=puzzle.ply,
        # Same authority as _game_facts: the puzzle position, not the game row.
        user_is_white=puzzle.side_to_move == "white",
        time_control=parse_time_control(game.time_control),
    )


def _history_facts(
    stats: PuzzleStats | None,
    motif: str | None,
    motif_rates: dict[str, tuple[float, int]],
) -> HistoryFacts:
    rate, sample = motif_rates.get(motif, (None, 0)) if motif else (None, 0)
    return HistoryFacts(
        puzzle_attempts=stats.attempts if stats else 0,
        puzzle_fail_count=stats.fail_count if stats else 0,
        motif=motif,
        motif_fail_rate_30d=rate,
        motif_sample_30d=sample,
    )


def _motif_fail_rates(
    db: Session, username: str
) -> dict[str, tuple[float | None, int]]:
    """30-day per-motif failure rate, computed once per run.

    Rates below ``MIN_ATTEMPTS_FOR_MOTIF_RANK`` are returned as None: the
    sample is too thin to state a rate, and the shared threshold in
    ``analytics_confidence`` is what decides that, not a number invented here.

    Counts all reviews, verified or not. Restricting to server-verified ones
    would discard most history; the stricter treatment belongs to pattern
    clustering, where a repeated *tendency* is claimed.
    """
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=MOTIF_HISTORY_DAYS
    )
    fails = func.sum(case((PuzzleReview.result == PuzzleResult.FAIL, 1), else_=0))
    stmt = (
        select(
            PuzzleStats.primary_motif.label("motif"),
            func.count().label("n"),
            fails.label("fails"),
        )
        .join(
            PuzzleStats,
            (PuzzleStats.puzzle_id == PuzzleReview.puzzle_id)
            & (PuzzleStats.username == PuzzleReview.username),
        )
        .where(
            PuzzleReview.username == username,
            PuzzleReview.reviewed_at >= since,
            PuzzleStats.primary_motif.isnot(None),
        )
        .group_by(PuzzleStats.primary_motif)
    )

    rates: dict[str, tuple[float | None, int]] = {}
    for row in db.execute(stmt).all():
        attempts = row.n or 0
        if attempts < MIN_ATTEMPTS_FOR_MOTIF_RANK:
            rates[row.motif] = (None, attempts)
        else:
            rates[row.motif] = ((row.fails or 0) / attempts, attempts)
    return rates
