import os
import sys
from pathlib import Path

# Load .env before any project imports read os.environ
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Add project root to path to verify imports work even if CWD is services/api
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import asyncio

from services.api.auth import require_operator
from services.api.db import SessionLocal, get_db
from services.api.engine import (
    EngineNotAvailableError,
    InvalidFenError,
    get_or_compute_eval,
    is_engine_available,
)
from services.api.jobs.cleanup_sessions import cleanup_abandoned_sessions
from services.api.models import (
    Job,
    JobStatus,
    PuzzleResult,
    PuzzleReview,
    PuzzleStats,
    RatingSnapshot,
)
from services.api.motifs import MotifPerformanceResponse, get_user_motif_performance
from services.api.openings import build_opening_tree
from services.api.puzzles.identity import backfill_puzzle_identity
from services.api.storage import GameRepository, PuzzleRepository
from services.api.storage.spaced_repetition import (
    _utcnow_naive,
    get_adaptive_puzzles,
    get_all_puzzle_stats,
    get_due_puzzle_count,
    get_next_due_date,
    get_puzzle_stats,
    insert_puzzle_review,
    update_puzzle_stats,
)
from services.api.time_control import classify_time_control
from services.api.worker import worker
from services.ingest import (
    ChessGame,
    NetworkError,
    RateLimitError,
    UserNotFoundError,
    get_player_profile,
    get_player_stats,
    import_all_games,
)
from services.ingest import (
    ImportError as ChessComImportError,
)

CLEANUP_INTERVAL_SECONDS = 3600

# Commit imported games in batches instead of once per game: a full import can
# span tens of thousands of games, and per-game commits hammer Postgres.
IMPORT_COMMIT_BATCH_SIZE = 200

# Rating explain thresholds
PERFORMANCE_DIFF_THRESHOLD = 0.5
RATING_DIFFERENCE_THRESHOLD = 100
SIGNIFICANT_WINS_VS_HIGHER_THRESHOLD = 2
SIGNIFICANT_LOSSES_VS_LOWER_THRESHOLD = 2
OPPONENT_RATING_STD_DEV_THRESHOLD = 150

# Chess.com draw result values
DRAW_RESULTS = frozenset(
    [
        "repetition",
        "agreed",
        "timevsinsufficient",
        "stalemate",
        "insufficient",
        "50move",
    ]
)


async def run_session_cleanup():
    """Background task to cleanup abandoned sessions periodically."""
    while True:
        try:
            # Run cleanup
            with SessionLocal() as db:
                await asyncio.to_thread(cleanup_abandoned_sessions, db)
        except Exception as e:
            print(f"Error in session cleanup: {e}")

        # Sleep for defined interval
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prevent worker startup in tests or if explicitly disabled
    if os.environ.get("KNIGHTMIND_WORKER_DISABLED") != "true":
        worker.start()

    # Backfill identity (title/motif) for any existing puzzles missing them.
    # Run in a thread to avoid blocking the async event loop.
    def _run_backfill():
        with SessionLocal() as db:
            backfill_puzzle_identity(db)

    await anyio.to_thread.run_sync(_run_backfill)

    # Start session cleanup background task if not disabled
    cleanup_task = None
    if os.environ.get("KNIGHTMIND_WORKER_DISABLED") != "true":
        cleanup_task = asyncio.create_task(run_session_cleanup())

    yield

    # Cancel cleanup task on shutdown
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    await worker.stop()


app = FastAPI(title="KnightMind API", version="0.1.0", lifespan=lifespan)

from services.api.ops import router as ops_router

app.include_router(ops_router)

from services.api.sessions import router as sessions_router

app.include_router(sessions_router)

from services.api.dashboard import router as dashboard_router

app.include_router(dashboard_router)


def get_allowed_origins() -> list[str]:
    origins = os.environ.get("KNIGHTMIND_CORS_ORIGINS", "")
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ImportResponse(BaseModel):
    message: str
    games_count: int
    new_games: int
    skipped_duplicates: int


class ImportStatusResponse(BaseModel):
    last_imported_at: str | None
    last_new_games: int | None


class UserStatusResponse(BaseModel):
    username: str
    games_count: int
    puzzles_count: int
    due_count: int
    next_due_at: datetime | None
    has_new_games: bool


@app.get("/users", dependencies=[Depends(require_operator)])
async def get_users(db: Session = Depends(get_db)):
    """Get list of all users who have imported games.

    Operator-only (enumerates every account) — gated to the tailnet. The public
    app only ever looks up a single known username via /users/{username}/...
    """
    game_repository = GameRepository(db)
    users = game_repository.get_users()
    return {"users": users}


