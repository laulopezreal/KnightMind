"""
Training session endpoints.

Handles session lifecycle: start, complete, and recent sessions query.
"""

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from services.api.db import get_db
from services.api.diagnosis.causes import CAUSE_LABELS
from services.api.diagnosis.planner import plan_focus
from services.api.identity import assert_owns_username, require_account
from services.api.models import Account, PuzzleStats, TrainingSession
from services.api.puzzles.provenance import resolve_display_name
from services.api.puzzles.resolution import is_resolved
from services.api.ratings_auto import auto_snapshot
from services.api.storage import PuzzleRepository
from services.api.storage.diagnosis_repository import DiagnosisRepository
from services.api.storage.spaced_repetition import _source_games, _vary_session
from services.api.usernames import Username

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


# Request/Response Models
class StartSessionRequest(BaseModel):
    username: Username
    n: int
    # Focus practice has no generic start path: its membership and scheduling
    # policy must come from a server-owned snapshot.
    session_type: Literal["standard", "timed", "accuracy_goal"] = "standard"
    target_accuracy: float | None = None  # Target accuracy percentage (0.0-100.0)
    target_time_minutes: int | None = None  # Target session time in minutes
    session_data: dict | None = (
        None  # Flexible storage for session-specific data (e.g., warmup flag)
    )


class StartSessionResponse(BaseModel):
    session_id: str
    requested_n: int
    session_type: str | None = None
    target_accuracy: float | None = None
    target_time_minutes: int | None = None


class FocusPracticeStartRequest(BaseModel):
    username: Username
    focus_cause: str
    n: int = Field(default=5, ge=2, le=10)


class FocusPracticeStartResponse(BaseModel):
    session_id: str
    session_type: Literal["focus_practice"] = "focus_practice"
    focus: dict
    requested_n: int
    returned_count: int
    puzzles: list[dict]


class CompleteSessionRequest(BaseModel):
    username: Username


class SessionSummary(BaseModel):
    session_id: str
    requested_n: int
    pass_count: int
    fail_count: int
    total_time_ms: int
    created_at: datetime
    completed_at: datetime | None
    # Enhanced session fields
    session_type: str | None = None
    target_accuracy: float | None = None
    target_time_minutes: int | None = None
    current_streak: int = 0
    best_streak: int = 0
    hints_used: int = 0
    # Surfaced from session_data so a resumed session is ordered the same way
    # it was served. Reading the focus back off the URL instead made the order
    # depend on how the user navigated back — see the resume path in
    # usePuzzleSession.
    focus_cause: str | None = None
    focus_name: str | None = None
    # Focus-practice is the one session type whose exact selected queue and
    # scheduling policy are server-owned snapshot state. These fields are only
    # populated for that type on GET /sessions/{id}; normal summaries retain
    # their compact legacy contract.
    selected_items: list[dict] | None = None
    puzzles: list[dict] | None = None
    focus_opening: str | None = None
    focus_opening_scope: str | None = None
    # Same reasoning, and the stakes are higher: motif *filters* the queue
    # rather than merely biasing it, so losing it on resume changes the set of
    # puzzles, not just their order.
    motif: str | None = None


class UseHintRequest(BaseModel):
    username: Username


def focus_practice_candidate_count(db: Session, username: str, cause: str) -> int:
    """Count the safely servable candidates for Today's Focus availability."""
    diagnosis_repo = DiagnosisRepository(db)
    puzzle_repo = PuzzleRepository(db)
    return sum(
        puzzle_repo.get_puzzle(username, puzzle_id) is not None
        for puzzle_id in diagnosis_repo.puzzle_ids_for_cause(username, cause)
    )


