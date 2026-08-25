"""Puzzle generation, browsing, review and per-puzzle diagnosis.

Fifth and largest slice of the main.py split -- twelve routes and the 35 models
and helpers used only by them, identified by walking the dependency closure of
the route handlers rather than by line range: they were scattered across twenty
blocks between lines 712 and 3031, interleaved with the jobs, users and import
routes.

Grouped by PATH, not by domain: /puzzles/{id}/diagnosis and
/puzzles/{id}/similar live here rather than with the diagnosis code, so
everything answering /puzzles/* is in one file. That matches how every other
router here is organised.

The solution-verification helpers (_verify_attempt, _verify_line,
_check_solution_move) are the reason review outcomes are server-verified rather
than trusted from the client, and _strip_solution is what keeps the browse
surface from echoing the answer.
"""

import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated, Literal

import chess
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_serializer
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from services.api.db import get_db
from services.api.diagnosis.causes import CAUSE_LABELS
from services.api.diagnosis.clusters import describe, humanise_cause, usable_motif
from services.api.identity import assert_owns_username, require_account
from services.api.jobs_routes import JobStatusResponse
from services.api.models import (
    Account,
    DiagnosisStatus,
    Job,
    JobStatus,
    JobType,
    PuzzleDiagnosis,
    PuzzleResult,
    PuzzleReview,
    PuzzleStats,
    TrainingSession,
)
from services.api.models import Game as GameModel
from services.api.models import Puzzle as PuzzleModel
from services.api.puzzles.provenance import resolve_display_name
from services.api.puzzles.resolution import (
    focus_is_visible,
    is_resolved,
    motif_is_visible,
    resolution_gate_enabled,
)
from services.api.ratelimit import rate_limit
from services.api.storage import PuzzleRepository, normalized_position
from services.api.storage.diagnosis_repository import DiagnosisRepository
from services.api.storage.game_repository import MANUAL_GAME_ID
from services.api.storage.spaced_repetition import (
    _utcnow_naive,
    get_adaptive_puzzles,
    get_all_puzzle_stats,
    get_puzzle_stats,
    get_trainable_puzzle_ids,
    insert_puzzle_review,
    update_puzzle_stats,
)
from services.api.usernames import Username, canonical_username

router = APIRouter(tags=["puzzles"])

# Per-principal rate limit (audit gate 10); see services/api/ratelimit.py.
RATE_LIMIT_PUZZLES_GENERATE = 5  # enqueues a heavy analysis job


_VALID_MOTIFS = frozenset(
    {
        "back_rank",
        "hanging_queen",
        "hanging_piece",
        "fork",
        "pin",
        "mate_threat",
        "blunder",
    }
)

# Fields that reveal (or strongly hint at) a puzzle's solution. They are
# stripped from every SCORED TRAINING payload so a client cannot pre-read the
# answer before making an attempt (audit gate 13 — closes the pre-exposure cheat
# vector left after gate 7's server-verified reviews). The training board gets
# live correct/incorrect feedback from POST /puzzles/{id}/check, and the
# solution only from POST /puzzles/{id}/reveal or a server-verified solve.
# played_move_uci (the original blunder) is included because it narrows the
# solution and the training client never needs it.
_SOLUTION_FIELDS = (
    "best_move_uci",
    "accept_moves_uci",
    "played_move_uci",
    # The full solution line is the answer too — never pre-ship it. The training
    # board learns the line one forced reply at a time via POST /check, and the
    # whole line only from POST /reveal or a server-verified full solve.
    "solution_pv",
)

_TRUTHY = {"1", "true", "yes", "on"}


class DailyPuzzlesResponse(BaseModel):
    puzzles: list[dict]
    count: int


class DailyPuzzleSessionRequest(BaseModel):
    username: Username
    n: int = 5


class DuePuzzlesResponse(BaseModel):
    due_count: int
    returned_count: int
    now: datetime
    puzzles: list[dict]


class PuzzleDiagnosisSummary(BaseModel):
    """Safe Library-list summary of a stored diagnosis.

    This deliberately excludes evidence/prose/recommendations and all move/line
    details. Full diagnosis evidence remains gated behind
    ``/puzzles/{id}/diagnosis`` after reveal.
    """

    state: Literal["ready", "unclear", "unavailable"]
    primary_cause: str | None = None
    primary_cause_label: str | None = None
    source: str | None = None
    diagnosed_at: datetime | None = None


class PuzzleListItem(BaseModel):
    id: str
    title: str | None
    # What the client should render. Equal to `title` wherever one exists,
    # which is everywhere today; it diverges once titles become NULL by
    # default (design §7). Optional for now so the list route -- which does not
    # yet join a game -- can omit it until step 1 finishes; the detail route
    # below always sets it.
    display_name: str | None = None
    primary_motif: str | None
    difficulty: str  # "easy" | "medium" | "hard"
    swing: float
    fen: str
    side_to_move: str
    # Solution fields are gated: they are populated only when the caller opts in
    # with ?reveal=true (owner asking to see the answer). Otherwise they are None
    # / empty so the Library browse surface can't passively echo the solution
    # into a scored /due session (dim 13).
    best_move_uci: str | None = None
    # Full set of accepted solutions (multi-PV equivalence set). Falls back to
    # [best_move_uci] for puzzles generated before this was persisted.
    accept_moves_uci: list[str] = []
    status: str  # "new" | "due" | "learning" | "mastered"
    attempts: int
    pass_count: int
    fail_count: int
    last_reviewed_at: datetime | None
    last_result: str | None
    next_due_at: datetime | None
    created_at: datetime | None
    diagnosis_summary: PuzzleDiagnosisSummary | None = None


def _puzzle_diagnosis_summary(
    row: PuzzleDiagnosis | None,
) -> PuzzleDiagnosisSummary | None:
    """Return the non-spoiler diagnosis subset safe for Library list rows."""

    if row is None:
        return None
    if row.status == DiagnosisStatus.UNAVAILABLE:
        return PuzzleDiagnosisSummary(
            state="unavailable",
            source=row.source,
            diagnosed_at=row.updated_at,
        )

    cause = row.user_confirmed_cause or row.primary_cause
    unclear = row.insufficient_evidence or not cause
    return PuzzleDiagnosisSummary(
        state="unclear" if unclear else "ready",
        primary_cause=cause,
        primary_cause_label=CAUSE_LABELS.get(cause) if cause else None,
        source=row.source,
        diagnosed_at=row.updated_at,
    )


class PuzzleCorpusStats(BaseModel):
    total: int
    due: int
    new: int
    learning: int
    mastered: int


class CauseOption(BaseModel):
    value: str
    label: str


class PuzzleListResponse(BaseModel):
    puzzles: list[PuzzleListItem]
    total: int
    limit: int
    offset: int
    available_motifs: list[str]
    available_causes: list[CauseOption] = []
    available_openings: list[str] = []
    stats: PuzzleCorpusStats


class ReviewRequest(BaseModel):
    username: Username
    result: PuzzleResult
    time_spent_ms: int | None = None
    session_id: str | None = None
    # Optional client-supplied idempotency key (stable per puzzle presentation).
    # A retried/double-submitted review with the same key is replayed without
    # re-counting stats/session or advancing scheduling.
    client_review_id: str | None = None
    # Optional UCI move the user actually played. When supplied, the SERVER
    # verifies it against the puzzle's accepted-solution set and computes the
    # authoritative pass/fail, ignoring the client's self-reported ``result``.
    # When omitted (legacy clients, timeouts, reveals) the review is recorded as
    # client-reported and NOT labelled verified.
    attempted_move: str | None = None


class CheckRequest(BaseModel):
    username: Username
    # The UCI move the user played on the board. Verified server-side; the
    # solution is never echoed back (audit gate 13).
    attempted_move: str
    # Index of this move within the solution line (an even ply: 0 for the first
    # move, 2 for the solver's second move, ...). Defaults to 0 so legacy
    # single-move clients keep working unchanged.
    ply_index: int = 0


class CheckResponse(BaseModel):
    # Server-authoritative live feedback for the training board. Reveals only
    # whether the played move solves the puzzle — NOT what the solution is.
    correct: bool
    result: str  # "pass" | "fail"
    # For a full-PV puzzle, the opponent's forced reply to a correct move (the
    # next PV ply). Safe to reveal — it is the forced response, not the solver's
    # upcoming answer, which is never sent. None for a wrong move, a legacy
    # single-move puzzle, or when the correct move was the last ply of the line.
    reply: str | None = None
    # True once the whole line is solved (or, for a legacy puzzle, on the one
    # correct move) — the client records the verified pass at this point.
    complete: bool = False
    # The solver's next move index in the line (ply_index + 2), so the client
    # knows which ply to check next. None when the line is complete.
    next_ply_index: int | None = None


class RevealRequest(BaseModel):
    username: Username


class RevealResponse(BaseModel):
    # Explicit "give up / show me" path. Returns the solution only when the
    # owner asks for it directly — the scored training payload never carries it.
    best_move_uci: str
    accept_moves_uci: list[str] = []
    # The full solution line (principal variation) as UCI moves, when the puzzle
    # has one. Empty for legacy single-move puzzles; the first move always equals
    # best_move_uci so a client can render either a single move or the whole line.
    solution_pv: list[str] = []


class ManualPuzzleRequest(BaseModel):
    # ``Username``, not ``str``: this was the one request model that still typed
    # the handle as a bare string, so "Save as puzzle" was the single live HTTP
    # path that could reach storage with a non-canonical key. It compensated
    # with a ``.lower()`` at the top of the handler, which is a DIFFERENT fold —
    # ``' Bob '`` lowercases to ``' bob '`` and writes a puzzle (plus its
    # PuzzleStats and its synthetic Game row) under a key no canonical read ever
    # matches. Annotating it puts this route on the same boundary as every other
    # one; the trade is that a whitespace-only handle now 422s instead of
    # writing a row under ``''``.
    username: Username
    fen: str
    title: str
    motif: str
    source: str | None = None
    solution_pv: str | None = None


class ManualPuzzleResponse(BaseModel):
    puzzle_id: str
    is_new: bool