@app.get("/users/{username}/status", response_model=UserStatusResponse)
async def get_user_status(username: str, db: Session = Depends(get_db)):
    """Get training status for a user to support empty states."""
    game_repository = GameRepository(db)
    puzzle_repository = PuzzleRepository(db)

    games_count = game_repository.get_game_count(username)
    puzzles_count = puzzle_repository.get_puzzle_count(username)

    # Use optimized queries instead of fetching all data
    latest_game_time = game_repository.get_latest_game_time(username)
    latest_puzzle_time = (
        puzzle_repository.get_latest_puzzle_time(username)
        if puzzles_count > 0
        else None
    )

    has_new_games = False
    if latest_game_time:
        if latest_puzzle_time is None or latest_game_time > latest_puzzle_time:
            has_new_games = True

    # Use efficient count queries
    due_count = 0
    next_due_at = None
    if puzzles_count > 0:
        due_count = get_due_puzzle_count(db, username)
        next_due_at = get_next_due_date(db, username)

        # If no stats exist, all puzzles are due
        total_stats = (
            db.scalar(
                select(func.count(PuzzleStats.puzzle_id)).where(
                    PuzzleStats.username == username
                )
            )
            or 0
        )
        if total_stats == 0:
            due_count = puzzles_count

    return UserStatusResponse(
        username=username,
        games_count=games_count,
        puzzles_count=puzzles_count,
        due_count=due_count,
        next_due_at=next_due_at,
        has_new_games=has_new_games,
    )


@app.get(
    "/users/{username}/motifs/performance", response_model=MotifPerformanceResponse
)
async def get_motif_performance(username: str, db: Session = Depends(get_db)):
    """Get user's performance breakdown across all chess tactical patterns/motifs."""
    return get_user_motif_performance(db, username)


@app.get("/users/validate")
async def validate_user(username: str):
    """
    Validate if a user exists on Chess.com.
    Proxies the request to avoid CORS issues and expose internal APIs.
    """
    username = username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")

    try:
        profile = await get_player_profile(username)
    except UserNotFoundError:
        return {"valid": False, "error": "User not found"}
    except RateLimitError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": str(e.retry_after)} if e.retry_after else None,
        ) from e
    except NetworkError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ChessComImportError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return {"valid": True, "username": profile.get("username", username)}


@app.post("/import/chesscom", response_model=ImportResponse)
async def import_chesscom_games(username: str, db: Session = Depends(get_db)):
    """
    Import games from Chess.com for a specific user.
    """
    try:
        count = 0
        new_games = 0
        skipped = 0

        game_repository = GameRepository(db)

        def persist_batch(games: list[ChessGame]) -> None:
            """Store a batch of games in a single transaction.

            Runs on a worker thread (via asyncio.to_thread) so the blocking
            SQLAlchemy work never starves the event loop. Batches are awaited
            sequentially, so only one thread touches the session at a time.
            """
            nonlocal new_games, skipped
            for game in games:
                try:
                    is_new, _ = game_repository.store_game(
                        username=username,
                        url=game.url,
                        pgn=game.pgn,
                        white_username=game.white_username,
                        black_username=game.black_username,
                        white_result=game.white_result,
                        black_result=game.black_result,
                        time_control=game.time_control,
                        end_time=game.end_time,
                        rated=game.rated,
                        commit=False,
                    )
                except ValueError:
                    # A single malformed game (e.g. empty/missing url, which
                    # store_game rejects to avoid identity collapse) must not
                    # abort the whole import: skip it and keep the rest.
                    skipped += 1
                    continue
                if is_new:
                    new_games += 1
                else:
                    skipped += 1
            db.commit()

        batch: list[ChessGame] = []
        async for game in import_all_games(username):
            count += 1
            batch.append(game)
            if len(batch) >= IMPORT_COMMIT_BATCH_SIZE:
                await asyncio.to_thread(persist_batch, batch)
                batch = []

        if batch:
            await asyncio.to_thread(persist_batch, batch)

        await asyncio.to_thread(
            game_repository.record_import_summary, username, new_games
        )

        return ImportResponse(
            message=f"Successfully processed {count} games for {username}",
            games_count=count,
            new_games=new_games,
            skipped_duplicates=skipped,
        )

    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RateLimitError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": str(e.retry_after)} if e.retry_after else None,
        ) from e
    except NetworkError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ChessComImportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Internal server error: {str(e)}"
        ) from e


@app.get("/import/status", response_model=ImportStatusResponse)
async def get_import_status(username: str, db: Session = Depends(get_db)):
    """Get the last import summary for a user."""
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    game_repository = GameRepository(db)
    summary = game_repository.get_last_import_summary(username)
    if not summary:
        return ImportStatusResponse(last_imported_at=None, last_new_games=None)
    return ImportStatusResponse(
        last_imported_at=summary.get("last_imported_at"),
        last_new_games=summary.get("last_new_games"),
    )


class EvalRequest(BaseModel):
    fen: str


class EvalResponse(BaseModel):
    best_move_uci: str
    eval: float  # In pawns, from side-to-move perspective


class EngineStatusResponse(BaseModel):
    available: bool
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    message: str | None = None
    progress: int = 0
    result: dict | None = None
    error: str | None = None