def _focus_practice_payloads(
    db: Session, username: str, selected_items: list[dict]
) -> list[dict]:
    """Rehydrate an immutable Focus Practice snapshot as safe puzzle payloads.

    The snapshot owns identity, order, and review policy. Current diagnosis and
    schedule state are intentionally never consulted here, so resume cannot
    quietly select a different queue or change a future item's policy.
    """
    from services.api.puzzles_routes import _end_times_by_puzzle, _strip_solution

    puzzle_repo = PuzzleRepository(db)
    puzzles = []
    for item in selected_items:
        puzzle_id = item.get("puzzle_id")
        policy = item.get("review_policy")
        if not isinstance(puzzle_id, str) or policy not in {
            "normal_review",
            "practice_only",
        }:
            raise HTTPException(
                status_code=409, detail={"code": "session_item_mismatch"}
            )
        puzzle = puzzle_repo.get_puzzle(username, puzzle_id)
        if puzzle is None:
            raise HTTPException(
                status_code=409, detail={"code": "session_item_mismatch"}
            )
        puzzles.append((puzzle_id, policy, puzzle))

    all_stats = {
        row.puzzle_id: row
        for row in db.scalars(
            select(PuzzleStats).where(PuzzleStats.username == username)
        ).all()
    }
    end_times = _end_times_by_puzzle(db, username, [puzzle for _, _, puzzle in puzzles])
    payloads = []
    for puzzle_id, policy, puzzle in puzzles:
        stats = all_stats.get(puzzle_id)
        payload = asdict(puzzle)
        payload.update(
            {
                "next_due_at": stats.next_due_at if stats else None,
                "interval_days": stats.interval_days if stats else None,
                "ease_factor": stats.ease_factor if stats else 2.0,
                "display_name": resolve_display_name(
                    title=stats.title if stats else None,
                    end_time=end_times.get(puzzle_id),
                    ply=getattr(puzzle, "ply", None),
                    resolved=is_resolved(stats),
                ),
                "queue_reason": {
                    "reason": "practice",
                    "explanation": "Extra practice for your current focus.",
                },
                "review_policy": policy,
            }
        )
        payloads.append(_strip_solution(payload))
    return payloads