@router.post(
    "/puzzles/generate",
    response_model=JobStatusResponse,
    dependencies=[
        Depends(
            rate_limit("puzzles_generate", default_limit=RATE_LIMIT_PUZZLES_GENERATE)
        )
    ],
)
def generate_puzzles_endpoint(
    username: Annotated[
        Username,
        Query(max_length=64, description="Username to generate puzzles for"),
    ],
    max_games: int = Query(
        30, ge=1, le=2000, description="Maximum number of recent games to analyze"
    ),
    max_puzzles: int = Query(
        30, ge=1, le=2000, description="Maximum number of puzzles to generate"
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Start a background job to generate puzzles."""
    assert_owns_username(account, username, db)
    try:
        new_job = Job(
            username=username,
            type=JobType.PUZZLE_GENERATION,
            status=JobStatus.QUEUED,
            message="Queued for generation",
            params={"max_games": max_games, "max_puzzles": max_puzzles},
        )
        db.add(new_job)
        db.commit()
        db.refresh(new_job)

        return JobStatusResponse(
            job_id=new_job.id, status=new_job.status, message="Job queued", progress=0
        )

    except IntegrityError as e:
        db.rollback()
        # Scope every lookup below to this job TYPE. The active-job index is
        # unique on (username, type), so the row we collided with is a
        # generation job specifically -- without the filter, a concurrently
        # running job of another type could be reported back as the caller's
        # generation job, handing them an id that will never produce puzzles.
        stmt = select(Job).where(
            Job.username == username,
            Job.type == JobType.PUZZLE_GENERATION,
            or_(Job.status == JobStatus.QUEUED, Job.status == JobStatus.RUNNING),
        )
        existing_job = db.scalars(stmt).first()

        if existing_job:
            return JobStatusResponse(
                job_id=existing_job.id,
                status=existing_job.status,
                message="Job already in progress",
                progress=existing_job.progress_current,
            )
        else:
            stmt = (
                select(Job)
                .where(
                    Job.username == username,
                    Job.type == JobType.PUZZLE_GENERATION,
                )
                .order_by(Job.created_at.desc())
            )
            latest_job = db.scalars(stmt).first()
            if latest_job:
                return JobStatusResponse(
                    job_id=latest_job.id,
                    status=latest_job.status,
                    message="Job completed recently",
                    progress=latest_job.progress_current,
                    result=latest_job.result_json,
                )
            raise HTTPException(
                status_code=500, detail="Could not create job or find existing one"
            ) from e


def _next_manual_ply(db: Session, username_lower: str) -> int:
    """Next sequential ply slot for a user's manual puzzles.

    Manual puzzles all share MANUAL_GAME_ID, so ``ply`` here is a synthetic
    per-user sequence number, not a board ply. Computed as ``max(ply) + 1``
    OUTSIDE the insert's transaction, so concurrent saves can compute the same
    value; ``create_manual_puzzle`` retries on the resulting unique-key
    collision (see its loop for why the retry does not belong in save_puzzle).
    """
    max_ply = db.scalar(
        select(func.max(PuzzleModel.ply)).where(
            PuzzleModel.username == username_lower,
            PuzzleModel.source_game_id == MANUAL_GAME_ID,
        )
    )
    return (max_ply + 1) if max_ply is not None else 0


@router.post("/puzzles/manual", response_model=ManualPuzzleResponse)
def create_manual_puzzle(
    request: ManualPuzzleRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Create a puzzle from an arbitrary position (Engine Analysis → Save as puzzle)."""
    assert_owns_username(account, request.username, db)
    # Folded, not merely aliased. The annotation makes this canonical today, but
    # this handler writes the synthetic ``games`` row itself (below) instead of
    # going through ``services/api/storage/`` — so the storage-boundary fold
    # that protects every other write does not reach it, and the annotation is
    # the ONLY thing standing between a non-canonical handle and a forked game
    # row. ``dev`` folded here; replacing that with a comment asserting
    # canonicality is the exact substitution this PR exists to undo.
    # Idempotent on annotated input, so no behaviour change today.
    username_lower = canonical_username(request.username)

    # Validate FEN
    try:
        board = chess.Board(request.fen)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid FEN") from err
    if board.is_game_over():
        raise HTTPException(
            status_code=400, detail="Position is already terminal — no puzzle possible"
        )
    if not list(board.legal_moves):
        raise HTTPException(status_code=400, detail="No legal moves in position")

    # Derive side_to_move from FEN (don't trust client field)
    side_to_move = "white" if board.turn == chess.WHITE else "black"

    # Normalize and validate motif
    motif = request.motif.strip().lower()
    if motif not in _VALID_MOTIFS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid motif. Must be one of: {', '.join(sorted(_VALID_MOTIFS))}",
        )

    # Validate and parse solution line (required; at least one UCI move)
    solution_pv_raw = (request.solution_pv or "").strip()
    if not solution_pv_raw:
        raise HTTPException(status_code=400, detail="Solution line is required")
    moves = solution_pv_raw.split()
    test_board = board.copy()
    for move_uci in moves:
        try:
            m = chess.Move.from_uci(move_uci)
        except ValueError as err:
            raise HTTPException(
                status_code=400, detail=f"Invalid UCI move in solution: {move_uci}"
            ) from err
        if m not in test_board.legal_moves:
            raise HTTPException(
                status_code=400, detail=f"Illegal move in solution: {move_uci}"
            )
        test_board.push(m)
    best_move_uci = moves[0]

    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    source_path = (request.source or "").strip() or None

    # Ensure synthetic game row exists; commit separately so the FK is in place
    # before the puzzle insert. GameRepository excludes MANUAL_GAME_ID from
    # corpus queries so this FK sentinel cannot look like an imported game.
    game = db.get(GameModel, (MANUAL_GAME_ID, username_lower))
    if not game:
        db.add(
            GameModel(
                game_id=MANUAL_GAME_ID,
                username=username_lower,
                url=f"manual://{username_lower}",
                white_username=username_lower,
                black_username="manual",
                white_result="manual",
                black_result="manual",
                time_control="manual",
                end_time=0,
                rated=False,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # Another concurrent request created it first

    puzzle_repo = PuzzleRepository(db)

    # Idempotency key is the normalized board POSITION, not the raw FEN. The raw
    # FEN carries halfmove/fullmove counters, so the same board reached via a
    # different move order (a transposition — routine in an analysis tool) has a
    # different raw FEN. Keying on the raw FEN inserted a permanent duplicate for
    # every transposition (there is no delete path), each carrying a PuzzleStats
    # row that inflated due_count forever. Keying on the position honours the
    # endpoint's "same position => same puzzle" contract.
    position_key = normalized_position(request.fen)

    # Manual puzzles all share MANUAL_GAME_ID, so the unique key
    # (username, source_game_id, ply) is really a per-user sequence. ply is
    # allocated as max(ply)+1 OUTSIDE the insert's transaction, so two concurrent
    # saves of DIFFERENT positions can compute the same ply; one insert wins, the
    # other raises IntegrityError. We must NOT resolve that loser to the winner's
    # id -- that silently drops the loser's saved position (the #268 bug). So:
    #   * same position already present     -> return it idempotently;
    #   * a DIFFERENT position took our ply -> reallocate ply and retry.
    # The retry lives here, not inside save_puzzle: the puzzle-generation path
    # passes a real board ply, where a ply collision genuinely means "this
    # position is already saved" and retrying with ply+1 would manufacture
    # duplicates. Only the synthetic manual sequence wants a fresh slot.
    def _existing_by_position() -> PuzzleModel | None:
        return db.scalars(
            select(PuzzleModel).where(
                PuzzleModel.username == username_lower,
                PuzzleModel.source_game_id == MANUAL_GAME_ID,
                PuzzleModel.normalized_position == position_key,
            )
        ).first()

    max_attempts = 8
    for _attempt in range(max_attempts):
        # Idempotent: the same position returns the same puzzle. Re-read every
        # iteration and immediately before the insert so it is the last read.
        existing = _existing_by_position()
        if existing:
            return ManualPuzzleResponse(puzzle_id=existing.id, is_new=False)

        ply = _next_manual_ply(db, username_lower)
        try:
            is_new, puzzle_id = puzzle_repo.save_puzzle(
                username=username_lower,
                source_game_id=MANUAL_GAME_ID,
                ply=ply,
                fen=request.fen,
                side_to_move=side_to_move,
                played_move_uci=best_move_uci,
                best_move_uci=best_move_uci,
                accept_moves_uci=best_move_uci,
                eval_before=0.0,
                eval_after=0.0,
                swing=0.0,
                solution_pv=solution_pv_raw,
                source_path=source_path,
                title=title,
                primary_motif=motif,
            )
        except IntegrityError:
            # A concurrent request committed THIS position (at some other ply)
            # between our precheck and our insert, so our insert violated the
            # partial unique index on the normalized position. save_puzzle keys
            # its own recovery off ply, so it could not resolve a position-index
            # violation and re-raised. Absorb it: return the winning row
            # idempotently instead of surfacing a 500.
            db.rollback()
            existing = _existing_by_position()
            if existing:
                return ManualPuzzleResponse(puzzle_id=existing.id, is_new=False)
            # No winner visible yet (a genuinely transient failure); retry.
            continue
        if is_new:
            return ManualPuzzleResponse(puzzle_id=puzzle_id, is_new=True)

        # save_puzzle found an existing row at our (username, MANUAL_GAME_ID, ply).
        # If it is our own position (raced in ahead of us) return it idempotently;
        # if a DIFFERENT position won the slot, loop to reallocate a fresh ply so
        # this save is not dropped.
        winner = db.get(PuzzleModel, puzzle_id)
        if winner is not None and winner.normalized_position == position_key:
            return ManualPuzzleResponse(puzzle_id=puzzle_id, is_new=False)

    # Exhausted retries under sustained contention. A clear, correct error is far
    # better than returning a phantom id; the client can safely retry the save.
    raise HTTPException(
        status_code=409,
        detail="Could not save puzzle due to concurrent updates; please retry.",
    )


@router.post("/daily-puzzle-sessions", response_model=DailyPuzzlesResponse)
def create_daily_puzzle_session(
    request: DailyPuzzleSessionRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Create a new daily puzzle session for a user."""
    username = request.username
    n = request.n

    assert_owns_username(account, username, db)

    # Validate n parameter
    if n < 1 or n > 20:
        raise HTTPException(
            status_code=400, detail="Number of puzzles must be between 1 and 20"
        )

    puzzle_repository = PuzzleRepository(db)

    # Get puzzles using the storage's selection logic
    puzzles = puzzle_repository.get_daily_puzzles(username, n)

    if not puzzles:
        raise HTTPException(
            status_code=404,
            detail=f"No puzzles found for user '{username}'. Generate puzzles first using POST /puzzles/generate",
        )

    # Mark puzzles as used today. Defer to the repository's UTC day default so
    # the write matches get_daily_puzzles' UTC read (dim 17): a server-local
    # date.today() here would disagree near the UTC/local midnight boundary and
    # break the used-today dedup/re-serve.
    puzzle_ids = [p.id for p in puzzles]
    puzzle_repository.mark_puzzles_used(username, puzzle_ids)

    # Reload specific puzzles to get updated used_on field
    # A new name, not a rebind: reassigning the filtered list to the same
    # variable keeps its declared type `list[Puzzle | None]`, so every later
    # read still looks nullable.
    fetched = [puzzle_repository.get_puzzle(username, pid) for pid in puzzle_ids]
    updated_puzzles = [p for p in fetched if p is not None]

    # Get puzzle stats to include primary_motif
    all_stats = get_all_puzzle_stats(db, username)

    # Convert to dict format for response and merge with stats
    end_times = _end_times_by_puzzle(db, username, updated_puzzles)
    puzzles_dict = []
    for p in updated_puzzles:
        p_dict = asdict(p)
        stats = all_stats.get(p.id)
        resolved = is_resolved(stats)
        if stats and resolved:
            p_dict["primary_motif"] = usable_motif(stats.primary_motif)
            p_dict["title"] = stats.title
        else:
            p_dict["primary_motif"] = None
            p_dict["title"] = None
        p_dict["display_name"] = resolve_display_name(
            title=stats.title if stats else None,
            end_time=end_times.get(p.id),
            ply=getattr(p, "ply", None),
            resolved=resolved,
        )
        # SCORED training path (post-generation warm-up): strip the solution so
        # it can't be pre-read before an attempt (audit gate 13).
        puzzles_dict.append(_strip_solution(p_dict))

    return DailyPuzzlesResponse(puzzles=puzzles_dict, count=len(puzzles_dict))


def _end_times_by_puzzle(db: Session, username: str, puzzles) -> dict[str, int]:
    """``{puzzle_id: game end_time}`` for a batch, in one query.

    The two dict-shaped payloads (`/puzzles/due` and the daily session) build
    their rows by iterating puzzles and looking stats up in a preloaded map,
    so provenance needs the same treatment: one query for the whole batch
    rather than a game fetch per puzzle. `/puzzles/due` is the scored training
    request, and an N+1 there is paid on every session start.

    Puzzles whose game is missing are simply absent from the map, and
    ``compose_provenance`` drops the date component for them.
    """
    game_ids = {p.source_game_id for p in puzzles if getattr(p, "source_game_id", None)}
    if not game_ids:
        return {}
    end_times: dict[str, int] = {
        game_id: end_time
        for game_id, end_time in db.execute(
            select(GameModel.game_id, GameModel.end_time).where(
                GameModel.username == username,
                GameModel.game_id.in_(game_ids),
            )
        ).all()
    }
    return {
        p.id: end_times[p.source_game_id]
        for p in puzzles
        if getattr(p, "source_game_id", None) in end_times
    }


def _queue_reason(
    stats,
    in_focus: bool,
    focus_name: str | None,
    now: datetime,
    resolved: bool = True,
) -> dict:
    """Why this puzzle is in today's queue.

    Reported per puzzle so the queue is inspectable rather than a black box:
    the user can see that a puzzle is here because it came due, because they
    have never seen it, or because it matches the pattern they chose to train.

    Deliberately no numeric score. The spec asked for one, but the ordering is
    already fully determined by facts that are *all* in this payload — the tier
    (due / new), the due date, and whether the puzzle matched the focus. A score
    would add a number that explains nothing the visible fields do not, and
    invites comparing two puzzles on a figure that was never calibrated for it.
    """
    if stats is None or stats.next_due_at is None:
        reason, explanation = "new", "You have not trained this position yet."
    else:
        due = stats.next_due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        days = max(0, (now - due).days)
        reason = "due"
        explanation = (
            "Due for review today."
            if days == 0
            else f"Due for review {days} day{'' if days == 1 else 's'} ago."
        )

    # A focus never replaces the scheduling reason — it explains the *order*,
    # not the presence. Saying "matches your focus" about a puzzle that is here
    # because it came due would misrepresent why it was served.
    #
    # Both the sentence and the `pattern` field name the DIAGNOSED CAUSE, so
    # they are revealing fields inside a scored pre-attempt payload (§4) --
    # which is exactly why a strip list naming only "motif and nickname" would
    # have missed them. Withheld for an unresolved puzzle; the scheduling half
    # of the explanation, which says only when it came due, always stays.
    if in_focus and focus_name and resolved:
        explanation = (
            f"{explanation} Matches the pattern you are training: {focus_name}."
        )

    payload = {"reason": reason, "explanation": explanation}
    if in_focus and focus_name and resolved:
        payload["pattern"] = focus_name
    if stats is not None and stats.fail_count:
        # Surfaced because a repeat failure is the strongest signal in the
        # corpus, and a user re-seeing a puzzle deserves to know it is a repeat.
        payload["previous_failures"] = stats.fail_count
    return payload


@router.get("/puzzles/due", response_model=DuePuzzlesResponse)
def get_due_puzzles_endpoint(
    username: Annotated[Username, Query(description="Username to get puzzles for")],
    n: int = Query(5, ge=1, le=20, description="Number of puzzles to return"),
    session_type: str = Query(
        "standard", description="Session type for adaptive selection"
    ),
    target_accuracy: float = Query(
        None, description="Target accuracy for adaptive selection"
    ),
    motif: str = Query(
        None, description="Filter puzzles by specific motif (e.g., 'Fork', 'Pin')"
    ),
    focus_opening: str = Query(
        None,
        description=(
            "Bias the order toward puzzles from this opening. Pass the full "
            "line; add focus_opening_scope=family to widen to the family. Like "
            "focus_cause this never narrows the session."
        ),
    ),
    focus_opening_scope: str = Query(
        "line", description="'line' (default) or 'family'"
    ),
    focus_cause: str = Query(
        None,
        description=(
            "Bias the order toward puzzles diagnosed with this mistake cause. "
            "Unlike `motif` this never narrows the session — it re-orders the "
            "puzzles that are already trainable."
        ),
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Get puzzles due for review, followed by new puzzles.
    Supports adaptive selection based on session type and target accuracy.
    Optionally filter by specific chess motif.

    ``focus_cause`` is opt-in and changes nothing unless passed: a user who
    never asks for a focused session gets the same queue as before. It is a
    bias rather than a filter so that clicking "train this pattern" on a day
    with nothing of that pattern due gives an ordinary session instead of an
    error.
    """
    assert_owns_username(account, username, db)
    puzzle_repository = PuzzleRepository(db)

    # 1. Load index to get all candidate IDs
    puzzles = puzzle_repository.get_all_puzzles(username)
    puzzle_ids = [p.id for p in puzzles]

    if not puzzle_ids:
        raise HTTPException(
            status_code=404,
            detail=f"No puzzles found for user '{username}'. Generate puzzles first.",
        )

    # 2. Filter by motif if specified
    if motif:
        # Query stats to filter by primary_motif
        motif_stmt = select(PuzzleStats.puzzle_id).where(
            PuzzleStats.username == username,
            PuzzleStats.primary_motif == motif,
            PuzzleStats.puzzle_id.in_(puzzle_ids),
        )
        filtered_ids = db.scalars(motif_stmt).all()

        if not filtered_ids:
            raise HTTPException(
                status_code=404,
                detail=f"No puzzles found for motif '{motif}'. Try a different motif.",
            )

        puzzle_ids = list(filtered_ids)

    # 3. Drop puzzles that are scheduled for a future date. Topping a session up
    #    with not-yet-due puzzles used to make "N puzzles due" a lie AND corrupt
    #    the intervals (an early review re-anchors next_due_at on today).
    #    See get_trainable_puzzle_ids.
    puzzle_ids = get_trainable_puzzle_ids(db, username, puzzle_ids)

    # 4. Resolve the focus to puzzle ids, if one was asked for. Done *after*
    #    the trainable narrowing above so the focus can only reorder what
    #    survived it — a focus must never make a not-yet-due puzzle due.
    focus_ids: set[str] = set()
    focus_name: str | None = None

    if focus_opening:
        # Same bias machinery as focus_cause: it reorders the already-trainable
        # set and can neither widen nor shorten the session.
        focus_ids |= DiagnosisRepository(db).puzzle_ids_for_opening(
            username, focus_opening, family=focus_opening_scope == "family"
        )
        focus_name = (
            focus_opening.split(":", 1)[0].strip()
            if focus_opening_scope == "family"
            else focus_opening
        )
    if focus_cause:
        from services.api.diagnosis.patterns import identify

        # Resolved through the same cause_breakdown the focus card uses, so the
        # two surfaces name the pattern identically. Resolving with phase=None
        # here instead meant a user clicking "Train 3 puzzles now" under "Back
        # Rank Neglect" saw every puzzle in the session attribute itself to
        # "King Safety Blind Spot" — the same naming drift the static table
        # exists to prevent, inside a single click.
        _repo = DiagnosisRepository(db)
        _stat = next(
            (s for s in _repo.cause_breakdown(username) if s.cause == focus_cause),
            None,
        )
        named = identify(focus_cause, _stat.dominant_phase if _stat else None)
        focus_name = named.name if named else None
        # `username` is already canonical — the Username type folds case at the
        # request boundary — so no re-folding here.
        focus_ids = DiagnosisRepository(db).puzzle_ids_for_cause(username, focus_cause)

    # 5. Get prioritized IDs and their stats using adaptive selection
    due_ids, all_stats = get_adaptive_puzzles(
        db, username, puzzle_ids, n, session_type, target_accuracy, focus_ids
    )

    # 3. Load content and merge with stats
    #
    # Loaded up front rather than inside the loop so provenance can resolve
    # every game in one query. The per-id fetches below were already happening;
    # this only reorders them so the batch is knowable before the payload is
    # built. Missing puzzles are dropped here exactly as they were before.
    loaded = [
        (pid, puzzle)
        for pid, puzzle in (
            (pid, puzzle_repository.get_puzzle(username, pid)) for pid in due_ids
        )
        if puzzle is not None
    ]
    end_times = _end_times_by_puzzle(db, username, [p for _, p in loaded])

    result_puzzles = []
    for pid, puzzle in loaded:
        p_dict = asdict(puzzle)
        stats = all_stats.get(pid)
        # /puzzles/due is the scored pre-attempt payload -- §4 calls it the
        # request that matters most, because it is the one the trainer reads
        # immediately before the user plays.
        due_resolved = is_resolved(stats)
        if stats:
            p_dict.update(
                {
                    "next_due_at": stats.next_due_at,
                    "interval_days": stats.interval_days,
                    "ease_factor": stats.ease_factor,
                    "attempts": stats.attempts,
                    "pass_count": stats.pass_count,
                    "fail_count": stats.fail_count,
                    "last_reviewed_at": stats.last_reviewed_at,
                    "last_result": stats.last_result,
                    "title": stats.title if due_resolved else None,
                    # §5: naming a motif reveals THAT motif, on the puzzles
                    # that have it. The nickname stays gated -- no intent in §5
                    # unlocks it, because a theme categorises the tactic while
                    # the nickname describes it.
                    "primary_motif": (
                        usable_motif(stats.primary_motif)
                        if motif_is_visible(
                            resolved=due_resolved,
                            puzzle_motif=stats.primary_motif,
                            requested_motif=motif,
                        )
                        else None
                    ),
                    "display_name": resolve_display_name(
                        title=stats.title,
                        end_time=end_times.get(pid),
                        ply=getattr(puzzle, "ply", None),
                        resolved=due_resolved,
                    ),
                }
            )
        else:
            # Default values for new puzzles
            p_dict.update(
                {
                    "next_due_at": None,
                    "interval_days": None,
                    "ease_factor": 2.0,
                    "attempts": 0,
                    "pass_count": 0,
                    "fail_count": 0,
                    "last_reviewed_at": None,
                    "last_result": None,
                    "title": None,
                    "primary_motif": None,
                    "display_name": resolve_display_name(
                        title=None,
                        end_time=end_times.get(pid),
                        ply=getattr(puzzle, "ply", None),
                        # No stats row at all, so nothing has been attempted.
                        resolved=False,
                    ),
                }
            )
        p_dict["queue_reason"] = _queue_reason(
            stats,
            pid in focus_ids,
            focus_name,
            datetime.now(timezone.utc),
            # The focus arrived in the request, so echoing it back reveals
            # nothing the caller did not supply -- and it unlocks exactly the
            # one cause or opening it names.
            resolved=focus_is_visible(
                resolved=due_resolved,
                focus_requested=bool(focus_cause or focus_opening),
                in_focus=pid in focus_ids,
            ),
        )
        # SCORED training path: never ship the solution up front.
        result_puzzles.append(_strip_solution(p_dict))

    # 5. Trainable count for metadata. `puzzle_ids` is already the trainable set
    #    (scoped to the motif filter when one was given), so this is the honest
    #    "how many could this request have served" number — the same predicate
    #    the /users/{username}/status due_count uses.
    now = datetime.now(timezone.utc)
    due_count = len(puzzle_ids)

    return {
        "due_count": due_count,
        "returned_count": len(result_puzzles),
        "now": now,
        "puzzles": result_puzzles,
    }


def _strip_puzzle_solutions_enabled() -> bool:
    """Whether the anti-cheat solution gate is turned on.

    Rollout flag — ``KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS`` (default OFF). Mirrors
    the ``KNIGHTMIND_REQUIRE_AUTH`` flag-reading pattern in ``identity.py``.

    OFF (default): solutions are INCLUDED in browse/training payloads —
    ``/puzzles/due`` & ``/daily-puzzle-sessions`` do NOT strip, and
    ``/puzzles/list`` & ``/puzzles/{id}`` include the solution regardless of
    ``?reveal``. This is the pre-audit behavior, backward-compatible with the
    old client-grading frontend and harmless to the new frontend (which grades
    via ``/check`` and ignores the extra fields). It lets the new API deploy
    before the new frontend is live, order-independently.

    ON: the strict anti-cheat behavior — strip on ``/due`` & ``/daily`` and gate
    ``/list`` & ``/{id}`` behind ``?reveal=true``. Flip it (no redeploy needed)
    once the new frontend is confirmed live.

    Server-side verification (``/check``, ``/reveal``, ``/review``) is unaffected
    by this flag either way.
    """
    return (
        os.environ.get("KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS", "").strip().lower()
        in _TRUTHY
    )


def _strip_solution(p_dict: dict) -> dict:
    """Remove solution-revealing fields from a training puzzle dict, in place.

    No-op unless ``KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS`` is enabled.
    """
    if not _strip_puzzle_solutions_enabled():
        return p_dict
    for field in _SOLUTION_FIELDS:
        p_dict.pop(field, None)
    return p_dict


def _swing_to_difficulty(swing: float) -> str:
    if swing < 2.0:
        return "easy"
    if swing < 5.0:
        return "medium"
    return "hard"


def _accept_moves(puzzle) -> list[str]:
    """Parse a puzzle's stored equivalence set, falling back to the best move.

    Older puzzles predate the accept_moves_uci column, so we always guarantee
    at least the single best move is accepted.
    """
    raw = getattr(puzzle, "accept_moves_uci", None)
    moves = [m for m in (raw or "").split(",") if m] if raw else []
    if puzzle.best_move_uci and puzzle.best_move_uci not in moves:
        moves.insert(0, puzzle.best_move_uci)
    return moves


def _verify_attempt(puzzle, attempted_move: str) -> PuzzleResult:
    """Server-authoritative pass/fail for a played move (audit gate 7).

    The move is parsed and checked for legality in the puzzle's FEN, then
    compared against the accepted-solution set (best move + multi-PV
    equivalents). A move that is illegal, malformed, or simply not in the
    accepted set is a FAIL — the server never trusts the client's claim here.
    Comparison is on normalised (lower-cased) UCI so casing can't smuggle a
    false pass.
    """
    candidate = (attempted_move or "").strip().lower()
    if not candidate:
        return PuzzleResult.FAIL

    # Legality: reject malformed or illegal moves outright.
    try:
        board = chess.Board(puzzle.fen)
        move = chess.Move.from_uci(candidate)
    except (ValueError, IndexError):
        return PuzzleResult.FAIL
    if move not in board.legal_moves:
        return PuzzleResult.FAIL

    accepted = {m.strip().lower() for m in _accept_moves(puzzle) if m}
    return PuzzleResult.PASS if candidate in accepted else PuzzleResult.FAIL


def _normalize_uci(move: str | None) -> str:
    """Lower-case, whitespace-trim a UCI move for comparison."""
    return (move or "").strip().lower()


def _solution_pv(puzzle) -> list[str]:
    """Parse a puzzle's persisted solution line into an ordered UCI list.

    The stored form is space-separated (comma tolerated) UCI moves starting with
    the solution move. Legacy puzzles have no line (NULL) and yield [], which the
    callers treat as single-move training. Even plies (0, 2, ...) are the solver's
    moves; odd plies are the opponent's forced replies.
    """
    raw = getattr(puzzle, "solution_pv", None)
    if not raw:
        return []
    return [m for m in raw.replace(",", " ").split() if m]


def _verify_line(puzzle, attempted_line: list[str]) -> PuzzleResult:
    """Server-authoritative pass/fail for a WHOLE solved line (full-PV puzzles).

    The line is solved only when the solver played every one of their plies
    (the even indices of the stored PV) correctly and in order — a wrong move at
    any ply fails the whole puzzle. The FIRST solver move (ply 0) accepts any
    move in the multi-PV equivalence set (best move + accept_moves_uci), since an
    equally-good opening move is a valid solve; every later ply must match the
    canonical forcing line exactly. Never trusts the client's claim (dim 11).
    """
    pv = _solution_pv(puzzle)
    if len(pv) < 2:
        # No real line to verify — fall back to single-move semantics on the
        # first supplied move (keeps a mis-routed call safe rather than crashing).
        first = attempted_line[0] if attempted_line else ""
        return _verify_attempt(puzzle, first)

    user_plies = [pv[i] for i in range(0, len(pv), 2)]
    if not attempted_line or len(attempted_line) != len(user_plies):
        return PuzzleResult.FAIL
    accepted_first = {m.strip().lower() for m in _accept_moves(puzzle) if m}
    for idx, (played, expected) in enumerate(
        zip(attempted_line, user_plies, strict=True)
    ):
        played_n = _normalize_uci(played)
        if idx == 0:
            # First move: any multi-PV equivalent is accepted.
            if played_n not in accepted_first:
                return PuzzleResult.FAIL
        elif played_n != _normalize_uci(expected):
            return PuzzleResult.FAIL
    return PuzzleResult.PASS


def _check_solution_move(
    puzzle, attempted_move: str, ply_index: int
) -> "CheckResponse":
    """Server-authoritative live feedback for one ply of a (possibly multi-move)
    solve, WITHOUT ever revealing the solver's upcoming answer.

    Legacy / single-move puzzles (no stored line) keep today's behaviour: verify
    against the accepted-solution set and report only correct/incorrect.

    Full-PV puzzles are validated ply-by-ply. ``ply_index`` is the solver's move
    index in the line (an even index). On a correct move the response carries the
    opponent's forced REPLY — the very next PV ply, which is safe to reveal
    because it is the forced response, not the solver's next answer — and whether
    the line is now complete. The solver's next move (ply_index + 2) is NEVER
    included, so the client cannot read ahead. A wrong move fails with no reply.
    """
    pv = _solution_pv(puzzle)

    # Legacy / single-move puzzle: accepted set, complete on the one correct move.
    if len(pv) < 2:
        result = _verify_attempt(puzzle, attempted_move)
        correct = result == PuzzleResult.PASS
        return CheckResponse(
            correct=correct,
            result=result.value,
            reply=None,
            complete=correct,
            next_ply_index=None,
        )

    # Full-PV puzzle: the solver only ever plays the even plies. Reject an
    # out-of-range or odd (opponent) index outright rather than trust it.
    if ply_index < 0 or ply_index >= len(pv) or ply_index % 2 != 0:
        return CheckResponse(
            correct=False, result=PuzzleResult.FAIL.value, reply=None, complete=False
        )

    if ply_index == 0:
        # First move: accept any multi-PV equivalent (best move + accept set);
        # later plies must match the exact forcing line (dim 11).
        accepted_first = {m.strip().lower() for m in _accept_moves(puzzle) if m}
        correct = _normalize_uci(attempted_move) in accepted_first
    else:
        correct = _normalize_uci(attempted_move) == _normalize_uci(pv[ply_index])
    if not correct:
        return CheckResponse(
            correct=False, result=PuzzleResult.FAIL.value, reply=None, complete=False
        )

    reply_index = ply_index + 1
    reply = pv[reply_index] if reply_index < len(pv) else None
    next_ply_index = ply_index + 2
    complete = next_ply_index >= len(pv)
    return CheckResponse(
        correct=True,
        result=PuzzleResult.PASS.value,
        reply=reply,
        complete=complete,
        next_ply_index=None if complete else next_ply_index,
    )


@router.get("/puzzles/list", response_model=PuzzleListResponse)
def list_puzzles(
    username: Annotated[Username, Query(description="Username to list puzzles for")],
    q: str = Query(None, description="Search by title or puzzle ID"),
    status: str = Query(None, description="Filter: new, due, learning, mastered"),
    motif: str = Query(
        None, description="Filter by primary_motif (comma-separated for OR)"
    ),
    cause: str = Query(
        None,
        description="Filter by diagnosed mistake cause (comma-separated for OR). "
        "This is what the Insights 'practise this' links target.",
    ),
    phase: str = Query(
        None, description="Filter by game phase: opening, middlegame, endgame"
    ),
    opening: str = Query(
        None, description="Filter by opening family, e.g. 'Sicilian Defense'"
    ),
    opening_line: str = Query(
        None,
        description=(
            "Filter by the full opening line, e.g. 'Sicilian Defense: Najdorf "
            "Variation'. Narrower than `opening`, which matches the whole family."
        ),
    ),
    difficulty: str = Query(None, description="Filter: easy, medium, hard"),
    sort: str = Query(
        "due_soonest",
        description="Sort: due_soonest, last_attempted, most_failed, difficulty_asc, difficulty_desc, newest",
    ),
    limit: int = Query(50, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    reveal: bool = Query(
        False,
        description="Include the solution (best_move_uci/accept_moves_uci). "
        "Off by default so the browse surface can't echo the answer.",
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    List all puzzles for a user with filtering, search, sorting, and pagination.
    Filtering, sorting, and pagination are performed in SQL for scalability.
    """
    from services.api.models import Puzzle as PuzzleModel

    assert_owns_username(account, username, db)

    # When the strip flag is OFF (default) the solution is always included so the
    # old client-grading frontend keeps working; ?reveal only matters when the
    # strict gate is ON.
    reveal_solution = reveal or not _strip_puzzle_solutions_enabled()
    # naive-UTC bound for SQL comparisons against naive next_due_at columns
    # (see spaced_repetition module note); an aware now would misclassify on
    # Postgres with a non-UTC session TimeZone.
    now = _utcnow_naive()
    # ``username`` is already canonical (folded at the request boundary); this
    # alias keeps the query readable without re-lowercasing.
    username_lower = username

    join_cond = (PuzzleModel.id == PuzzleStats.puzzle_id) & (
        PuzzleStats.username == username_lower
    )
    diagnosis_join_cond = (PuzzleModel.id == PuzzleDiagnosis.puzzle_id) & (
        PuzzleDiagnosis.username == username_lower
    )

    # --- 1. Corpus stats (unfiltered) ---
    status_case = case(
        (
            or_(PuzzleStats.puzzle_id.is_(None), PuzzleStats.attempts == 0),
            literal("new"),
        ),
        (
            and_(PuzzleStats.next_due_at.isnot(None), PuzzleStats.next_due_at <= now),
            literal("due"),
        ),
        (
            and_(
                PuzzleStats.attempts >= 3,
                (PuzzleStats.pass_count * 1.0 / PuzzleStats.attempts) >= 0.8,
            ),
            literal("mastered"),
        ),
        else_=literal("learning"),
    )

    corpus_stmt = (
        select(
            func.count().label("total"),
            func.sum(case((status_case == "new", 1), else_=0)).label("cnt_new"),
            func.sum(case((status_case == "due", 1), else_=0)).label("cnt_due"),
            func.sum(case((status_case == "learning", 1), else_=0)).label(
                "cnt_learning"
            ),
            func.sum(case((status_case == "mastered", 1), else_=0)).label(
                "cnt_mastered"
            ),
        )
        .select_from(PuzzleModel)
        .outerjoin(PuzzleStats, join_cond)
        .where(PuzzleModel.username == username_lower)
    )
    cr = db.execute(corpus_stmt).one()
    corpus_total = cr.total or 0

    # --- 2. Available motifs (unfiltered) ---
    motifs_stmt = (
        select(PuzzleStats.primary_motif)
        .join(PuzzleModel, PuzzleModel.id == PuzzleStats.puzzle_id)
        .where(
            PuzzleModel.username == username_lower,
            PuzzleStats.username == username_lower,
            PuzzleStats.primary_motif.isnot(None),
        )
        .distinct()
        .order_by(PuzzleStats.primary_motif)
    )
    available_motifs = [row[0] for row in db.execute(motifs_stmt).all()]

    # Same contract as available_motifs: the filter surface should offer only
    # values that would actually return something.
    causes_stmt = (
        select(
            func.coalesce(
                PuzzleDiagnosis.user_confirmed_cause, PuzzleDiagnosis.primary_cause
            )
        )
        .where(
            PuzzleDiagnosis.username == username_lower,
            PuzzleDiagnosis.status == DiagnosisStatus.OK,
        )
        .distinct()
    )

    # Carries its own label rather than a bare slug: the label table lives in
    # causes.py, and shipping a second copy to the frontend would let the two
    # drift the moment a cause is renamed.
    openings_stmt = (
        select(PuzzleDiagnosis.opening_family)
        .where(
            PuzzleDiagnosis.username == username_lower,
            PuzzleDiagnosis.status == DiagnosisStatus.OK,
            PuzzleDiagnosis.opening_family.isnot(None),
        )
        .distinct()
    )
    available_openings = sorted(row[0] for row in db.execute(openings_stmt).all())

    available_causes = [
        CauseOption(value=value, label=CAUSE_LABELS.get(value, value))
        for value in sorted(row[0] for row in db.execute(causes_stmt).all() if row[0])
    ]

    # --- 3. Build filtered query ---
    # Reuse status_case from corpus stats so status logic is defined once.
    computed_status = status_case.label("computed_status")

    base_stmt = (
        select(PuzzleModel, PuzzleStats, PuzzleDiagnosis, GameModel, computed_status)
        .outerjoin(PuzzleStats, join_cond)
        .outerjoin(PuzzleDiagnosis, diagnosis_join_cond)
        # Provenance needs the game's end_time. On the join rather than a
        # lookup per row: this route is paginated but still returns N rows, and
        # a per-row `db.get` would be an N+1 on the Library's main list.
        # OUTER, so a puzzle whose game is missing keeps its place in a page
        # whose total was counted separately -- it just loses the date.
        .outerjoin(
            GameModel,
            (GameModel.game_id == PuzzleModel.source_game_id)
            & (GameModel.username == PuzzleModel.username),
        )
        .where(PuzzleModel.username == username_lower)
    )

    # Search filter.
    #
    # Title, opening and id -- NOT the composed provenance string. Provenance
    # is derived, never stored (design §3), so matching "12 Mar · Sicilian ·
    # move 18" as text would mean composing it in SQL. The opening is the one
    # provenance component that IS stored, and it is the one worth searching:
    # "sicilian" is a thing a user types, "move 18" is not.
    #
    # This matters more after rollout step 6 than it does today. With titles
    # NULL by default, a title-only predicate silently degrades to hex-id
    # search while the box still invites a name -- so the opening term is what
    # keeps the feature meaningful, and the placeholder says what is covered
    # rather than implying more.
    #
    # The gate reaches this predicate, not just the payload. A withheld
    # nickname that is still matchable lets a user confirm what a puzzle is
    # called without being shown it -- a slower version of showing it. So the
    # title term applies only to resolved rows.
    #
    # Expressed in SQL rather than by filtering afterwards, because this runs
    # before LIMIT: post-filtering would return short pages and a wrong total.
    # The opening and id terms stay ungated on purpose -- the opening IS
    # provenance, which is never withheld, and an id reveals nothing.
    if q:
        q_pattern = f"%{q.lower()}%"
        title_match: ColumnElement[bool] = func.lower(PuzzleStats.title).like(q_pattern)
        if resolution_gate_enabled():
            title_match = and_(
                title_match,
                PuzzleStats.attempts > 0,
                or_(
                    PuzzleStats.next_due_at.is_(None),
                    # NAIVE, matching the column. An aware bound makes Postgres
                    # reinterpret the naive column through the session
                    # TimeZone, shifting the boundary by the offset -- the rule
                    # spaced_repetition documents and the bug the worker
                    # heartbeat already paid for once. This function computed
                    # `now = _utcnow_naive()` 150 lines up for exactly this and
                    # the first version of this predicate ignored it, so the
                    # SQL and Python halves of one gate could disagree.
                    PuzzleStats.next_due_at > now,
                ),
            )
        base_stmt = base_stmt.where(
            or_(
                title_match,
                func.lower(PuzzleDiagnosis.opening_name).like(q_pattern),
                func.lower(PuzzleModel.id).like(q_pattern),
            )
        )

    # Status filter (uses the same CASE expression as corpus stats)
    if status:
        base_stmt = base_stmt.where(status_case == status)

    # Motif filter
    if motif:
        motif_values = [m.strip().lower() for m in motif.split(",")]
        base_stmt = base_stmt.where(
            func.lower(PuzzleStats.primary_motif).in_(motif_values)
        )

    # Mistake-cause filter. Joins the diagnosis rather than the motif: a motif
    # says what was on the board, a cause says why it was missed, and the
    # Insights cards send users here by cause.
    #
    # The predicate is deliberately identical to the one behind the Insights
    # counts (``DiagnosisRepository.cause_counts``): correction over computed
    # cause, analysable rows only. A user who clicks "8 mistakes · practise
    # this" must land on 8 puzzles, and that holds by construction rather than
    # by the two queries happening to agree.
    if cause:
        cause_values = [c.strip().lower() for c in cause.split(",") if c.strip()]
        diagnosis_cause = func.coalesce(
            PuzzleDiagnosis.user_confirmed_cause, PuzzleDiagnosis.primary_cause
        )
        base_stmt = base_stmt.where(
            PuzzleDiagnosis.status == DiagnosisStatus.OK,
            func.lower(diagnosis_cause).in_(cause_values),
        )

    # Phase and opening: both live on the diagnosis, both analysable rows only,
    # so they compose with the cause filter rather than fighting it.
    if phase:
        base_stmt = base_stmt.where(
            PuzzleDiagnosis.status == DiagnosisStatus.OK,
            func.lower(PuzzleDiagnosis.phase) == phase.strip().lower(),
        )

    if opening_line:
        base_stmt = base_stmt.where(
            PuzzleDiagnosis.status == DiagnosisStatus.OK,
            func.lower(PuzzleDiagnosis.opening_name) == opening_line.strip().lower(),
        )

    if opening:
        base_stmt = base_stmt.where(
            PuzzleDiagnosis.status == DiagnosisStatus.OK,
            func.lower(PuzzleDiagnosis.opening_family) == opening.strip().lower(),
        )

    # Difficulty filter
    if difficulty:
        d = difficulty.lower()
        if d == "easy":
            base_stmt = base_stmt.where(PuzzleModel.swing < 2.0)
        elif d == "medium":
            base_stmt = base_stmt.where(
                PuzzleModel.swing >= 2.0, PuzzleModel.swing < 5.0
            )
        elif d == "hard":
            base_stmt = base_stmt.where(PuzzleModel.swing >= 5.0)

    # --- 4. Total count (filtered, before pagination) ---
    count_stmt = select(func.count()).select_from(
        base_stmt.with_only_columns(PuzzleModel.id).subquery()
    )
    total = db.scalar(count_stmt) or 0

    # --- 5. Sort ---
    if sort == "due_soonest":
        sort_priority = case(
            (
                and_(
                    PuzzleStats.next_due_at.isnot(None), PuzzleStats.next_due_at <= now
                ),
                literal(0),
            ),
            (
                or_(PuzzleStats.puzzle_id.is_(None), PuzzleStats.attempts == 0),
                literal(1),
            ),
            else_=literal(2),
        )
        base_stmt = base_stmt.order_by(
            sort_priority,
            case((PuzzleStats.next_due_at.is_(None), 1), else_=0),
            PuzzleStats.next_due_at.asc(),
        )
    elif sort == "last_attempted":
        base_stmt = base_stmt.order_by(
            case((PuzzleStats.last_reviewed_at.isnot(None), 0), else_=1),
            PuzzleStats.last_reviewed_at.desc(),
        )
    elif sort == "most_failed":
        base_stmt = base_stmt.order_by(func.coalesce(PuzzleStats.fail_count, 0).desc())
    elif sort == "difficulty_asc":
        base_stmt = base_stmt.order_by(PuzzleModel.swing.asc())
    elif sort == "difficulty_desc":
        base_stmt = base_stmt.order_by(PuzzleModel.swing.desc())
    elif sort == "newest":
        base_stmt = base_stmt.order_by(PuzzleModel.created_at.desc())

    # --- 6. Paginate ---
    base_stmt = base_stmt.limit(limit).offset(offset)

    # --- 7. Build response ---
    rows = db.execute(base_stmt).all()
    result_puzzles = []
    for puzzle, stats, diagnosis, game, row_status in rows:
        row_resolved = is_resolved(stats)
        result_puzzles.append(
            PuzzleListItem(
                id=puzzle.id,
                # `title` is gated identically to display_name. Gating only
                # display_name would withhold the nickname from the field the
                # client renders while still shipping it in the field beside
                # it -- the gate would be decorative.
                title=(stats.title if (stats and row_resolved) else None),
                display_name=resolve_display_name(
                    title=stats.title if stats else None,
                    end_time=game.end_time if game else None,
                    ply=puzzle.ply,
                    opening_name=diagnosis.opening_name if diagnosis else None,
                    resolved=row_resolved,
                ),
                # §5: the Library's motif filter is themed intent, so a
                # filtered browse shows the motif it was filtered by, and
                # nothing else.
                primary_motif=(
                    usable_motif(stats.primary_motif)
                    if (
                        stats
                        and motif_is_visible(
                            resolved=row_resolved,
                            puzzle_motif=stats.primary_motif,
                            requested_motif=motif,
                        )
                    )
                    else None
                ),
                difficulty=_swing_to_difficulty(puzzle.swing),
                swing=puzzle.swing,
                fen=puzzle.fen,
                side_to_move=puzzle.side_to_move,
                # Gated on ?reveal=true (dim 13) only when the strip flag is ON.
                # When OFF (default) the solution is always included so the old
                # client-grading frontend keeps working.
                best_move_uci=puzzle.best_move_uci if reveal_solution else None,
                accept_moves_uci=_accept_moves(puzzle) if reveal_solution else [],
                status=row_status,
                attempts=stats.attempts if stats else 0,
                pass_count=stats.pass_count if stats else 0,
                fail_count=stats.fail_count if stats else 0,
                last_reviewed_at=stats.last_reviewed_at if stats else None,
                last_result=stats.last_result if stats else None,
                next_due_at=stats.next_due_at if stats else None,
                created_at=puzzle.created_at,
                # The summary carries `primary_cause` and its label -- the
                # diagnosed cause, in the browse payload. §4 lists diagnosis
                # prose as a revealing field and this is the short form of it,
                # so it follows the same gate. Missed on the first pass through
                # step 3: the leak was found by printing an actual response
                # rather than by reading the list of fields.
                diagnosis_summary=(
                    _puzzle_diagnosis_summary(diagnosis) if row_resolved else None
                ),
            )
        )

    return PuzzleListResponse(
        puzzles=result_puzzles,
        total=total,
        limit=limit,
        offset=offset,
        available_motifs=available_motifs,
        available_causes=available_causes,
        available_openings=available_openings,
        stats=PuzzleCorpusStats(
            total=corpus_total,
            due=cr.cnt_due or 0,
            new=cr.cnt_new or 0,
            learning=cr.cnt_learning or 0,
            mastered=cr.cnt_mastered or 0,
        ),
    )


@router.get("/puzzles/{puzzle_id}", response_model=PuzzleListItem)
def get_puzzle_detail(
    puzzle_id: str,
    username: Annotated[Username, Query(description="Username to look up puzzle for")],
    reveal: bool = Query(
        False,
        description="Include the solution (best_move_uci/accept_moves_uci). "
        "Off by default so the browse surface can't echo the answer.",
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Get a single puzzle by ID with user stats."""
    from services.api.models import Puzzle as PuzzleModel

    assert_owns_username(account, username, db)

    # When the strip flag is OFF (default) the solution is always included so the
    # old client-grading frontend keeps working; ?reveal only matters when the
    # strict gate is ON.
    reveal_solution = reveal or not _strip_puzzle_solutions_enabled()
    # ``username`` is already canonical (folded at the request boundary).
    username_lower = username
    # naive-UTC bound for SQL comparison against naive next_due_at (see
    # spaced_repetition module note).
    now = _utcnow_naive()

    detail_status_case = case(
        (
            or_(PuzzleStats.puzzle_id.is_(None), PuzzleStats.attempts == 0),
            literal("new"),
        ),
        (
            and_(PuzzleStats.next_due_at.isnot(None), PuzzleStats.next_due_at <= now),
            literal("due"),
        ),
        (
            and_(
                PuzzleStats.attempts >= 3,
                (PuzzleStats.pass_count * 1.0 / PuzzleStats.attempts) >= 0.8,
            ),
            literal("mastered"),
        ),
        else_=literal("learning"),
    )

    stmt = (
        select(PuzzleModel, PuzzleStats, detail_status_case.label("computed_status"))
        .outerjoin(
            PuzzleStats,
            (PuzzleModel.id == PuzzleStats.puzzle_id)
            & (PuzzleStats.username == username_lower),
        )
        .where(PuzzleModel.id == puzzle_id, PuzzleModel.username == username_lower)
    )
    row = db.execute(stmt).first()
    if not row:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    puzzle, stats, computed_status = row
    # One row, so the game is a primary-key lookup rather than a join rewrite.
    # Provenance needs its end_time; a missing game degrades the label to the
    # move number instead of failing the request.
    game = (
        db.get(GameModel, (puzzle.source_game_id, puzzle.username))
        if puzzle.source_game_id
        else None
    )
    resolved = is_resolved(stats)
    return PuzzleListItem(
        id=puzzle.id,
        title=(stats.title if (stats and resolved) else None),
        display_name=resolve_display_name(
            title=stats.title if stats else None,
            end_time=game.end_time if game else None,
            ply=puzzle.ply,
            resolved=resolved,
        ),
        primary_motif=(usable_motif(stats.primary_motif) if (stats and resolved) else None),
        difficulty=_swing_to_difficulty(puzzle.swing),
        swing=puzzle.swing,
        fen=puzzle.fen,
        side_to_move=puzzle.side_to_move,
        # Gated on ?reveal=true (dim 13) only when the strip flag is ON. When OFF
        # (default) the solution is always included so the old client-grading
        # frontend keeps working.
        best_move_uci=puzzle.best_move_uci if reveal_solution else None,
        accept_moves_uci=_accept_moves(puzzle) if reveal_solution else [],
        status=computed_status,
        attempts=stats.attempts if stats else 0,
        pass_count=stats.pass_count if stats else 0,
        fail_count=stats.fail_count if stats else 0,
        last_reviewed_at=stats.last_reviewed_at if stats else None,
        last_result=stats.last_result if stats else None,
        next_due_at=stats.next_due_at if stats else None,
        created_at=puzzle.created_at,
        # diagnosis_summary is intentionally omitted here: the detail page uses
        # GET /puzzles/{id}/diagnosis for full diagnosis data, not this field.
    )


class DiagnosisEvidenceItem(BaseModel):
    id: str
    label: str
    value: str


class DiagnosisResponse(BaseModel):
    """A diagnosis, or an honest statement that there isn't one yet.

    ``state`` is what the UI renders on, and every value is a real situation
    rather than an error:

    * ``ready``       — a cause, with the evidence behind it
    * ``unclear``     — analysed, but no rule found a supported cause
    * ``pending``     — not analysed yet; a job will get to it
    * ``unavailable`` — this puzzle cannot be analysed at all
    * ``withheld``    — there may well be one, but this puzzle has not been
      attempted in its current exposure, and the prose names the cause and the
      solution (§4). A distinct state rather than reusing ``pending``, which
      would claim the analysis has not happened; the honest statement is that
      it is not being shown yet.

    No cause is ever invented to avoid ``unclear``. There is deliberately no
    numeric confidence: the rule strength is an ordering prior, not a
    calibrated probability, and rendering it as a percentage would overstate
    what the rules know.
    """

    state: Literal["ready", "unclear", "pending", "unavailable", "withheld"]
    puzzle_id: str
    primary_motif: str | None = None
    primary_cause: str | None = None
    primary_cause_label: str | None = None
    secondary_causes: list[str] = []
    secondary_cause_labels: list[str] = []
    phase: str | None = None
    evidence: list[DiagnosisEvidenceItem] = []
    # True when a diagnosis has evidence but the caller did not reveal. Lets the
    # UI say "solve it to see why" instead of rendering an empty section as if
    # there were nothing to show.
    evidence_withheld: bool = False
    explanation: str | None = None
    training_recommendation: str | None = None
    user_confirmed_cause: str | None = None
    # A safe, server-owned taxonomy for post-resolution feedback. It is set
    # only on ready responses, so clients cannot infer anything from an
    # unresolved/withheld response and never need to copy the taxonomy.
    cause_options: list[CauseOption] | None = None
    source: str | None = None
    diagnosed_at: datetime | None = None

    @model_serializer(mode="wrap")
    def _omit_unavailable_cause_options(self, handler):
        """Keep the options absent, not merely empty, until a ready diagnosis."""
        data = handler(self)
        if data["cause_options"] is None:
            del data["cause_options"]
        return data


class DiagnosisConfirmRequest(BaseModel):
    cause: str


def _unresolved_diagnosis(puzzle_id: str) -> DiagnosisResponse:
    """The gate's answer for a puzzle the user has not attempted yet.

    Every revealing field is simply absent rather than emptied-but-present, so
    there is nothing to infer from the shape of the response: an unresolved
    puzzle with a diagnosis and one without are byte-identical here.
    """
    return DiagnosisResponse(state="withheld", puzzle_id=puzzle_id)


def _diagnosis_response(
    puzzle_id: str, row, reveal_solution: bool = False
) -> DiagnosisResponse:
    """Build the client payload.

    ``reveal_solution`` defaults to False deliberately: the evidence names the
    solution move, so a call site that forgets to pass the gate withholds
    rather than leaks. The first version defaulted to True and POST /confirm —
    which returns this same body — silently bypassed the gate that GET
    enforced.
    """
    from services.api.models import DiagnosisStatus

    if row is None:
        return DiagnosisResponse(state="pending", puzzle_id=puzzle_id)
    if row.status == DiagnosisStatus.UNAVAILABLE:
        # The stored ``error`` is a developer detail (an illegal move, an
        # unparseable FEN) and is deliberately not echoed to the client.
        return DiagnosisResponse(state="unavailable", puzzle_id=puzzle_id)

    cause = row.user_confirmed_cause or row.primary_cause
    unclear = row.insufficient_evidence or not cause
    secondary = list(row.secondary_causes or [])
    # The evidence names the solution — "Best move: Qxd5", the squares it
    # attacks, the length of the winning line. That makes this endpoint a
    # side-channel around the anti-cheat gate, so it obeys the same rule as
    # /puzzles/{id}: withheld unless the caller reveals. The cause and its label
    # stay, because "loose piece awareness" is a coaching label, not the move.
    evidence = row.evidence_json or [] if reveal_solution else []
    state: Literal["ready", "unclear"] = "unclear" if unclear else "ready"
    return DiagnosisResponse(
        state=state,
        puzzle_id=puzzle_id,
        primary_motif=usable_motif(row.primary_motif),
        primary_cause=cause,
        primary_cause_label=CAUSE_LABELS.get(cause) if cause else None,
        secondary_causes=secondary,
        secondary_cause_labels=[CAUSE_LABELS.get(c, c) for c in secondary],
        phase=row.phase,
        evidence=[DiagnosisEvidenceItem(**item) for item in evidence],
        evidence_withheld=not reveal_solution and bool(row.evidence_json),
        explanation=row.explanation,
        training_recommendation=row.training_recommendation,
        user_confirmed_cause=row.user_confirmed_cause,
        cause_options=(
            [
                CauseOption(value=cause, label=label)
                for cause, label in CAUSE_LABELS.items()
            ]
            if state == "ready"
            else None
        ),
        source=row.source,
        diagnosed_at=row.updated_at,
    )


@router.get("/puzzles/{puzzle_id}/diagnosis", response_model=DiagnosisResponse)
def get_puzzle_diagnosis(
    puzzle_id: str,
    username: Annotated[Username, Query(description="Username the puzzle belongs to")],
    reveal: bool = Query(
        False,
        description="Include the evidence, which names the solution move. Off by "
        "default so the diagnosis cannot be used to read the answer.",
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Read the stored diagnosis for a puzzle.

    Never computes one. Diagnosis is background work, so a page load stays a
    single indexed row read whatever else is going on — and stays that way when
    the AI stage arrives and computing means a model call.
    """
    from services.api.models import Puzzle as PuzzleModel

    assert_owns_username(account, username, db)

    exists = db.scalar(
        select(PuzzleModel.id).where(
            PuzzleModel.id == puzzle_id, PuzzleModel.username == username
        )
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    # Same gate as /puzzles/{id}: when the strip flag is OFF (default) the
    # evidence is always included, so this deploys without breaking anything;
    # when it is ON, ?reveal=true is required.
    reveal_solution = reveal or not _strip_puzzle_solutions_enabled()

    # The resolution gate is a SECOND, independent question, and this endpoint
    # is the only one that has to load stats to answer it -- the existence
    # check above reads Puzzle, not PuzzleStats. One extra indexed read on a
    # single-row endpoint. Diagnosis prose names the cause and the solution in
    # sentences, so it is the most revealing payload in the app.
    # NOT db.get: PuzzleStats is keyed on puzzle_id ALONE, so a get() by id
    # would happily return another user's row. Both halves of the ownership
    # key go in the predicate -- the same rule the joins elsewhere follow
    # (#360).
    stats = db.scalars(
        select(PuzzleStats).where(
            PuzzleStats.puzzle_id == puzzle_id,
            PuzzleStats.username == username,
        )
    ).first()
    if not is_resolved(stats):
        return _unresolved_diagnosis(puzzle_id)

    return _diagnosis_response(
        puzzle_id, DiagnosisRepository(db).get(username, puzzle_id), reveal_solution
    )


class MotifHintRequest(BaseModel):
    username: Username
    # Optional: a hint asked outside a session still returns the motif, it just
    # has no counter to record against. The Library's own board is exactly that
    # case, and refusing there would make the gate inescapable on that surface.
    session_id: str | None = None


class MotifHintResponse(BaseModel):
    """Rung 0 of the hint ladder."""

    puzzle_id: str
    # None when the puzzle genuinely has no usable motif -- `blunder` means "no
    # motif was identified", so returning it would spend a hint on nothing.
    primary_motif: str | None
    hints_used: int | None = None


@router.post("/puzzles/{puzzle_id}/hint/motif", response_model=MotifHintResponse)
def use_motif_hint(
    puzzle_id: str,
    request: MotifHintRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Ask for the motif. This is the gate's exit, and it is meant to be.

    §4 withholds the motif before an attempt, which without an exit turns a
    hint ladder into a wall: the existing rungs are "name the piece to move",
    "highlight the destination", "reveal the solution", and the motif reveals
    strictly less than the first of those. So it sorts *before* them as a new
    rung 0.

    Deliberately a POST on a puzzle-scoped path rather than a widening of
    ``POST /sessions/{id}/hint``, which takes a session and a username and has
    no ``puzzle_id`` at all -- it cannot say which puzzle was hinted, and once
    §4 strips the motif from the payload the client has nowhere else to get it.

    The ask is recorded, which is the point of routing it through an endpoint
    instead of relaxing the serializer: a motif that arrives this way is a hint
    the user spent, and §9 keeps the distinction so the scheduler can use it
    later. Reuses the existing counter rather than adding a second one.
    """
    puzzle = db.scalars(
        select(PuzzleModel).where(
            PuzzleModel.id == puzzle_id,
            PuzzleModel.username == request.username,
        )
    ).first()
    if not puzzle:
        raise HTTPException(status_code=404, detail="Puzzle not found")
    assert_owns_username(account, request.username, db)

    stats = db.scalars(
        select(PuzzleStats).where(
            PuzzleStats.puzzle_id == puzzle_id,
            PuzzleStats.username == request.username,
        )
    ).first()
    # `usable_motif` drops "blunder", which means no motif was identified.
    # Spending a hint to be told nothing was identified is worse than being
    # told there is nothing to tell.
    motif = usable_motif(stats.primary_motif) if stats else None

    hints_used = None
    if request.session_id:
        session = db.scalars(
            select(TrainingSession).where(TrainingSession.id == request.session_id)
        ).first()
        # A bad or foreign session id must not cost the user their hint: the
        # motif is returned either way, and only the recording is skipped.
        if (
            session
            and session.username == request.username
            and session.completed_at is None
        ):
            session.hints_used += 1
            db.commit()
            hints_used = session.hints_used

    return MotifHintResponse(
        puzzle_id=puzzle_id, primary_motif=motif, hints_used=hints_used
    )


class SimilarPuzzleItem(BaseModel):
    """A sibling puzzle in the same weakness cluster.

    Carries no solution fields at all — not even behind ``?reveal``. This is a
    discovery surface reached *from* a puzzle the user is studying, so shipping
    answers here would put four more solutions on screen for every one they
    asked to see. Following a link lands on the detail page, which owns the
    reveal decision.
    """

    id: str
    title: str | None = None
    # See PuzzleListItem.display_name: equal to `title` today, and the single
    # field a client should render once titles become NULL by default.
    display_name: str | None = None
    primary_motif: str | None = None
    difficulty: str
    swing: float
    fen: str
    side_to_move: str
    created_at: datetime | None = None
    attempts: int = 0
    fail_count: int = 0


class SimilarPuzzlesResponse(BaseModel):
    cause: str | None = None
    cause_label: str | None = None
    match: str | None = None
    reason: str | None = None
    puzzles: list[SimilarPuzzleItem] = []


@router.get("/puzzles/{puzzle_id}/similar", response_model=SimilarPuzzlesResponse)
def get_similar_puzzles(
    puzzle_id: str,
    username: Annotated[Username, Query(description="Owner of the puzzle")],
    n: int = Query(5, ge=1, le=20, description="How many siblings to return"),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Other puzzles the user got wrong for the same reason.

    Answers "what else does this weakness cost me", which the training queue
    does not: the queue orders what is *due*, and a weakness is worth seeing
    whole regardless of when its puzzles next come round.

    An empty list is a normal answer, not an error — an undiagnosed puzzle, an
    unclassified cause, or a weakness with exactly one example all legitimately
    have no siblings, and the caller renders nothing rather than an error card.
    """
    from services.api.models import Puzzle as PuzzleModel

    assert_owns_username(account, username, db)
    username_lower = username

    # Ownership is a WHERE, not a get(): the puzzles PK is `id` alone, so
    # filtering on username here is what stops one account probing another's
    # corpus for which weaknesses it contains.
    owns = db.execute(
        select(PuzzleModel.id).where(
            PuzzleModel.id == puzzle_id,
            PuzzleModel.username == username_lower,
        )
    ).first()
    if owns is None:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    # The ANCHOR's own resolution decides whether this response may name its
    # diagnosed cause. Without this the route is a one-hop bypass of the
    # diagnosis gate: GET /puzzles/{id}/diagnosis answers "withheld" while
    # GET /puzzles/{id}/similar hands over the same cause for the same puzzle.
    anchor_stats = db.scalars(
        select(PuzzleStats).where(
            PuzzleStats.puzzle_id == puzzle_id,
            PuzzleStats.username == username_lower,
        )
    ).first()
    anchor_resolved = is_resolved(anchor_stats)

    repo = DiagnosisRepository(db)
    key = repo.cluster_key_for(username_lower, puzzle_id)
    if key is None:
        return SimilarPuzzlesResponse()

    cluster = key  # narrowed for the closure below

    def _cause_fields() -> dict:
        if not anchor_resolved:
            return {}
        return {
            "cause": cluster.cause,
            "cause_label": humanise_cause(cluster.cause),
        }

    sibling_ids, tier = repo.similar_puzzle_ids(username_lower, puzzle_id, key, n)
    if not sibling_ids or tier is None:
        return SimilarPuzzlesResponse(**_cause_fields())

    # Stats ride in on the join rather than a second round trip. PuzzleStats is
    # keyed on puzzle_id alone, so the username predicate belongs in the ON
    # clause — the same shape GET /puzzles/{id} uses.
    rows = db.execute(
        select(PuzzleModel, PuzzleStats, GameModel)
        .outerjoin(
            PuzzleStats,
            (PuzzleModel.id == PuzzleStats.puzzle_id)
            & (PuzzleStats.username == username_lower),
        )
        # Same reason as the list route: provenance needs end_time, and the
        # sibling set is N rows, so this must be a join rather than a lookup
        # per row.
        .outerjoin(
            GameModel,
            (GameModel.game_id == PuzzleModel.source_game_id)
            & (GameModel.username == PuzzleModel.username),
        )
        .where(
            PuzzleModel.username == username_lower,
            PuzzleModel.id.in_(sibling_ids),
        )
    ).all()

    # Preserve the repository's recency order; the IN query above does not
    # promise one, and re-sorting here would quietly discard the ranking. A
    # diagnosis whose puzzle row is missing is skipped, so the list can be
    # shorter than n.
    by_id = {puzzle.id: (puzzle, stats, game) for puzzle, stats, game in rows}
    items = []
    for pid in sibling_ids:
        found = by_id.get(pid)
        if found is None:
            continue
        row, stats, game = found
        # Siblings are selected by shared diagnosis, NOT by attempt state, so
        # this set is mostly puzzles the user has never touched -- and it is
        # reached from a puzzle they just solved, with links straight into
        # each one. §4 names this route as a leak site and the first pass
        # closed the other four and missed it.
        sibling_resolved = is_resolved(stats)
        items.append(
            SimilarPuzzleItem(
                id=row.id,
                title=stats.title if (stats and sibling_resolved) else None,
                display_name=resolve_display_name(
                    title=stats.title if stats else None,
                    end_time=game.end_time if game else None,
                    ply=row.ply,
                    resolved=sibling_resolved,
                ),
                # "blunder" means no motif was identified; tagging a row with it
                # says nothing and contradicts the reason line, which omits it.
                primary_motif=(
                    usable_motif(stats.primary_motif)
                    if (stats and sibling_resolved)
                    else None
                ),
                difficulty=_swing_to_difficulty(row.swing),
                swing=row.swing,
                fen=row.fen,
                side_to_move=row.side_to_move,
                created_at=row.created_at,
                attempts=stats.attempts if stats else 0,
                fail_count=stats.fail_count if stats else 0,
            )
        )

    return SimilarPuzzlesResponse(
        **_cause_fields(),
        match=tier.value,
        # `describe` names the cause in prose, so it follows the cause.
        reason=describe(key, tier) if anchor_resolved else None,
        puzzles=items,
    )


@router.post("/puzzles/{puzzle_id}/diagnosis/confirm", response_model=DiagnosisResponse)
def confirm_puzzle_diagnosis(
    puzzle_id: str,
    payload: DiagnosisConfirmRequest,
    username: Annotated[Username, Query(description="Username the puzzle belongs to")],
    reveal: bool = Query(
        False,
        description="Include the evidence, which names the solution move.",
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Let the user correct the cause label.

    Stored beside the computed cause, never over it: keeping both is what makes
    rule accuracy measurable against real feedback, and a later re-run of the
    rules must not silently discard the correction.
    """

    assert_owns_username(account, username, db)

    if payload.cause not in CAUSE_LABELS:
        raise HTTPException(status_code=422, detail="Unknown cause")

    repo = DiagnosisRepository(db)
    row = repo.confirm_cause(username, puzzle_id, payload.cause)
    if row is None:
        raise HTTPException(status_code=404, detail="No diagnosis for this puzzle")
    db.commit()

    # BOTH gates as the read, not just the reveal one. This returns the
    # identical body, so the resolution gate has to be mirrored here or the
    # endpoint is a one-POST bypass: the GET answers "withheld" while this
    # hands over the prose and the evidence naming the solution move.
    #
    # _diagnosis_response's docstring already records this endpoint bypassing
    # the GET's reveal gate once before. The confirmation itself is still
    # recorded above -- only the body is withheld.
    stats = db.scalars(
        select(PuzzleStats).where(
            PuzzleStats.puzzle_id == puzzle_id,
            PuzzleStats.username == username,
        )
    ).first()
    if not is_resolved(stats):
        return _unresolved_diagnosis(puzzle_id)

    return _diagnosis_response(
        puzzle_id, row, reveal or not _strip_puzzle_solutions_enabled()
    )


def _find_existing_review(db, puzzle_id, username, session_id, client_review_id):
    """Return the prior review for this idempotency key, or None.

    Matches the uniqueness tuple (puzzle_id, username, session_id,
    client_review_id); a NULL session_id is compared with IS NULL, mirroring the
    COALESCE(session_id, '') unique index.
    """
    return db.scalars(
        select(PuzzleReview).where(
            PuzzleReview.puzzle_id == puzzle_id,
            PuzzleReview.username == username,
            PuzzleReview.session_id == session_id,
            PuzzleReview.client_review_id == client_review_id,
        )
    ).first()


def _build_review_response(
    stats,
    puzzle_stats,
    result,
    verified: bool = False,
    source: str | None = None,
    review_context: str = "standard",
    affects_scheduling: bool = True,
) -> dict:
    """Build the review endpoint payload for a given (stats, result).

    Shared by the normal path and the idempotent-replay path so a replayed
    review returns the same shape without re-running any scheduling logic.

    ``result`` here is the authoritative (server-decided) outcome. ``verified``
    and ``source`` tell the client whether that outcome was independently
    checked by the server or merely echoes the client's self-report — so the UI
    and analytics never present a self-reported pass as verified skill.
    """
    result_val = result.value if isinstance(result, PuzzleResult) else result
    feedback_message = ""
    if not affects_scheduling:
        feedback_message = "Practice recorded. Your normal review date is unchanged."
    elif result_val == "pass":
        if stats.attempts == 1:
            feedback_message = "Perfect! First try!"
        elif stats.attempts > 0 and stats.pass_count / stats.attempts > 0.8:
            feedback_message = "Great job! You're mastering this pattern."
        else:
            feedback_message = "Good solve!"
    else:
        if stats.attempts > 0 and stats.fail_count / stats.attempts > 0.5:
            feedback_message = "Keep practicing this pattern."
        else:
            feedback_message = "Almost! Review the solution carefully."

    return {
        "next_due_at": stats.next_due_at,
        "interval_days": stats.interval_days,
        "ease_factor": stats.ease_factor,
        "feedback": feedback_message,
        "puzzle_info": puzzle_stats,
        # Server-decided outcome and whether it was independently verified.
        "result": result_val,
        "verified": verified,
        "source": source,
        "review_context": review_context,
        "affects_scheduling": affects_scheduling,
        "stats": {
            "attempts": stats.attempts,
            "pass_count": stats.pass_count,
            "fail_count": stats.fail_count,
            "last_reviewed_at": stats.last_reviewed_at,
            "last_result": stats.last_result,
        },
    }


@router.post("/puzzles/{puzzle_id}/review")
def review_puzzle(
    puzzle_id: str,
    request: ReviewRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Record a puzzle review and update scheduling.

    Optionally tracks the review in a training session.
    Provides enhanced feedback including puzzle statistics.

    Idempotent replay: when ``client_review_id`` is supplied and a review with
    that key already exists for this (puzzle, user, session), the prior outcome
    is returned WITHOUT re-recording the review, re-incrementing session
    counters, or advancing scheduling. This makes double-clicks and network
    retries safe.
    """
    assert_owns_username(account, request.username, db)
    puzzle_repository = PuzzleRepository(db)
    puzzle = puzzle_repository.get_puzzle(request.username, puzzle_id)
    if not puzzle:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    # Normalize an empty session_id to NULL (dim 14). The unique index keys on
    # COALESCE(session_id, ''), so "" and None collapse to the same value; the
    # idempotency lookup, the write, and the index must all agree on one
    # representation. Otherwise a first submit with session_id="" then a NULL
    # retry (same client_review_id) misses both the fast-path replay and the
    # IntegrityError-replay lookup and 500s. All uses below go through this local.
    session_id = request.session_id or None

    # Idempotent replay: short-circuit before any mutation if this exact
    # client_review_id was already recorded for this (puzzle, user, session).
    if request.client_review_id:
        existing = _find_existing_review(
            db,
            puzzle_id,
            request.username,
            session_id,
            request.client_review_id,
        )
        if existing:
            stats = get_puzzle_stats(db, puzzle_id, request.username)
            puzzle_stats = puzzle_repository.get_puzzle_stats(
                request.username, puzzle_id
            )
            return _build_review_response(
                stats,
                puzzle_stats,
                existing.result,
                verified=existing.verified,
                source=existing.source,
                review_context=existing.review_context,
                affects_scheduling=existing.affects_scheduling,
            )

    # Server-verified training integrity (audit gate 7): when the client sends
    # the move it played, the SERVER decides pass/fail from the board — the
    # client's self-reported ``result`` is recorded (client_result) but never
    # trusted for the outcome. Absent a move, fall back to the client's claim
    # and mark it unverified so analytics can tell skill from self-report.
    client_result = request.result
    if request.attempted_move is not None:
        # For a full-PV puzzle the client sends the WHOLE solved line as a
        # space-separated UCI string here; the server re-verifies every ply so a
        # puzzle counts as solved only when the entire line was played correctly.
        # A single move (legacy puzzle, or a puzzle with no stored line) verifies
        # against the accepted-solution set exactly as before.
        if len(_solution_pv(puzzle)) >= 2:
            effective_result = _verify_line(puzzle, request.attempted_move.split())
        else:
            effective_result = _verify_attempt(puzzle, request.attempted_move)
        verified = True
        review_source = "server_verified"
    else:
        effective_result = request.result
        verified = False
        review_source = "client_reported"

    review_context = "standard"
    affects_scheduling = True
    # If session_id provided, validate session and update counters
    if session_id:
        from services.api.models import PuzzleResult as PR
        from services.api.models import TrainingSession

        # Lock the session row for the duration of this transaction so the
        # counter read-modify-write below is race-safe: concurrent reviews in the
        # SAME session serialize on this lock instead of both reading the stale
        # count and losing an increment (Postgres READ COMMITTED). SQLite has no
        # row lock but serializes writers, so the plain SELECT is safe there.
        # Locking the session BEFORE the puzzle-stats row (below) gives a single
        # consistent lock order, so concurrent same-session reviews can't deadlock.
        stmt = select(TrainingSession).where(TrainingSession.id == session_id)
        if db.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        session = db.scalars(stmt).first()

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.username != request.username:
            raise HTTPException(
                status_code=403, detail="Session belongs to different user"
            )

        if session.completed_at is not None:
            raise HTTPException(status_code=400, detail="Session already completed")

        if session.session_type == "focus_practice":
            selected = (session.session_data or {}).get("selected_items", [])
            item = next(
                (
                    candidate
                    for candidate in selected
                    if candidate.get("puzzle_id") == puzzle_id
                ),
                None,
            )
            if item is None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "session_item_mismatch"},
                )
            policy = item.get("review_policy")
            if policy not in {"normal_review", "practice_only"}:
                raise HTTPException(
                    status_code=409, detail={"code": "session_item_mismatch"}
                )
            review_context = "focus_practice"
            affects_scheduling = policy == "normal_review"

        # Increment session counters (will be committed with review). Use the
        # SERVER-decided outcome so a spoofed "pass" with a wrong move can't
        # inflate session pass_count / streak.
        if effective_result == PR.PASS:
            session.pass_count += 1
            # Update streak
            session.current_streak += 1
            if session.current_streak > session.best_streak:
                session.best_streak = session.current_streak
        else:
            session.fail_count += 1
            # Reset streak on fail
            session.current_streak = 0

        # Add time if provided
        if request.time_spent_ms:
            session.total_time_ms += request.time_spent_ms

    try:
        # 1. Record individual review (with optional session_id + idempotency
        #    key). ``result`` is the authoritative outcome; the client's raw
        #    claim and how it was decided are recorded alongside it.
        insert_puzzle_review(
            db,
            puzzle_id,
            request.username,
            effective_result,
            request.time_spent_ms,
            session_id=session_id,
            client_review_id=request.client_review_id,
            attempted_move=request.attempted_move,
            client_result=client_result,
            verified=verified,
            source=review_source,
            review_context=review_context,
            affects_scheduling=affects_scheduling,
        )

        # 2. Update aggregate stats (triggers scheduling logic)
        if affects_scheduling:
            stats = update_puzzle_stats(
                db, puzzle_id, request.username, effective_result
            )
        else:
            stats = get_puzzle_stats(db, puzzle_id, request.username)
            if stats is None:
                raise HTTPException(
                    status_code=409, detail={"code": "session_item_mismatch"}
                )

        # 3. Get puzzle details for feedback
        puzzle_stats = puzzle_repository.get_puzzle_stats(request.username, puzzle_id)

        # 4. Commit all changes atomically (single transaction boundary;
        #    the storage helpers above only flush, they never commit)
        db.commit()
    except IntegrityError:
        # A concurrent same-key submit slipped past the replay SELECT above and
        # committed first; the unique index rejects this duplicate. Roll back our
        # (uncommitted) mutations — including the session counter increments —
        # and replay the winner's outcome instead of surfacing a 500 or double
        # counting. This is the concurrency backstop for the replay-before-mutate
        # fast path.
        db.rollback()
        if request.client_review_id:
            existing = _find_existing_review(
                db,
                puzzle_id,
                request.username,
                session_id,
                request.client_review_id,
            )
            if existing:
                stats = get_puzzle_stats(db, puzzle_id, request.username)
                puzzle_stats = puzzle_repository.get_puzzle_stats(
                    request.username, puzzle_id
                )
                return _build_review_response(
                    stats,
                    puzzle_stats,
                    existing.result,
                    verified=existing.verified,
                    source=existing.source,
                    review_context=existing.review_context,
                    affects_scheduling=existing.affects_scheduling,
                )
        raise

    # 5. Build the response (feedback + scheduling + stats). Feedback reflects
    #    the server-decided outcome, not the client's self-reported claim.
    return _build_review_response(
        stats,
        puzzle_stats,
        effective_result,
        verified=verified,
        source=review_source,
        review_context=review_context,
        affects_scheduling=affects_scheduling,
    )


@router.post("/puzzles/{puzzle_id}/check", response_model=CheckResponse)
def check_puzzle(
    puzzle_id: str,
    request: CheckRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Server-authoritative live feedback for the training board (audit gate 13).

    Verifies the played move against the puzzle's accepted-solution set (or, for
    a full-PV puzzle, the move expected at ``ply_index`` of the line) and returns
    only correct/incorrect plus — on a correct move of a multi-move line — the
    opponent's forced reply and whether the line is complete. It never returns the
    solver's upcoming answer, so the client can train the whole line WITHOUT ever
    holding the part it has yet to find. Records nothing; scheduling/stats still
    flow through POST /puzzles/{id}/review. Ownership is enforced exactly as
    review.
    """
    assert_owns_username(account, request.username, db)
    puzzle_repository = PuzzleRepository(db)
    puzzle = puzzle_repository.get_puzzle(request.username, puzzle_id)
    if not puzzle:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    return _check_solution_move(puzzle, request.attempted_move, request.ply_index)


@router.post("/puzzles/{puzzle_id}/reveal", response_model=RevealResponse)
def reveal_puzzle(
    puzzle_id: str,
    request: RevealRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Explicit "show me the solution" path for the training board.

    The scored training payload (list/due/daily) no longer carries the answer,
    so a user who gives up (or asks for a full clue) fetches it here on demand.
    Returns nothing but the solution; recording the resulting fail still happens
    via POST /puzzles/{id}/review when the user moves on. Ownership enforced.
    """
    assert_owns_username(account, request.username, db)
    puzzle_repository = PuzzleRepository(db)
    puzzle = puzzle_repository.get_puzzle(request.username, puzzle_id)
    if not puzzle:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    return RevealResponse(
        best_move_uci=puzzle.best_move_uci,
        accept_moves_uci=_accept_moves(puzzle),
        solution_pv=_solution_pv(puzzle),
    )