class DailyPuzzlesResponse(BaseModel):
    puzzles: list[dict]
    count: int


class DailyPuzzleSessionRequest(BaseModel):
    username: str
    n: int = 5


class DuePuzzlesResponse(BaseModel):
    due_count: int
    returned_count: int
    now: datetime
    puzzles: list[dict]


class PuzzleListItem(BaseModel):
    id: str
    title: str | None
    primary_motif: str | None
    difficulty: str  # "easy" | "medium" | "hard"
    swing: float
    fen: str
    side_to_move: str
    best_move_uci: str
    status: str  # "new" | "due" | "learning" | "mastered"
    attempts: int
    pass_count: int
    fail_count: int
    last_reviewed_at: datetime | None
    last_result: str | None
    next_due_at: datetime | None
    created_at: datetime | None


class PuzzleCorpusStats(BaseModel):
    total: int
    due: int
    new: int
    learning: int
    mastered: int


class PuzzleListResponse(BaseModel):
    puzzles: list[PuzzleListItem]
    total: int
    limit: int
    offset: int
    available_motifs: list[str]
    stats: PuzzleCorpusStats


class ReviewRequest(BaseModel):
    username: str
    result: PuzzleResult
    time_spent_ms: int | None = None
    session_id: str | None = None
    # Optional client-supplied idempotency key (stable per puzzle presentation).
    # A retried/double-submitted review with the same key is replayed without
    # re-counting stats/session or advancing scheduling.
    client_review_id: str | None = None


@app.get("/")
async def root():
    return {"message": "KnightMind API", "version": "0.1.0"}


@app.get("/openings")
async def get_openings(
    username: str = Query(..., description="Username to build opening tree for"),
    color: Literal["white", "black", "both"] = Query(
        "both", description="Filter by player's color"
    ),
    max_ply: int = Query(
        12, ge=1, le=40, description="Maximum number of half-moves to include"
    ),
    db: Session = Depends(get_db),
):
    """
    Get the opening tree for a user's games.

    Builds a tree structure from the user's stored PGN games showing:
    - move_san: The move in Standard Algebraic Notation
    - ply: Half-move number (1 = white's first, 2 = black's first, etc.)
    - games_count: Number of games reaching this position
    - wins/draws/losses: Results from the player's perspective
    - win_rate: Win percentage (wins + 0.5*draws) / games
    - children: Subsequent moves played from this position

    Args:
        username: The username to build the tree for (must have imported games)
        color: Filter games by the player's color ("white", "black", or "both")
        max_ply: Maximum depth in half-moves (default 12 = 6 full moves each side)

    Returns:
        Opening tree as nested JSON structure
    """
    game_repository = GameRepository(db)

    # Check if user has any games
    game_count = game_repository.get_game_count(username)
    if game_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}'. Import games first using POST /import/chesscom",
        )

    # Stream all PGNs for the user in bulk batches (one query per batch)
    # instead of one query per game, without holding every blob in memory.
    metadata_list = game_repository.get_all_metadata(username)
    game_ids = [meta.game_id for meta in metadata_list]
    pgn_count = 0

    def _iter_pgn_texts():
        nonlocal pgn_count
        for pgn in game_repository.iter_pgns(username, game_ids):
            pgn_count += 1
            yield pgn

    # Build the opening tree
    tree = build_opening_tree(
        pgn_texts=_iter_pgn_texts(),
        player_username=username,
        color_filter=color,
        max_ply=max_ply,
    )

    if metadata_list and pgn_count == 0:
        raise HTTPException(
            status_code=503,
            detail="Games found but PGN content is missing. Re-import games to populate PGN data.",
        )

    return tree


@app.get("/engine/status", response_model=EngineStatusResponse)
async def get_engine_status():
    """Check if the Stockfish engine is available."""
    available, message = is_engine_available()
    return EngineStatusResponse(available=available, message=message)


@app.post("/engine/eval", response_model=EvalResponse)
async def evaluate_fen(request: EvalRequest):
    """Evaluate a chess position using Stockfish with caching."""
    try:
        result = await asyncio.to_thread(get_or_compute_eval, request.fen)
        return EvalResponse(best_move_uci=result.best_move_uci, eval=result.eval)
    except EngineNotAvailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except InvalidFenError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/puzzles/generate", response_model=JobStatusResponse)
