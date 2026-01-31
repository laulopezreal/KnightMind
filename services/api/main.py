from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone, timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import os
import re
import sys

# Add project root to path to verify imports work even if CWD is services/api
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.api.db import SessionLocal, get_db
from services.api.models import Job, JobStatus, PuzzleStats, PuzzleReview, PuzzleResult
from services.api.worker import worker
from services.api.engine import (
    EngineNotAvailableError,
    InvalidFenError,
    evaluate_position,
    is_engine_available,
    get_or_compute_eval,
)
from services.api.openings import build_opening_tree
from services.api.puzzles import generate_puzzles
from services.api.storage import get_puzzle_storage, get_storage
from services.api.storage.spaced_repetition import (
    get_due_puzzles,
    insert_puzzle_review,
    update_puzzle_stats
)
from services.ingest import (
    ImportError as ChessComImportError,
)
from services.ingest import (
    NetworkError,
    RateLimitError,
    UserNotFoundError,
    fetch_games_from_archive,
    get_player_archives,
    get_player_stats,
    parse_game,
    import_all_games,
)

from services.api.models import Job, JobStatus, PuzzleStats, PuzzleReview, PuzzleResult, RatingSnapshot

from services.api.puzzles.identity import backfill_puzzle_identity
from services.api.jobs.cleanup_sessions import cleanup_abandoned_sessions
import asyncio

CLEANUP_INTERVAL_SECONDS = 3600

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
        
    # NOTE: Backfill should be run manually or via a migration script
    # Running it on startup can block the server if there are many puzzles
    # with SessionLocal() as db:
    #     backfill_puzzle_identity(db)
        
    # Start session cleanup background task
    cleanup_task = asyncio.create_task(run_session_cleanup())
        
    yield
    
    # Cancel cleanup task on shutdown
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

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        # Add your Netlify domain here when deployed
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ImportResponse(BaseModel):
    message: str
    games_count: int
    new_games: int
    skipped_duplicates: int


@app.get("/users")
async def get_users():
    """Get list of users who have imported games."""
    storage = get_storage()
    users = storage.get_users()
    return {"users": users}


@app.get("/users/validate")
async def validate_user(username: str):
    """
    Validate if a user exists on Chess.com.
    Proxies the request to avoid CORS issues and expose internal APIs.
    """
    import httpx
    
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
        
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.chess.com/pub/player/{username}", follow_redirects=True)
            
            if resp.status_code == 200:
                return {"valid": True, "username": username}
            elif resp.status_code == 404:
                return {"valid": False, "error": "User not found"}
            else:
                raise HTTPException(status_code=502, detail="Chess.com API error")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/import/chesscom", response_model=ImportResponse)
async def import_chesscom_games(username: str):
    """
    Import games from Chess.com for a specific user.
    """
    try:
        count = 0
        new_games = 0
        skipped = 0
        
        storage = get_storage()
        
        # Create generator
        games_generator = import_all_games(username)
        
        async for game in games_generator:
            count += 1
            is_new, _ = storage.store_game(
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
            )
            
            if is_new:
                new_games += 1
            else:
                skipped += 1
                
        return ImportResponse(
            message=f"Successfully processed {count} games for {username}",
            games_count=count,
            new_games=new_games,
            skipped_duplicates=skipped
        )
        
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RateLimitError as e:
        raise HTTPException(
            status_code=429, 
            detail=str(e),
            headers={"Retry-After": str(e.retry_after)} if e.retry_after else None
        )
    except NetworkError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ChessComImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


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


class DuePuzzlesResponse(BaseModel):
    due_count: int
    returned_count: int
    now: datetime
    puzzles: list[dict]


class ReviewRequest(BaseModel):
    username: str
    result: PuzzleResult
    time_spent_ms: int | None = None
    session_id: str | None = None


@app.get("/")
async def root():
    return {"message": "KnightMind API", "version": "0.1.0"}