@router.post("/focus-practice/start", response_model=FocusPracticeStartResponse)
def start_focus_practice(
    request: FocusPracticeStartRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Create a focused session with immutable server-selected review policy."""
    assert_owns_username(account, request.username, db)
    if request.focus_cause not in CAUSE_LABELS:
        raise HTTPException(status_code=422, detail="Unknown cause")
    diagnosis_repo = DiagnosisRepository(db)
    planned = plan_focus(diagnosis_repo.cause_breakdown(request.username))
    if planned is None or planned.cause != request.focus_cause:
        raise HTTPException(
            status_code=409,
            detail={"code": "focus_practice_unavailable", "reason": "focus_changed"},
        )

    puzzle_repo = PuzzleRepository(db)
    all_stats = {
        row.puzzle_id: row
        for row in db.scalars(
            select(PuzzleStats).where(PuzzleStats.username == request.username)
        ).all()
    }
    captured_at = datetime.now(timezone.utc).replace(tzinfo=None)
    candidates = []
    for puzzle_id in diagnosis_repo.puzzle_ids_for_cause(
        request.username, request.focus_cause
    ):
        puzzle = puzzle_repo.get_puzzle(request.username, puzzle_id)
        if puzzle is None:
            continue
        stats = all_stats.get(puzzle_id)
        if stats is None or stats.next_due_at is None:
            tier, policy, sort_time = 1, "normal_review", captured_at
        elif stats.next_due_at <= captured_at:
            tier, policy, sort_time = 0, "normal_review", stats.next_due_at
        else:
            tier, policy, sort_time = 2, "practice_only", stats.next_due_at
        candidates.append((tier, sort_time, puzzle_id, policy, puzzle, stats))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    ids = [item[2] for item in candidates]
    source_games = _source_games(db, request.username, ids)
    varied = []
    for tier in (0, 1, 2):
        tier_ids = [item[2] for item in candidates if item[0] == tier]
        varied.extend(
            _vary_session(
                tier_ids,
                all_stats,
                source_games,
                request.n,
                cap_motifs=False,
            )
        )
    candidate_by_id = {item[2]: item for item in candidates}
    selected = [candidate_by_id[puzzle_id] for puzzle_id in varied[: request.n]]
    if len(selected) < 2:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "focus_practice_unavailable",
                "reason": "insufficient_safe_candidates",
            },
        )

    snapshot_items = []
    for position, (_, _, puzzle_id, policy, _, _) in enumerate(selected):
        snapshot_items.append(
            {"puzzle_id": puzzle_id, "position": position, "review_policy": policy}
        )
    payloads = _focus_practice_payloads(db, request.username, snapshot_items)
    session_id = str(uuid.uuid4())
    db.add(
        TrainingSession(
            id=session_id,
            username=request.username,
            requested_n=request.n,
            pass_count=0,
            fail_count=0,
            total_time_ms=0,
            session_type="focus_practice",
            current_streak=0,
            best_streak=0,
            hints_used=0,
            session_data={
                "schema_version": 1,
                "focus_cause": request.focus_cause,
                "focus_name": planned.name,
                "selected_items": snapshot_items,
            },
        )
    )
    db.commit()
    return FocusPracticeStartResponse(
        session_id=session_id,
        focus={"cause": request.focus_cause, "name": planned.name},
        requested_n=request.n,
        returned_count=len(payloads),
        puzzles=payloads,
    )


@router.post("/start", response_model=StartSessionResponse)
def start_session(
    request: StartSessionRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Start a new training session.

    Creates a session record and returns the session_id for tracking reviews.
    """
    assert_owns_username(account, request.username, db)
    session_id = str(uuid.uuid4())

    session = TrainingSession(
        id=session_id,
        username=request.username,
        requested_n=request.n,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
        session_type=request.session_type,
        target_accuracy=request.target_accuracy,
        target_time_minutes=request.target_time_minutes,
        current_streak=0,
        best_streak=0,
        hints_used=0,
        session_data=request.session_data or {},
    )

    db.add(session)
    db.commit()

    return StartSessionResponse(
        session_id=session_id,
        requested_n=request.n,
        session_type=request.session_type,
        target_accuracy=request.target_accuracy,
        target_time_minutes=request.target_time_minutes,
    )


@router.get("/recent", response_model=list[SessionSummary])
def get_recent_sessions(
    username: Username,
    limit: int = 10,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Get recent training sessions for a user.

    Returns sessions ordered by created_at descending.
    Limit: default 10, max 50.
    """
    assert_owns_username(account, username, db)
    # Enforce max limit
    limit = min(limit, 50)

    stmt = (
        select(TrainingSession)
        .where(TrainingSession.username == username)
        .order_by(desc(TrainingSession.created_at))
        .limit(limit)
    )

    sessions = db.scalars(stmt).all()

    return [
        SessionSummary(
            session_id=s.id,
            requested_n=s.requested_n,
            pass_count=s.pass_count,
            fail_count=s.fail_count,
            total_time_ms=s.total_time_ms,
            created_at=s.created_at,
            completed_at=s.completed_at,
            session_type=s.session_type,
            target_accuracy=s.target_accuracy,
            target_time_minutes=s.target_time_minutes,
            current_streak=s.current_streak,
            best_streak=s.best_streak,
            hints_used=s.hints_used,
        )
        for s in sessions
    ]


@router.get("/{session_id}", response_model=SessionSummary)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Get session details by ID.

    Used for validating sessions on page load.
    """
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    session = db.scalars(stmt).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Object-level ownership: 404 (not 403) so foreign session ids aren't confirmed.
    assert_owns_username(account, session.username, db, status_code=404)

    session_data = session.session_data or {}
    selected_items = None
    puzzles = None
    if session.session_type == "focus_practice":
        snapshot_items = session_data.get("selected_items")
        if not isinstance(snapshot_items, list):
            raise HTTPException(
                status_code=409, detail={"code": "session_item_mismatch"}
            )
        selected_items = snapshot_items
        puzzles = _focus_practice_payloads(db, session.username, selected_items)

    return SessionSummary(
        session_id=session.id,
        requested_n=session.requested_n,
        pass_count=session.pass_count,
        fail_count=session.fail_count,
        total_time_ms=session.total_time_ms,
        created_at=session.created_at,
        completed_at=session.completed_at,
        session_type=session.session_type,
        target_accuracy=session.target_accuracy,
        target_time_minutes=session.target_time_minutes,
        current_streak=session.current_streak,
        best_streak=session.best_streak,
        hints_used=session.hints_used,
        focus_cause=session_data.get("focus_cause"),
        focus_name=session_data.get("focus_name"),
        selected_items=selected_items,
        puzzles=puzzles,
        focus_opening=session_data.get("focus_opening"),
        focus_opening_scope=session_data.get("focus_opening_scope"),
        motif=session_data.get("motif"),
    )


@router.post("/{session_id}/complete", response_model=SessionSummary)
async def complete_session(
    session_id: str,
    request: CompleteSessionRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Mark a session as complete.

    Idempotent - returns existing summary if already completed.
    Auto-records rating snapshots linked to this session (best-effort).
    """
    # Fetch session
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    session = db.scalars(stmt).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Real object-ownership against the authenticated account (404 hides
    # foreign session ids). The self-referential username check below is only
    # meaningful once request.username is trusted, which it now is under auth.
    assert_owns_username(account, session.username, db, status_code=404)

    if session.username != request.username:
        raise HTTPException(status_code=403, detail="Session belongs to different user")

    # If not already completed, set completed_at and auto-snapshot
    should_auto_snapshot = False
    if session.completed_at is None:
        session.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(session)
        should_auto_snapshot = True

    # Build response before auto-snapshot so a rollback inside
    # auto_snapshot cannot expire the ORM-managed session object.
    result = SessionSummary(
        session_id=session.id,
        requested_n=session.requested_n,
        pass_count=session.pass_count,
        fail_count=session.fail_count,
        total_time_ms=session.total_time_ms,
        created_at=session.created_at,
        completed_at=session.completed_at,
        session_type=session.session_type,
        target_accuracy=session.target_accuracy,
        target_time_minutes=session.target_time_minutes,
        current_streak=session.current_streak,
        best_streak=session.best_streak,
        hints_used=session.hints_used,
        focus_cause=(session.session_data or {}).get("focus_cause"),
        focus_opening=(session.session_data or {}).get("focus_opening"),
        focus_opening_scope=(session.session_data or {}).get("focus_opening_scope"),
        motif=(session.session_data or {}).get("motif"),
    )

    # Best-effort auto-snapshot after response is built
    if should_auto_snapshot:
        await auto_snapshot(request.username, db, session_id=session_id)

    return result


@router.post("/{session_id}/use_hint", response_model=SessionSummary)
def use_hint(
    session_id: str,
    request: UseHintRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Use a hint during a training session.

    Updates the session's hint counter.
    """
    # Fetch session
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    session = db.scalars(stmt).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Object-ownership against the account (404 hides foreign session ids).
    assert_owns_username(account, session.username, db, status_code=404)

    if session.username != request.username:
        raise HTTPException(status_code=403, detail="Session belongs to different user")

    if session.completed_at is not None:
        raise HTTPException(status_code=400, detail="Session already completed")

    # Increment hints used
    session.hints_used += 1
    db.commit()
    db.refresh(session)

    return SessionSummary(
        session_id=session.id,
        requested_n=session.requested_n,
        pass_count=session.pass_count,
        fail_count=session.fail_count,
        total_time_ms=session.total_time_ms,
        created_at=session.created_at,
        completed_at=session.completed_at,
        session_type=session.session_type,
        target_accuracy=session.target_accuracy,
        target_time_minutes=session.target_time_minutes,
        current_streak=session.current_streak,
        best_streak=session.best_streak,
        hints_used=session.hints_used,
        focus_cause=(session.session_data or {}).get("focus_cause"),
        focus_opening=(session.session_data or {}).get("focus_opening"),
        focus_opening_scope=(session.session_data or {}).get("focus_opening_scope"),
        motif=(session.session_data or {}).get("motif"),
    )
