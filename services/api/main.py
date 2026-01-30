from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError

import os
import sys

# Add project root to path to verify imports work even if CWD is services/api
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.api.db import SessionLocal
from services.api.models import Job, JobStatus
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
from services.ingest import (
    ImportError as ChessComImportError,
)
from services.ingest import (
    NetworkError,
    RateLimitError,
    UserNotFoundError,
    fetch_games_from_archive,
    get_player_archives,
    parse_game,
    import_all_games,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prevent worker startup in tests or if explicitly disabled
    if os.environ.get("KNIGHTMIND_WORKER_DISABLED") != "true":
        worker.start()
    yield
    await worker.stop()

app = FastAPI(title="KnightMind API", version="0.1.0", lifespan=lifespan)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
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