@app.post("/import/chesscom", response_model=ImportResponse)
async def import_chesscom_games(username: str = Query(..., description="Chess.com username")):
    """
    Import games from Chess.com for a given username.
    
    Fetches all games from Chess.com API and stores them locally.
    Duplicate games are skipped automatically.
    """
    storage = get_storage()

    try:
        # Get all archive URLs for the user
        archives = await get_player_archives(username)

        if not archives:
            return ImportResponse(
                message=f"No games found for {username}",
                games_count=0,
                new_games=0,
                skipped_duplicates=0,
            )

        new_games = 0
        skipped = 0

        # Process each monthly archive
        for archive_url in archives:
            games = await fetch_games_from_archive(archive_url)

            for game_data in games:
                game = parse_game(game_data)

                # Skip games without PGN
                if not game.pgn:
                    continue

                is_new, _ = storage.store_game(
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
                )

                if is_new:
                    new_games += 1
                else:
                    skipped += 1

        total_games = storage.get_game_count(username)

        return ImportResponse(
            message=f"Successfully imported games for {username}",
            games_count=total_games,
            new_games=new_games,
            skipped_duplicates=skipped,
        )

    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RateLimitError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": str(e.retry_after)} if e.retry_after else None,
        )
    except NetworkError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ChessComImportError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/openings")
async def get_openings(
    username: str = Query(..., description="Username to build opening tree for"),
    color: Literal["white", "black", "both"] = Query("both", description="Filter by player's color"),
    max_ply: int = Query(12, ge=1, le=40, description="Maximum number of half-moves to include")
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
    storage = get_storage()

    # Check if user has any games
    game_count = storage.get_game_count(username)
    if game_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}'. Import games first using POST /import/chesscom"
        )

    # Get all PGNs for the user
    pgn_texts = []
    metadata_list = storage.get_all_metadata(username)
    for meta in metadata_list:
        pgn = storage.get_pgn(username, meta.game_id)
        if pgn:
            pgn_texts.append(pgn)

    # Build the opening tree
    tree = build_opening_tree(
        pgn_texts=pgn_texts,
        player_username=username,
        color_filter=color,
        max_ply=max_ply
    )

    return tree


@app.get("/engine/status", response_model=EngineStatusResponse)
async def get_engine_status():
    """Check if the Stockfish engine is available."""
    available, message = is_engine_available()
    return EngineStatusResponse(available=available, message=message)


@app.post("/engine/eval", response_model=EvalResponse)
async def evaluate_fen(request: EvalRequest):
    """
    Evaluate a chess position using Stockfish.
    
    Uses cached evaluations when available for better performance.
    
    Args:
        request: Request with FEN string
        
    Returns:
        Best move in UCI format and evaluation in pawns
    """
    try:
        result = get_or_compute_eval(request.fen)
        return EvalResponse(best_move_uci=result.best_move_uci, eval=result.eval)
    except EngineNotAvailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except InvalidFenError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/puzzles/generate", response_model=JobStatusResponse)