async def generate_puzzles_endpoint(
    username: str = Query(..., description="Username to generate puzzles for"),
    max_games: int = Query(
        30, ge=1, le=2000, description="Maximum number of recent games to analyze"
    ),
    max_puzzles: int = Query(
        30, ge=1, le=2000, description="Maximum number of puzzles to generate"
    ),
    db: Session = Depends(get_db),
):
    """Start a background job to generate puzzles."""
    try:
        new_job = Job(
            username=username,
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
        stmt = select(Job).where(
            Job.username == username,
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
                .where(Job.username == username)
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


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, db: Session = Depends(get_db)):
    """Get status of a specific job."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        message=job.message,
        progress=job.progress_current,
        result=job.result_json,
        error=job.error_message,
    )


@app.post("/jobs/{job_id}/cancel", response_model=JobStatusResponse)
async def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """Cancel a running or queued job."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Only allow cancellation of queued or running jobs
    if job.status not in [JobStatus.QUEUED, JobStatus.RUNNING]:
        raise HTTPException(
            status_code=400, detail=f"Cannot cancel job with status '{job.status}'"
        )

    # Update job status to canceled
    job.status = JobStatus.CANCELED
    job.message = "Canceled by user"
    job.updated_at = datetime.now(timezone.utc)
    db.commit()

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        message=job.message,
        progress=job.progress_current,
        result=job.result_json,
        error=job.error_message,
    )


@app.post("/daily-puzzle-sessions", response_model=DailyPuzzlesResponse)
async def create_daily_puzzle_session(
    request: DailyPuzzleSessionRequest, db: Session = Depends(get_db)
):
    """Create a new daily puzzle session for a user."""
    username = request.username
    n = request.n

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

    # Mark puzzles as used today
    puzzle_ids = [p.id for p in puzzles]
    puzzle_repository.mark_puzzles_used(username, puzzle_ids, date.today())

    # Reload specific puzzles to get updated used_on field
    updated_puzzles = [
        puzzle_repository.get_puzzle(username, pid) for pid in puzzle_ids
    ]
    updated_puzzles = [p for p in updated_puzzles if p is not None]

    # Get puzzle stats to include primary_motif
    all_stats = get_all_puzzle_stats(db, username)

    # Convert to dict format for response and merge with stats
    puzzles_dict = []
    for p in updated_puzzles:
        p_dict = asdict(p)
        stats = all_stats.get(p.id)
        if stats:
            p_dict["primary_motif"] = stats.primary_motif
            p_dict["title"] = stats.title
        else:
            p_dict["primary_motif"] = None
            p_dict["title"] = None
        puzzles_dict.append(p_dict)

    return DailyPuzzlesResponse(puzzles=puzzles_dict, count=len(puzzles_dict))


@app.get("/puzzles/due", response_model=DuePuzzlesResponse)
async def get_due_puzzles_endpoint(
    username: str = Query(..., description="Username to get puzzles for"),
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
    db: Session = Depends(get_db),
):
    """
    Get puzzles due for review, followed by new puzzles.
    Supports adaptive selection based on session type and target accuracy.
    Optionally filter by specific chess motif.
    """
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

    # 3. Get prioritized IDs and their stats using adaptive selection
    due_ids, all_stats = get_adaptive_puzzles(
        db, username, puzzle_ids, n, session_type, target_accuracy
    )

    # 3. Load content and merge with stats
    result_puzzles = []
    for pid in due_ids:
        puzzle = puzzle_repository.get_puzzle(username, pid)
        if not puzzle:
            continue

        p_dict = asdict(puzzle)
        stats = all_stats.get(pid)
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
                    "title": stats.title,
                    "primary_motif": stats.primary_motif,
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
                }
            )
        result_puzzles.append(p_dict)

    # 4. Total due count for metadata
    # 4. Total due count for metadata
    # We can calculate this from all_stats since it contains all stats for the user's puzzles
    now = datetime.now(timezone.utc)
    due_count = sum(
        1
        for s in all_stats.values()
        if s.next_due_at and s.next_due_at.replace(tzinfo=timezone.utc) <= now
    )

    return {
        "due_count": due_count,
        "returned_count": len(result_puzzles),
        "now": now,
        "puzzles": result_puzzles,
    }


def _swing_to_difficulty(swing: float) -> str:
    if swing < 2.0:
        return "easy"
    if swing < 5.0:
        return "medium"
    return "hard"


@app.get("/puzzles/list", response_model=PuzzleListResponse)
async def list_puzzles(
    username: str = Query(..., description="Username to list puzzles for"),
    q: str = Query(None, description="Search by title or puzzle ID"),
    status: str = Query(None, description="Filter: new, due, learning, mastered"),
    motif: str = Query(
        None, description="Filter by primary_motif (comma-separated for OR)"
    ),
    difficulty: str = Query(None, description="Filter: easy, medium, hard"),
    sort: str = Query(
        "due_soonest",
        description="Sort: due_soonest, last_attempted, most_failed, difficulty_asc, difficulty_desc, newest",
    ),
    limit: int = Query(50, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
):
    """
    List all puzzles for a user with filtering, search, sorting, and pagination.
    Filtering, sorting, and pagination are performed in SQL for scalability.
    """
    from services.api.models import Puzzle as PuzzleModel

    # naive-UTC bound for SQL comparisons against naive next_due_at columns
    # (see spaced_repetition module note); an aware now would misclassify on
    # Postgres with a non-UTC session TimeZone.
    now = _utcnow_naive()
    username_lower = username.lower()

    join_cond = (PuzzleModel.id == PuzzleStats.puzzle_id) & (
        PuzzleStats.username == username_lower
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

    # --- 3. Build filtered query ---
    # Reuse status_case from corpus stats so status logic is defined once.
    computed_status = status_case.label("computed_status")

    base_stmt = (
        select(PuzzleModel, PuzzleStats, computed_status)
        .outerjoin(PuzzleStats, join_cond)
        .where(PuzzleModel.username == username_lower)
    )

    # Search filter
    if q:
        q_pattern = f"%{q.lower()}%"
        base_stmt = base_stmt.where(
            or_(
                func.lower(PuzzleStats.title).like(q_pattern),
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
    for puzzle, stats, row_status in rows:
        result_puzzles.append(
            PuzzleListItem(
                id=puzzle.id,
                title=stats.title if stats else None,
                primary_motif=stats.primary_motif if stats else None,
                difficulty=_swing_to_difficulty(puzzle.swing),
                swing=puzzle.swing,
                fen=puzzle.fen,
                side_to_move=puzzle.side_to_move,
                best_move_uci=puzzle.best_move_uci,
                status=row_status,
                attempts=stats.attempts if stats else 0,
                pass_count=stats.pass_count if stats else 0,
                fail_count=stats.fail_count if stats else 0,
                last_reviewed_at=stats.last_reviewed_at if stats else None,
                last_result=stats.last_result if stats else None,
                next_due_at=stats.next_due_at if stats else None,
                created_at=puzzle.created_at,
            )
        )

    return PuzzleListResponse(
        puzzles=result_puzzles,
        total=total,
        limit=limit,
        offset=offset,
        available_motifs=available_motifs,
        stats=PuzzleCorpusStats(
            total=corpus_total,
            due=cr.cnt_due or 0,
            new=cr.cnt_new or 0,
            learning=cr.cnt_learning or 0,
            mastered=cr.cnt_mastered or 0,
        ),
    )


@app.get("/puzzles/{puzzle_id}", response_model=PuzzleListItem)
async def get_puzzle_detail(
    puzzle_id: str,
    username: str = Query(..., description="Username to look up puzzle for"),
    db: Session = Depends(get_db),
):
    """Get a single puzzle by ID with user stats."""
    from services.api.models import Puzzle as PuzzleModel

    username_lower = username.lower()
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
    return PuzzleListItem(
        id=puzzle.id,
        title=stats.title if stats else None,
        primary_motif=stats.primary_motif if stats else None,
        difficulty=_swing_to_difficulty(puzzle.swing),
        swing=puzzle.swing,
        fen=puzzle.fen,
        side_to_move=puzzle.side_to_move,
        best_move_uci=puzzle.best_move_uci,
        status=computed_status,
        attempts=stats.attempts if stats else 0,
        pass_count=stats.pass_count if stats else 0,
        fail_count=stats.fail_count if stats else 0,
        last_reviewed_at=stats.last_reviewed_at if stats else None,
        last_result=stats.last_result if stats else None,
        next_due_at=stats.next_due_at if stats else None,
        created_at=puzzle.created_at,
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


def _build_review_response(stats, puzzle_stats, result) -> dict:
    """Build the review endpoint payload for a given (stats, result).

    Shared by the normal path and the idempotent-replay path so a replayed
    review returns the same shape without re-running any scheduling logic.
    """
    result_val = result.value if isinstance(result, PuzzleResult) else result
    feedback_message = ""
    if result_val == "pass":
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
        "stats": {
            "attempts": stats.attempts,
            "pass_count": stats.pass_count,
            "fail_count": stats.fail_count,
            "last_reviewed_at": stats.last_reviewed_at,
            "last_result": stats.last_result,
        },
    }


@app.post("/puzzles/{puzzle_id}/review")
async def review_puzzle(
    puzzle_id: str, request: ReviewRequest, db: Session = Depends(get_db)
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
    puzzle_repository = PuzzleRepository(db)
    puzzle = puzzle_repository.get_puzzle(request.username, puzzle_id)
    if not puzzle:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    # Idempotent replay: short-circuit before any mutation if this exact
    # client_review_id was already recorded for this (puzzle, user, session).
    if request.client_review_id:
        existing = _find_existing_review(
            db,
            puzzle_id,
            request.username,
            request.session_id,
            request.client_review_id,
        )
        if existing:
            stats = get_puzzle_stats(db, puzzle_id, request.username)
            puzzle_stats = puzzle_repository.get_puzzle_stats(
                request.username, puzzle_id
            )
            return _build_review_response(stats, puzzle_stats, existing.result)

    # If session_id provided, validate session and update counters
    if request.session_id:
        from services.api.models import PuzzleResult as PR
        from services.api.models import TrainingSession

        stmt = select(TrainingSession).where(TrainingSession.id == request.session_id)
        session = db.scalars(stmt).first()

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.username != request.username:
            raise HTTPException(
                status_code=403, detail="Session belongs to different user"
            )

        if session.completed_at is not None:
            raise HTTPException(status_code=400, detail="Session already completed")

        # Increment session counters (will be committed with review)
        if request.result == PR.PASS:
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
        # 1. Record individual review (with optional session_id + idempotency key)
        insert_puzzle_review(
            db,
            puzzle_id,
            request.username,
            request.result,
            request.time_spent_ms,
            session_id=request.session_id,
            client_review_id=request.client_review_id,
        )

        # 2. Update aggregate stats (triggers scheduling logic)
        stats = update_puzzle_stats(db, puzzle_id, request.username, request.result)

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
                request.session_id,
                request.client_review_id,
            )
            if existing:
                stats = get_puzzle_stats(db, puzzle_id, request.username)
                puzzle_stats = puzzle_repository.get_puzzle_stats(
                    request.username, puzzle_id
                )
                return _build_review_response(stats, puzzle_stats, existing.result)
        raise

    # 5. Build the response (feedback + scheduling + stats)
    return _build_review_response(stats, puzzle_stats, request.result)


# --- Rating Drivers Explainer Support ---


def get_opponent_rating_from_pgn(pgn: str, user_is_white: bool) -> int | None:
    """Extract opponent rating from PGN headers."""
    tag = "BlackElo" if user_is_white else "WhiteElo"
    match = re.search(f'\\[{tag} "(\\d+)"\\]', pgn)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def calculate_expected_score(player_rating: int, opponent_rating: int) -> float:
    """Calculates the expected score for a player based on Elo ratings."""
    return 1 / (1 + 10 ** ((opponent_rating - player_rating) / 400))


class SnapshotRequest(BaseModel):
    username: str
    time_control: Literal["rapid", "blitz", "bullet"]


class SnapshotResponse(BaseModel):
    rating: int
    recorded_at: datetime


@app.post("/ratings/snapshot", response_model=SnapshotResponse)
async def create_rating_snapshot(
    request: SnapshotRequest, db: Session = Depends(get_db)
):
    """Fetch current rating from Chess.com and store a snapshot."""
    try:
        stats = await get_player_stats(request.username)

        # Parse rating: { "chess_rapid": { "last": { "rating": ... } } }
        tc_key = f"chess_{request.time_control}"
        if not (rating := stats.get(tc_key, {}).get("last", {}).get("rating")):
            raise HTTPException(
                status_code=502,
                detail=f"Could not find rating for {request.time_control} in Chess.com response",
            )

        snapshot = RatingSnapshot(
            username=request.username,
            source="chesscom",
            time_control=request.time_control,
            rating=rating,
            recorded_at=datetime.now(timezone.utc),
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        return SnapshotResponse(
            rating=snapshot.rating, recorded_at=snapshot.recorded_at
        )

    except (UserNotFoundError, NetworkError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e)) from e


class SnapshotHistoryItem(BaseModel):
    rating: int
    recorded_at: datetime


@app.get("/ratings/history", response_model=list[SnapshotHistoryItem])
async def get_rating_history(
    username: str,
    time_control: str = "rapid",
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Return chronological rating snapshot history for charting.

    Fetches the most recent `limit` snapshots (desc) then reverses to
    chronological order so the frontend chart always shows the latest window.
    """
    stmt = (
        select(RatingSnapshot)
        .where(
            RatingSnapshot.username == username,
            RatingSnapshot.time_control == time_control,
        )
        .order_by(RatingSnapshot.recorded_at.desc())
        .limit(limit)
    )
    snapshots = list(reversed(db.scalars(stmt).all()))
    return [
        SnapshotHistoryItem(rating=s.rating, recorded_at=s.recorded_at)
        for s in snapshots
    ]


class HighlightGame(BaseModel):
    opponent_rating: int | None
    result: str
    expected_score: float
    rating_diff: int | None
    game_id: str
    played_at: datetime
    url: str


class Highlights(BaseModel):
    best_surprises: list[HighlightGame]
    worst_surprises: list[HighlightGame]


class RatingWindow(BaseModel):
    start: datetime
    end: datetime
    source: str


class RatingInfo(BaseModel):
    start: int | None
    end: int | None
    net_change: int | None
    reference_rating: int
    reference_is_approx: bool


class DriverStats(BaseModel):
    games: int
    wins: int
    draws: int
    losses: int
    avg_opponent_rating: int | None
    expected_total: float | None
    actual_total: float | None
    actual_minus_expected: float | None
    missing_opponent_rating_games: int


class Driver(BaseModel):
    text: str
    severity: Literal["major", "moderate", "minor"]
    direction: Literal["up", "down", "neutral"]


class ExplainResponse(BaseModel):
    time_control: str
    window: RatingWindow
    rating: RatingInfo
    stats: DriverStats
    drivers: list[Driver]
    highlights: Highlights


@app.get("/ratings/explain", response_model=ExplainResponse)
async def explain_rating_changes(
    username: str,
    time_control: str = "rapid",
    since_session_id: str | None = None,
    since: datetime | None = None,
    limit_games: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Explain rating drivers based on recent games."""
    from services.api.models import TrainingSession

    # 1. Determine Window
    now = datetime.now(timezone.utc)
    window_start: datetime
    source_type: str

    if since_session_id:
        session = db.get(TrainingSession, since_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        window_start = session.created_at.replace(tzinfo=timezone.utc)
        source_type = "session"
    elif since:
        window_start = since
        source_type = "since"
    else:
        # Fallback: Last session or 7 days
        stmt = (
            select(TrainingSession)
            .where(TrainingSession.username == username)
            .order_by(TrainingSession.created_at.desc())
        )
        last_session = db.scalars(stmt).first()
        if last_session:
            window_start = last_session.created_at.replace(tzinfo=timezone.utc)
            source_type = "last_session"
        else:
            window_start = now - timedelta(days=7)
            source_type = "fallback_7d"

    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)

    # 2. Load Games
    game_repository = GameRepository(db)
    all_metadata = game_repository.get_all_metadata(username)

    start_ts = int(window_start.timestamp())
    relevant_games = []

    count = 0
    for meta in all_metadata:
        if count >= limit_games:
            break
        if classify_time_control(meta.time_control) != time_control.lower():
            continue
        if meta.end_time < start_ts:
            break

        relevant_games.append(meta)
        count += 1

    relevant_games.reverse()

    # 3. Process Games
    wins = 0
    draws = 0
    losses = 0
    total_opp_rating = 0
    opp_rating_count = 0
    missing_ratings = 0

    game_details = []
    opp_ratings = []

    # Snapshot closest to (but before or at) window start — best reference anchor
    stmt = (
        select(RatingSnapshot)
        .where(
            RatingSnapshot.username == username,
            RatingSnapshot.time_control == time_control,
            RatingSnapshot.recorded_at <= window_start,
        )
        .order_by(RatingSnapshot.recorded_at.desc())
    )
    pre_window_snapshot = db.scalars(stmt).first()

    # Earliest snapshot inside the window
    stmt = (
        select(RatingSnapshot)
        .where(
            RatingSnapshot.username == username,
            RatingSnapshot.time_control == time_control,
            RatingSnapshot.recorded_at >= window_start,
        )
        .order_by(RatingSnapshot.recorded_at.asc())
    )
    earliest_snapshot = db.scalars(stmt).first()

    reference_rating = 0
    reference_is_approx = False

    if pre_window_snapshot:
        reference_rating = pre_window_snapshot.rating
    elif earliest_snapshot:
        reference_rating = earliest_snapshot.rating

    # Bulk-load PGNs for the selected window in one query (the window is
    # bounded by limit_games) instead of one query per game.
    pgns_by_game_id = game_repository.get_pgns(
        username, [meta.game_id for meta in relevant_games]
    )

    for meta in relevant_games:
        pgn = pgns_by_game_id.get(meta.game_id)
        if not pgn:
            continue

        user_is_white = meta.white_username.lower() == username.lower()

        result_score = 0.0
        if user_is_white:
            if meta.white_result == "win":
                result_score = 1.0
            elif meta.white_result in DRAW_RESULTS:
                result_score = 0.5
        else:
            if meta.black_result == "win":
                result_score = 1.0
            elif meta.black_result in DRAW_RESULTS:
                result_score = 0.5

        if result_score == 1.0:
            wins += 1
        elif result_score == 0.5:
            draws += 1
        else:
            losses += 1

        opp_rating = get_opponent_rating_from_pgn(pgn, user_is_white)

        if opp_rating is None:
            missing_ratings += 1
            game_details.append(
                {
                    "meta": meta,
                    "opp_rating": None,
                    "actual": result_score,
                    "expected": None,
                }
            )
        else:
            total_opp_rating += opp_rating
            opp_rating_count += 1
            opp_ratings.append(opp_rating)
            game_details.append(
                {
                    "meta": meta,
                    "opp_rating": opp_rating,
                    "actual": result_score,
                    "expected": None,
                }
            )

    if reference_rating == 0:
        if opp_rating_count > 0:
            reference_rating = int(total_opp_rating / opp_rating_count)
        else:
            reference_rating = 1200
        reference_is_approx = True

    expected_total = 0.0
    actual_total_rated = 0.0
    surprises = []

    for item in game_details:
        if item["opp_rating"] is not None:
            r_opp = item["opp_rating"]
            expected = calculate_expected_score(reference_rating, r_opp)
            item["expected"] = expected

            expected_total += expected
            actual_total_rated += item["actual"]

            surprises.append(
                HighlightGame(
                    opponent_rating=r_opp,
                    result=(
                        "Win"
                        if item["actual"] == 1.0
                        else "Draw" if item["actual"] == 0.5 else "Loss"
                    ),
                    expected_score=round(expected, 2),
                    rating_diff=r_opp - reference_rating,
                    game_id=item["meta"].game_id,
                    played_at=datetime.fromtimestamp(
                        item["meta"].end_time, tz=timezone.utc
                    ),
                    url=item["meta"].url,
                )
            )

    avg_opp = int(total_opp_rating / opp_rating_count) if opp_rating_count > 0 else None

    drivers: list[Driver] = []
    diff = actual_total_rated - expected_total

    if diff > PERFORMANCE_DIFF_THRESHOLD:
        severity = (
            "major" if abs(diff) > 2.0 else "moderate" if abs(diff) > 1.0 else "minor"
        )
        drivers.append(
            Driver(
                text=f"You outperformed expectations by {diff:+.1f} points (upward pressure).",
                severity=severity,
                direction="up",
            )
        )
    elif diff < -PERFORMANCE_DIFF_THRESHOLD:
        severity = (
            "major" if abs(diff) > 2.0 else "moderate" if abs(diff) > 1.0 else "minor"
        )
        drivers.append(
            Driver(
                text=f"You underperformed expectations by {diff:+.1f} points (downward pressure).",
                severity=severity,
                direction="down",
            )
        )

    wins_vs_higher = sum(
        1
        for g in game_details
        if g["opp_rating"]
        and g["opp_rating"] >= reference_rating + RATING_DIFFERENCE_THRESHOLD
        and g["actual"] == 1.0
    )
    if wins_vs_higher >= SIGNIFICANT_WINS_VS_HIGHER_THRESHOLD:
        drivers.append(
            Driver(
                text=f"{wins_vs_higher} wins against higher-rated opponents likely offset losses.",
                severity="moderate" if wins_vs_higher >= 4 else "minor",
                direction="up",
            )
        )

    losses_vs_lower = sum(
        1
        for g in game_details
        if g["opp_rating"]
        and g["opp_rating"] <= reference_rating - RATING_DIFFERENCE_THRESHOLD
        and g["actual"] == 0.0
    )
    if losses_vs_lower >= SIGNIFICANT_LOSSES_VS_LOWER_THRESHOLD:
        drivers.append(
            Driver(
                text=f"{losses_vs_lower} losses against lower-rated opponents likely drove most of the drop.",
                severity="moderate" if losses_vs_lower >= 4 else "minor",
                direction="down",
            )
        )

    if len(opp_ratings) >= 5:
        variance = sum((x - avg_opp) ** 2 for x in opp_ratings) / len(opp_ratings)
        std_dev = variance**0.5
        if std_dev > OPPONENT_RATING_STD_DEV_THRESHOLD:
            drivers.append(
                Driver(
                    text="Wide opponent rating range increased volatility.",
                    severity="minor",
                    direction="neutral",
                )
            )

    # Sort drivers by severity (major first)
    severity_order = {"major": 0, "moderate": 1, "minor": 2}
    drivers.sort(key=lambda d: severity_order[d.severity])

    def get_surprise_val(h: HighlightGame):
        act = 1.0 if h.result == "Win" else 0.5 if h.result == "Draw" else 0.0
        return act - h.expected_score

    surprises.sort(key=get_surprise_val, reverse=True)
    best_surprises = [s for s in surprises if get_surprise_val(s) > 0][:3]
    worst_surprises = [s for s in surprises if get_surprise_val(s) < 0][-3:]
    worst_surprises.reverse()

    # Start: prefer pre-window snapshot, fall back to earliest in-window
    start_rating_val = (
        pre_window_snapshot.rating
        if pre_window_snapshot
        else earliest_snapshot.rating if earliest_snapshot else None
    )

    # End: latest snapshot within the window period
    stmt = (
        select(RatingSnapshot)
        .where(
            RatingSnapshot.username == username,
            RatingSnapshot.time_control == time_control,
            RatingSnapshot.recorded_at >= window_start,
        )
        .order_by(RatingSnapshot.recorded_at.desc())
    )
    latest_in_window = db.scalars(stmt).first()
    end_rating_val = latest_in_window.rating if latest_in_window else None

    net_change = None
    if start_rating_val is not None and end_rating_val is not None:
        net_change = end_rating_val - start_rating_val

    return ExplainResponse(
        time_control=time_control,
        window=RatingWindow(start=window_start, end=now, source=source_type),
        rating=RatingInfo(
            start=start_rating_val,
            end=end_rating_val,
            net_change=net_change,
            reference_rating=reference_rating,
            reference_is_approx=reference_is_approx,
        ),
        stats=DriverStats(
            games=len(relevant_games),
            wins=wins,
            draws=draws,
            losses=losses,
            avg_opponent_rating=avg_opp,
            expected_total=expected_total if opp_rating_count > 0 else None,
            actual_total=actual_total_rated if opp_rating_count > 0 else None,
            actual_minus_expected=diff if opp_rating_count > 0 else None,
            missing_opponent_rating_games=missing_ratings,
        ),
        drivers=drivers,
        highlights=Highlights(
            best_surprises=best_surprises, worst_surprises=worst_surprises
        ),
    )