async def generate_puzzles_endpoint(
    username: str = Query(..., description="Username to generate puzzles for"),
    max_games: int = Query(30, description="Maximum number of recent games to analyze"),
    max_puzzles: int = Query(30, description="Maximum number of puzzles to generate"),
):
    """
    Start a background job to generate puzzles.
    
    If a job is already running/queued for this user, returns the existing job status.
    """
    with SessionLocal() as db:
        # Optimistic locking: Try to create new job first
        # The unique partial index on (username) WHERE status IN ('queued', 'running')
        # ensures only one active job exists.
        
        try:
            new_job = Job(
                username=username,
                status=JobStatus.QUEUED,
                message="Queued for generation",
                params={"max_games": max_games, "max_puzzles": max_puzzles}
            )
            db.add(new_job)
            db.commit()
            db.refresh(new_job)
            
            return JobStatusResponse(
                job_id=new_job.id,
                status=new_job.status,
                message="Job queued",
                progress=0
            )
            
        except IntegrityError:
            db.rollback()
            # Job already exists, fetch and return it
            stmt = select(Job).where(
                Job.username == username,
                or_(Job.status == JobStatus.QUEUED, Job.status == JobStatus.RUNNING)
            )
            existing_job = db.scalars(stmt).first()
            
            if existing_job:
                return JobStatusResponse(
                    job_id=existing_job.id,
                    status=existing_job.status,
                    message="Job already in progress",
                    progress=existing_job.progress_current
                )
            else:
                # Should be rare: job finished right between insert failure and select
                # Retry or return 500? Use recursion simplistically or just assume queued.
                # If it finished, we can return the finished job if we query for it?
                # But our index is only on active jobs. 
                # If we are here, it means we collided, so it WAS active.
                # If we don't find it now, it means it finished. 
                # Let's find the latest job for user.
                stmt = select(Job).where(Job.username == username).order_by(Job.created_at.desc())
                latest_job = db.scalars(stmt).first()
                if latest_job:
                     return JobStatusResponse(
                        job_id=latest_job.id,
                        status=latest_job.status,
                        message="Job completed recently",
                        progress=latest_job.progress_current,
                        result=latest_job.result_json
                    )
                raise HTTPException(status_code=500, detail="Could not create job or find existing one")


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get status of a specific job."""
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        return JobStatusResponse(
            job_id=job.id,
            status=job.status,
            message=job.message,
            progress=job.progress_current,
            result=job.result_json,
            error=job.error_message
        )


@app.post("/jobs/{job_id}/cancel", response_model=JobStatusResponse)
async def cancel_job(job_id: str):
    """
    Cancel a running or queued job.
    
    Sets the job status to 'canceled' if it's currently queued or running.
    The worker will detect this and stop processing.
    """
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Only allow cancellation of queued or running jobs
        if job.status not in [JobStatus.QUEUED, JobStatus.RUNNING]:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot cancel job with status '{job.status}'"
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
            result=job.result_json
        )


@app.get("/puzzles/daily", response_model=DailyPuzzlesResponse)
async def get_daily_puzzles(
    username: str = Query(..., description="Username to get puzzles for"),
    n: int = Query(5, ge=1, le=20, description="Number of puzzles to return"),
):
    """
    Get daily puzzle set for a user.
    
    Returns n puzzles, preferring unused ones. Marks returned puzzles
    with today's date to enable daily rotation.
    
    Args:
        username: Username to get puzzles for
        n: Number of puzzles (1-20, default 5)
        
    Returns:
        List of puzzles with metadata
    """
    puzzle_storage = get_puzzle_storage()
    
    # Get puzzles using the storage's selection logic
    puzzles = puzzle_storage.get_daily_puzzles(username, n)
    
    if not puzzles:
        raise HTTPException(
            status_code=404,
            detail=f"No puzzles found for user '{username}'. Generate puzzles first using POST /puzzles/generate"
        )
    
    # Mark puzzles as used today
    puzzle_ids = [p.id for p in puzzles]
    puzzle_storage.mark_puzzles_used(username, puzzle_ids, date.today())
    
    # Reload specific puzzles to get updated used_on field
    updated_puzzles = [puzzle_storage.get_puzzle(username, pid) for pid in puzzle_ids]
    updated_puzzles = [p for p in updated_puzzles if p is not None]
    
    # Convert to dict format for response
    puzzles_dict = [asdict(p) for p in updated_puzzles]
    
    return DailyPuzzlesResponse(
        puzzles=puzzles_dict,
        count=len(puzzles_dict)
    )


@app.get("/puzzles/due", response_model=DuePuzzlesResponse)
async def get_due_puzzles_endpoint(
    username: str = Query(..., description="Username to get puzzles for"),
    n: int = Query(5, ge=1, le=20, description="Number of puzzles to return"),
    db: Session = Depends(get_db)
):
    """
    Get puzzles due for review, followed by new puzzles.
    """
    puzzle_storage = get_puzzle_storage()
    
    # 1. Load index to get all candidate IDs
    index = puzzle_storage._get_user_index(username)
    puzzle_ids = list(index.values())
    
    if not puzzle_ids:
        raise HTTPException(
            status_code=404, 
            detail=f"No puzzles found for user '{username}'. Generate puzzles first."
        )
    
    # 2. Get prioritized IDs and their stats
    due_ids, all_stats = get_due_puzzles(db, username, puzzle_ids, n)
    
    # 3. Load content and merge with stats
    result_puzzles = []
    for pid in due_ids:
        puzzle = puzzle_storage.get_puzzle(username, pid)
        if not puzzle:
            continue
        
        p_dict = asdict(puzzle)
        stats = all_stats.get(pid)
        if stats:
            p_dict.update({
                "next_due_at": stats.next_due_at,
                "interval_days": stats.interval_days,
                "ease_factor": stats.ease_factor,
                "attempts": stats.attempts,
                "pass_count": stats.pass_count,
                "fail_count": stats.fail_count,
                "last_reviewed_at": stats.last_reviewed_at,
                "last_result": stats.last_result,
                "title": stats.title,
                "primary_motif": stats.primary_motif
            })
        else:
            # Default values for new puzzles
            p_dict.update({
                "next_due_at": None,
                "interval_days": None,
                "ease_factor": 2.0,
                "attempts": 0,
                "pass_count": 0,
                "fail_count": 0,
                "last_reviewed_at": None,
                "last_result": None,
                "title": None,
                "primary_motif": None
            })
        result_puzzles.append(p_dict)
        
    # 4. Total due count for metadata
    # 4. Total due count for metadata
    # We can calculate this from all_stats since it contains all stats for the user's puzzles
    now = datetime.now(timezone.utc)
    due_count = sum(
        1 for s in all_stats.values() 
        if s.next_due_at and s.next_due_at.replace(tzinfo=timezone.utc) <= now
    )
    
    return {
        "due_count": due_count,
        "returned_count": len(result_puzzles),
        "now": now,
        "puzzles": result_puzzles
    }


@app.post("/puzzles/{puzzle_id}/review")
async def review_puzzle(
    puzzle_id: str,
    request: ReviewRequest,
    db: Session = Depends(get_db)
):
    """
    Record a puzzle review and update scheduling.
    
    Optionally tracks the review in a training session.
    """
    puzzle_storage = get_puzzle_storage()
    if not puzzle_storage.get_puzzle(request.username, puzzle_id):
        raise HTTPException(status_code=404, detail="Puzzle not found")
    
    # If session_id provided, validate session and update counters
    if request.session_id:
        from services.api.models import TrainingSession, PuzzleResult as PR
        
        stmt = select(TrainingSession).where(TrainingSession.id == request.session_id)
        session = db.scalars(stmt).first()
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        if session.username != request.username:
            raise HTTPException(status_code=403, detail="Session belongs to different user")
        
        if session.completed_at is not None:
            raise HTTPException(status_code=400, detail="Session already completed")
        
        # Increment session counters (will be committed with review)
        if request.result == PR.PASS:
            session.pass_count += 1
        else:
            session.fail_count += 1
        
        # Add time if provided
        if request.time_spent_ms:
            session.total_time_ms += request.time_spent_ms
        
    # 1. Record individual review (with optional session_id)
    insert_puzzle_review(
        db, 
        puzzle_id, 
        request.username, 
        request.result, 
        request.time_spent_ms,
        session_id=request.session_id
    )
    
    # 2. Update aggregate stats (triggers scheduling logic)
    stats = update_puzzle_stats(db, puzzle_id, request.username, request.result)
    
    # 3. Commit all changes atomically
    db.commit()
    
    return {
        "next_due_at": stats.next_due_at,
        "interval_days": stats.interval_days,
        "ease_factor": stats.ease_factor,
        "stats": {
            "attempts": stats.attempts,
            "pass_count": stats.pass_count,
            "fail_count": stats.fail_count,
            "last_reviewed_at": stats.last_reviewed_at,
            "last_result": stats.last_result
        }
    }


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


class SnapshotRequest(BaseModel):
    username: str
    time_control: Literal["rapid", "blitz", "bullet"]


class SnapshotResponse(BaseModel):
    rating: int
    recorded_at: datetime


@app.post("/ratings/snapshot", response_model=SnapshotResponse)
async def create_rating_snapshot(request: SnapshotRequest, db: Session = Depends(get_db)):
    """Fetch current rating from Chess.com and store a snapshot."""
    try:
        stats = await get_player_stats(request.username)
        
        # Parse rating: { "chess_rapid": { "last": { "rating": ... } } }
        tc_key = f"chess_{request.time_control}"
        if not (rating := stats.get(tc_key, {}).get("last", {}).get("rating")):
             raise HTTPException(status_code=502, detail=f"Could not find rating for {request.time_control} in Chess.com response")
        
        rating = stats[tc_key]["last"]["rating"]
        
        snapshot = RatingSnapshot(
            username=request.username,
            source="chesscom",
            time_control=request.time_control,
            rating=rating,
            recorded_at=datetime.now(timezone.utc)
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        
        return SnapshotResponse(rating=snapshot.rating, recorded_at=snapshot.recorded_at)
        
    except (UserNotFoundError, NetworkError) as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))


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
    expected_minus_actual: float | None
    missing_opponent_rating_games: int


class ExplainResponse(BaseModel):
    time_control: str
    window: RatingWindow
    rating: RatingInfo
    stats: DriverStats
    drivers: list[str]
    highlights: Highlights


@app.get("/ratings/explain", response_model=ExplainResponse)
async def explain_rating_changes(
    username: str,
    time_control: str = "rapid",
    since_session_id: str | None = None,
    since: datetime | None = None,
    limit_games: int = 200,
    db: Session = Depends(get_db)
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
        stmt = select(TrainingSession).where(TrainingSession.username == username).order_by(TrainingSession.created_at.desc())
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
    storage = get_storage()
    all_metadata = storage.get_all_metadata(username)
    
    start_ts = int(window_start.timestamp())
    relevant_games = []
    
    count = 0
    for meta in all_metadata:
        if count >= limit_games:
            break
        if meta.time_control.lower() != time_control.lower():
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
    
    stmt = select(RatingSnapshot).where(
        RatingSnapshot.username == username, 
        RatingSnapshot.time_control == time_control,
        RatingSnapshot.recorded_at >= window_start
    ).order_by(RatingSnapshot.recorded_at.asc())
    earliest_snapshot = db.scalars(stmt).first()
    
    stmt = select(RatingSnapshot).where(
         RatingSnapshot.username == username, 
         RatingSnapshot.time_control == time_control,
         RatingSnapshot.recorded_at < now
    ).order_by(RatingSnapshot.recorded_at.desc())
    latest_snapshot = db.scalars(stmt).first()

    reference_rating = 0
    reference_is_approx = False
    
    if earliest_snapshot:
        reference_rating = earliest_snapshot.rating
    elif latest_snapshot:
        reference_rating = latest_snapshot.rating
    
    for meta in relevant_games:
        pgn = storage.get_pgn(username, meta.game_id)
        if not pgn:
             continue
             
        user_is_white = (meta.white_username.lower() == username.lower())
        
        result_score = 0.0
        if user_is_white:
             if meta.white_result == "win": result_score = 1.0
             elif meta.white_result in ["repetition", "agreed", "timevsinsufficient", "stalemate", "insufficient"]: result_score = 0.5
        else:
             if meta.black_result == "win": result_score = 1.0
             elif meta.black_result in ["repetition", "agreed", "timevsinsufficient", "stalemate", "insufficient"]: result_score = 0.5

        if result_score == 1.0: wins += 1
        elif result_score == 0.5: draws += 1
        else: losses += 1
        
        opp_rating = get_opponent_rating_from_pgn(pgn, user_is_white)
        
        if opp_rating is None:
            missing_ratings += 1
            game_details.append({
                "meta": meta,
                "opp_rating": None,
                "actual": result_score,
                "expected": None
            })
        else:
            total_opp_rating += opp_rating
            opp_rating_count += 1
            opp_ratings.append(opp_rating)
            game_details.append({
                "meta": meta,
                "opp_rating": opp_rating,
                "actual": result_score,
                "expected": None 
            })

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
            expected = 1 / (1 + 10 ** ((r_opp - reference_rating) / 400))
            item["expected"] = expected
            
            expected_total += expected
            actual_total_rated += item["actual"]
            
            surprises.append(HighlightGame(
                opponent_rating=r_opp,
                result="Win" if item["actual"]==1.0 else "Draw" if item["actual"]==0.5 else "Loss",
                expected_score=round(expected, 2),
                rating_diff=r_opp - reference_rating,
                game_id=item["meta"].game_id,
                played_at=datetime.fromtimestamp(item["meta"].end_time, tz=timezone.utc),
                url=item["meta"].url
            ))
            
    avg_opp = int(total_opp_rating / opp_rating_count) if opp_rating_count > 0 else None
    
    drivers = []
    diff = actual_total_rated - expected_total
    
    if diff > 0.5:
        drivers.append("You outperformed expectations overall (upward pressure).")
    elif diff < -0.5:
        drivers.append("You underperformed expectations overall (downward pressure).")
        
    wins_vs_higher = sum(1 for g in game_details if g["opp_rating"] and g["opp_rating"] >= reference_rating + 100 and g["actual"] == 1.0)
    if wins_vs_higher >= 2:
        drivers.append("Wins against higher-rated opponents likely offset losses.")
        
    losses_vs_lower = sum(1 for g in game_details if g["opp_rating"] and g["opp_rating"] <= reference_rating - 100 and g["actual"] == 0.0)
    if losses_vs_lower >= 2:
        drivers.append("Losses against lower-rated opponents likely drove most of the drop.")
        
    if len(opp_ratings) >= 5:
        variance = sum((x - avg_opp) ** 2 for x in opp_ratings) / len(opp_ratings)
        std_dev = variance ** 0.5
        if std_dev > 150:
            drivers.append("Wide opponent rating range increased volatility.")

    def get_surprise_val(h: HighlightGame):
        act = 1.0 if h.result == "Win" else 0.5 if h.result == "Draw" else 0.0
        return act - h.expected_score
        
    surprises.sort(key=get_surprise_val, reverse=True)
    best_surprises = surprises[:3]
    worst_surprises = surprises[-3:]
    worst_surprises.reverse()
    
    start_rating_val = earliest_snapshot.rating if earliest_snapshot else None
    
    stmt = select(RatingSnapshot).where(
        RatingSnapshot.username == username,
        RatingSnapshot.time_control == time_control
    ).order_by(RatingSnapshot.recorded_at.desc())
    latest_any = db.scalars(stmt).first()
    end_rating_val = latest_any.rating if latest_any else None
    
    net_change = None
    if start_rating_val and end_rating_val:
        net_change = end_rating_val - start_rating_val

    return ExplainResponse(
        time_control=time_control,
        window=RatingWindow(start=window_start, end=now, source=source_type),
        rating=RatingInfo(
            start=start_rating_val,
            end=end_rating_val,
            net_change=net_change,
            reference_rating=reference_rating,
            reference_is_approx=reference_is_approx
        ),
        stats=DriverStats(
            games=len(relevant_games),
            wins=wins,
            draws=draws,
            losses=losses,
            avg_opponent_rating=avg_opp,
            expected_total=expected_total if opp_rating_count > 0 else None,
            actual_total=actual_total_rated if opp_rating_count > 0 else None,
            expected_minus_actual=diff if opp_rating_count > 0 else None,
            missing_opponent_rating_games=missing_ratings
        ),
        drivers=drivers,
        highlights=Highlights(best_surprises=best_surprises, worst_surprises=worst_surprises)
    )
