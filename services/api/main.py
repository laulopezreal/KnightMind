import logging
import os
import sys
from pathlib import Path

# Load .env before any project imports read os.environ
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import anyio
import chess
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Add project root to path to verify imports work even if CWD is services/api
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import asyncio

from services.api.analytics_confidence import (
    MIN_GAMES_FOR_RATING_DRIVERS,
    rating_confidence,
)
from services.api.auth import require_operator
from services.api.db import SessionLocal, get_db
from services.api.diagnosis.job import DIAGNOSIS_BATCH_DEFAULT, DIAGNOSIS_BATCH_MAX
from services.api.engine import (
    EngineNotAvailableError,
    InvalidFenError,
    get_or_compute_eval,
    is_engine_available,
)
from services.api.identity import (
    assert_owns_username,
    claim_username_if_unowned,
    require_account,
)
from services.api.jobs.cleanup_sessions import (
    cleanup_abandoned_sessions,
    purge_expired_ai_audit,
)
from services.api.models import (
    Account,
    Job,
    JobStatus,
    JobType,
    PuzzleResult,
    PuzzleReview,
    PuzzleStats,
    RatingSnapshot,
)
from services.api.models import (
    Game as GameModel,
)
from services.api.models import (
    Puzzle as PuzzleModel,
)
from services.api.motifs import MotifPerformanceResponse, get_user_motif_performance
from services.api.openings import OpeningTreeBuilder
from services.api.openings import make_key as make_openings_cache_key
from services.api.openings import tree_cache as openings_tree_cache
from services.api.puzzles.identity import backfill_puzzle_identity
from services.api.ratelimit import rate_limit
from services.api.ratings_auto import auto_snapshot, auto_snapshot_throttled
from services.api.storage import GameRepository, PuzzleRepository, normalized_position
from services.api.storage.diagnosis_repository import DiagnosisRepository
from services.api.storage.game_repository import MANUAL_GAME_ID
from services.api.storage.spaced_repetition import (
    _utcnow_naive,
    get_adaptive_puzzles,
    get_all_puzzle_stats,
    get_next_due_date,
    get_puzzle_stats,
    get_trainable_puzzle_count,
    get_trainable_puzzle_ids,
    insert_puzzle_review,
    update_puzzle_stats,
)
from services.api.time_control import classify_time_control
from services.api.usernames import Username, canonical_username
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

# Per-principal rate limits (audit gate 10). Defaults are per 60s window and can
# be overridden per route via RATE_LIMIT_<NAME>[ _WINDOW] env vars (0 disables).
# See services/api/ratelimit.py for the algorithm and the multi-worker caveat.
RATE_LIMIT_ENGINE_EVAL = 30  # Stockfish CPU; also has a per-process in-flight cap
RATE_LIMIT_IMPORT_CHESSCOM = 5  # heavy Chess.com fetch + bulk DB writes
RATE_LIMIT_PUZZLES_GENERATE = 5  # enqueues a heavy analysis job
RATE_LIMIT_RATINGS_SNAPSHOT = 10  # outbound Chess.com call per request
RATE_LIMIT_DIAGNOSE = 5  # enqueues a whole-corpus analysis job

# A FEN is bounded in length (piece placement + 5 short fields); anything much
# longer than a legal position is junk. Reject oversized input with 400 before
# it reaches the engine, so a caller can't ship a giant body to /engine/eval.
MAX_FEN_LENGTH = 120

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
    """Background task for periodic housekeeping.

    Session cleanup and AI-audit retention run in separate try blocks on
    purpose: a failure in one must not skip the other, and a stalled retention
    sweep would let prompt/response blobs accumulate past their window
    silently.
    """
    while True:
        try:
            # Run cleanup
            with SessionLocal() as db:
                await asyncio.to_thread(cleanup_abandoned_sessions, db)
        except Exception as e:
            print(f"Error in session cleanup: {e}")

        try:
            with SessionLocal() as db:
                await asyncio.to_thread(purge_expired_ai_audit, db)
        except Exception as e:
            print(f"Error in AI audit purge: {e}")

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

logger = logging.getLogger("knightmind.api")

from services.api.ops import router as ops_router

app.include_router(ops_router)

from services.api.sessions import router as sessions_router

app.include_router(sessions_router)

from services.api.dashboard import router as dashboard_router

app.include_router(dashboard_router)

from services.api.auth_routes import router as auth_router

app.include_router(auth_router)


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
async def get_user_status(
    username: Username,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Get training status for a user to support empty states."""
    assert_owns_username(account, username, db)
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

    # Use efficient count queries. `due_count` is the *trainable* count — due
    # plus never-reviewed — because that is what the Train page gates on. The
    # strict due-only count used to report 0 for a user whose puzzles had all
    # just been generated but who also had older scheduled puzzles, disabling
    # "Start Session" on a pile of untouched puzzles. (The previous
    # `total_stats == 0` special case only covered the all-or-nothing case.)
    due_count = 0
    next_due_at = None
    if puzzles_count > 0:
        due_count = get_trainable_puzzle_count(db, username)
        next_due_at = get_next_due_date(db, username)

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
async def get_motif_performance(
    username: Username,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Get user's performance breakdown across all chess tactical patterns/motifs."""
    assert_owns_username(account, username, db)
    return get_user_motif_performance(db, username)


@app.get("/users/validate")
async def validate_user(username: str):
    """
    Validate if a user exists on Chess.com.
    Proxies the request to avoid CORS issues and expose internal APIs.
    """
    # This is the Chess.com existence proxy: the upstream lookup uses the raw
    # (stripped) handle, but the value we hand back is canonical so the caller
    # stores the same key every other endpoint keys on.
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

    return {
        "valid": True,
        "username": profile.get("username") or canonical_username(username),
    }


@app.post(
    "/import/chesscom",
    response_model=ImportResponse,
    dependencies=[
        Depends(rate_limit("import_chesscom", default_limit=RATE_LIMIT_IMPORT_CHESSCOM))
    ],
)
async def import_chesscom_games(
    username: Annotated[Username, Query(max_length=64)],
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Import games from Chess.com for a specific user.
    """
    # First-importer-wins: claim the handle for this account if unowned, else
    # 403 if another account already owns it. No-op when auth is disabled.
    claim_username_if_unowned(account, username, db)
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

        # Incremental sync: fetch only monthly archives that could contain new
        # games. Derive the cutoff from the newest stored game's end time (not
        # the last-sync timestamp) so an interrupted prior sync resumes safely.
        # First sync (no stored games) → since=None → full history.
        since = await asyncio.to_thread(game_repository.get_latest_game_time, username)

        batch: list[ChessGame] = []
        async for game in import_all_games(username, since=since):
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

        # Fresh games are on file — record the current ratings alongside them
        # so rating history never depends on a manual snapshot (best-effort).
        # A sync that found nothing new can't have moved the rating, so skip
        # the Chess.com round-trip entirely on no-op re-imports.
        if new_games > 0:
            await auto_snapshot(username, db)

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
    except HTTPException:
        raise
    except Exception as e:
        # Log the real error server-side; return a generic message so raw
        # exception/DB text never reaches the caller (dim 23).
        logger.exception("Unexpected error importing Chess.com games")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.get("/import/status", response_model=ImportStatusResponse)
async def get_import_status(
    username: Username,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Get the last import summary for a user."""
    assert_owns_username(account, username, db)
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
    # None when the position is terminal (checkmate/stalemate): there is no
    # move to make. Clients should branch on is_terminal.
    best_move_uci: str | None
    eval: float  # In pawns, from side-to-move perspective
    mate_in: int | None = None  # Signed distance to mate, None for cp evals
    is_terminal: bool = False  # Position is game-over (no best move)


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
    username: Username
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
    username: str
    fen: str
    title: str
    motif: str
    source: str | None = None
    solution_pv: str | None = None


class ManualPuzzleResponse(BaseModel):
    puzzle_id: str
    is_new: bool


@app.get("/")
async def root():
    return {"message": "KnightMind API", "version": "0.1.0"}


@app.get("/openings")
async def get_openings(
    username: Annotated[
        Username, Query(description="Username to build opening tree for")
    ],
    color: Literal["white", "black", "both"] = Query(
        "both", description="Filter by player's color"
    ),
    max_ply: int = Query(
        12, ge=1, le=40, description="Maximum number of half-moves to include"
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Get the opening tree for a user's games.

    Builds a tree structure from the user's stored PGN games showing:
    - move_san: The move in Standard Algebraic Notation
    - ply: Half-move number (1 = white's first, 2 = black's first, etc.)
    - games_count: Number of games reaching this position
    - wins/draws/losses: Results from the player's perspective
    - win_rate: Score percentage (wins + 0.5*draws) / games
    - children: Subsequent moves played from this position
    - analysis: How many stored games actually reached the tree, and why the
      rest didn't (colour filter vs. unreadable/not-the-player/unfinished)

    Args:
        username: The username to build the tree for (must have imported games)
        color: Filter games by the player's color ("white", "black", or "both")
        max_ply: Maximum depth in half-moves (default 12 = 6 full moves each side)

    Returns:
        Opening tree as nested JSON structure
    """
    assert_owns_username(account, username, db)
    game_repository = GameRepository(db)

    # Check if user has any games
    game_count = game_repository.get_game_count(username)
    if game_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No games found for user '{username}'. Import games first using POST /import/chesscom",
        )

    # Rebuilding re-parses every stored PGN, and the client refetches on mount
    # and on every colour-filter change. The key folds in the game count and the
    # newest game's timestamp, so an import invalidates this by construction.
    cache_key = make_openings_cache_key(
        username=username,
        color=color,
        max_ply=max_ply,
        game_count=game_count,
        latest_game_time=game_repository.get_latest_game_time(username),
    )
    cached = openings_tree_cache.get(cache_key)
    if cached is not None:
        return cached

    # Stream all PGNs for the user in bulk batches (one query per batch)
    # instead of one query per game, without holding every blob in memory.
    metadata_list = game_repository.get_all_metadata(username)
    game_ids = [meta.game_id for meta in metadata_list]
    pgn_count = 0

    # Build the opening tree. The builder (rather than the build_opening_tree
    # convenience wrapper) is used directly so its per-game report survives.
    builder = OpeningTreeBuilder(max_ply=max_ply)
    for pgn in game_repository.iter_pgns(username, game_ids):
        pgn_count += 1
        builder.add_game(pgn, username, color)

    if metadata_list and pgn_count == 0:
        raise HTTPException(
            status_code=503,
            detail="Games found but PGN content is missing. Re-import games to populate PGN data.",
        )

    tree = builder.build_tree()
    # Attached to the root node rather than wrapping the response so existing
    # clients keep reading the tree at the top level. `games_stored` comes from
    # the repository, so a tree built from a fraction of a user's games is
    # reportable instead of silently looking complete.
    tree["analysis"] = {"games_stored": game_count, **builder.report.to_dict()}
    # Stored only once the response is fully composed: the cache hands values
    # back by reference, so anything mutated after this point would corrupt
    # every later hit.
    openings_tree_cache.put(cache_key, tree)
    return tree


@app.get("/engine/status", response_model=EngineStatusResponse)
async def get_engine_status():
    """Check if the Stockfish engine is available."""
    available, message = is_engine_available()
    return EngineStatusResponse(available=available, message=message)


# /engine/eval is unauthenticated, so bound how many evaluations may be in
# flight per process. Excess requests are rejected with 429 rather than queued,
# so a caller cannot pile up unbounded Stockfish work. NOTE: this is a
# per-process guard only; it is NOT a substitute for auth / per-client rate
# limiting at the ingress, which must still be added to prevent abuse.
_ENGINE_EVAL_MAX_INFLIGHT = int(os.environ.get("ENGINE_EVAL_MAX_CONCURRENCY", "4"))
_engine_eval_inflight = 0
_engine_eval_lock = asyncio.Lock()


@app.post(
    "/engine/eval",
    response_model=EvalResponse,
    dependencies=[
        Depends(rate_limit("engine_eval", default_limit=RATE_LIMIT_ENGINE_EVAL))
    ],
)
async def evaluate_fen(
    request: EvalRequest,
    account: Account | None = Depends(require_account),
):
    """Evaluate a chess position using Stockfish with caching.

    Gated behind an authenticated account (when auth is enabled) purely to keep
    unauthenticated callers from spending Stockfish CPU. No per-user data.
    """
    # Size cap: reject an oversized FEN before touching the engine or the
    # in-flight guard, so a caller can't force expensive parsing with junk.
    if len(request.fen) > MAX_FEN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"FEN too long (max {MAX_FEN_LENGTH} characters)",
        )

    global _engine_eval_inflight
    async with _engine_eval_lock:
        if _engine_eval_inflight >= _ENGINE_EVAL_MAX_INFLIGHT:
            raise HTTPException(
                status_code=429,
                detail="Engine evaluation capacity reached; retry later.",
            )
        _engine_eval_inflight += 1

    try:
        result = await asyncio.to_thread(get_or_compute_eval, request.fen)
        return EvalResponse(
            best_move_uci=result.best_move_uci,
            eval=result.eval,
            mate_in=result.mate_in,
            is_terminal=result.is_terminal,
        )
    except EngineNotAvailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except InvalidFenError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        async with _engine_eval_lock:
            _engine_eval_inflight -= 1


@app.post(
    "/puzzles/generate",
    response_model=JobStatusResponse,
    dependencies=[
        Depends(
            rate_limit("puzzles_generate", default_limit=RATE_LIMIT_PUZZLES_GENERATE)
        )
    ],
)
async def generate_puzzles_endpoint(
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


@app.post("/puzzles/manual", response_model=ManualPuzzleResponse)
async def create_manual_puzzle(
    request: ManualPuzzleRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Create a puzzle from an arbitrary position (Engine Analysis → Save as puzzle)."""
    assert_owns_username(account, request.username, db)
    username_lower = request.username.lower()

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


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Get status of a specific job."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Object-level ownership: 404 (not 403) so foreign job ids aren't confirmed.
    assert_owns_username(account, job.username, db, status_code=404)

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        message=job.message,
        progress=job.progress_current,
        result=job.result_json,
        error=job.error_message,
    )


@app.post("/jobs/{job_id}/cancel", response_model=JobStatusResponse)
async def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Cancel a running or queued job."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Object-level ownership: 404 (not 403) so foreign job ids aren't confirmed.
    assert_owns_username(account, job.username, db, status_code=404)

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
        # SCORED training path (post-generation warm-up): strip the solution so
        # it can't be pre-read before an attempt (audit gate 13).
        puzzles_dict.append(_strip_solution(p_dict))

    return DailyPuzzlesResponse(puzzles=puzzles_dict, count=len(puzzles_dict))


@app.get("/puzzles/due", response_model=DuePuzzlesResponse)
async def get_due_puzzles_endpoint(
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
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """
    Get puzzles due for review, followed by new puzzles.
    Supports adaptive selection based on session type and target accuracy.
    Optionally filter by specific chess motif.
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

    # 4. Get prioritized IDs and their stats using adaptive selection
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


@app.get("/puzzles/list", response_model=PuzzleListResponse)
async def list_puzzles(
    username: Annotated[Username, Query(description="Username to list puzzles for")],
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
    return PuzzleListItem(
        id=puzzle.id,
        title=stats.title if stats else None,
        primary_motif=stats.primary_motif if stats else None,
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
    )


# ---------------------------------------------------------------------------
# Mistake diagnosis
# ---------------------------------------------------------------------------


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

    No cause is ever invented to avoid ``unclear``. There is deliberately no
    numeric confidence: the rule strength is an ordering prior, not a
    calibrated probability, and rendering it as a percentage would overstate
    what the rules know.
    """

    state: Literal["ready", "unclear", "pending", "unavailable"]
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
    source: str | None = None
    diagnosed_at: datetime | None = None


class DiagnosisConfirmRequest(BaseModel):
    cause: str


class PendingDiagnosisResponse(BaseModel):
    username: str
    pending: int


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
    from services.api.diagnosis.causes import CAUSE_LABELS
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
    return DiagnosisResponse(
        state="unclear" if unclear else "ready",
        puzzle_id=puzzle_id,
        primary_motif=row.primary_motif,
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
        source=row.source,
        diagnosed_at=row.updated_at,
    )


@app.get("/puzzles/{puzzle_id}/diagnosis", response_model=DiagnosisResponse)
async def get_puzzle_diagnosis(
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
    return _diagnosis_response(
        puzzle_id, DiagnosisRepository(db).get(username, puzzle_id), reveal_solution
    )


@app.get("/users/{username}/diagnosis/pending", response_model=PendingDiagnosisResponse)
async def get_pending_diagnoses(
    username: Username,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """How many puzzles still need diagnosing — drives the backfill CTA."""
    assert_owns_username(account, username, db)
    return PendingDiagnosisResponse(
        username=username, pending=DiagnosisRepository(db).pending_count(username)
    )


@app.post(
    "/users/{username}/diagnose",
    response_model=JobStatusResponse,
    dependencies=[Depends(rate_limit("diagnose", default_limit=RATE_LIMIT_DIAGNOSE))],
)
async def diagnose_puzzles_endpoint(
    username: Username,
    limit: int = Query(
        DIAGNOSIS_BATCH_DEFAULT,
        ge=1,
        le=DIAGNOSIS_BATCH_MAX,
        description="Maximum puzzles to analyse in this run",
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Queue a diagnosis run over the puzzles that still need one.

    Scoped to its own job type, so it can run alongside puzzle generation --
    that is what the (username, type) active-job index exists for. A duplicate
    request returns the in-flight job rather than erroring, mirroring
    /puzzles/generate.
    """
    assert_owns_username(account, username, db)
    try:
        job = Job(
            username=username,
            type=JobType.DIAGNOSIS,
            status=JobStatus.QUEUED,
            message="Queued for diagnosis",
            params={"limit": limit},
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return JobStatusResponse(
            job_id=job.id, status=job.status, message="Job queued", progress=0
        )
    except IntegrityError as e:
        db.rollback()
        existing = db.scalars(
            select(Job).where(
                Job.username == username,
                Job.type == JobType.DIAGNOSIS,
                or_(Job.status == JobStatus.QUEUED, Job.status == JobStatus.RUNNING),
            )
        ).first()
        if existing:
            return JobStatusResponse(
                job_id=existing.id,
                status=existing.status,
                message="Diagnosis already in progress",
                progress=existing.progress_current,
            )
        raise HTTPException(
            status_code=500, detail="Could not create diagnosis job"
        ) from e


@app.post("/puzzles/{puzzle_id}/diagnosis/confirm", response_model=DiagnosisResponse)
async def confirm_puzzle_diagnosis(
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
    from services.api.diagnosis.causes import CAUSE_LABELS

    assert_owns_username(account, username, db)

    if payload.cause not in CAUSE_LABELS:
        raise HTTPException(status_code=422, detail="Unknown cause")

    repo = DiagnosisRepository(db)
    row = repo.confirm_cause(username, puzzle_id, payload.cause)
    if row is None:
        raise HTTPException(status_code=404, detail="No diagnosis for this puzzle")
    db.commit()
    # Same gate as the read: this returns the identical body, evidence and all.
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
    stats, puzzle_stats, result, verified: bool = False, source: str | None = None
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
        # Server-decided outcome and whether it was independently verified.
        "result": result_val,
        "verified": verified,
        "source": source,
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
        )

        # 2. Update aggregate stats (triggers scheduling logic)
        stats = update_puzzle_stats(db, puzzle_id, request.username, effective_result)

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
    )


@app.post("/puzzles/{puzzle_id}/check", response_model=CheckResponse)
async def check_puzzle(
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


@app.post("/puzzles/{puzzle_id}/reveal", response_model=RevealResponse)
async def reveal_puzzle(
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


# --- Rating Drivers Explainer Support ---


def _rating_from_pgn(pgn: str, tag: str) -> int | None:
    """Extract a numeric Elo header (WhiteElo/BlackElo) from PGN headers."""
    match = re.search(f'\\[{tag} "(\\d+)"\\]', pgn)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def get_opponent_rating_from_pgn(pgn: str, user_is_white: bool) -> int | None:
    """Extract opponent rating from PGN headers."""
    return _rating_from_pgn(pgn, "BlackElo" if user_is_white else "WhiteElo")


def get_player_rating_from_pgn(pgn: str, user_is_white: bool) -> int | None:
    """Extract the player's own rating from PGN headers.

    Chess.com writes each side's post-game rating into the Elo headers, so
    across a window of games these values form the player's actual rating
    trajectory — no manual snapshots required.
    """
    return _rating_from_pgn(pgn, "WhiteElo" if user_is_white else "BlackElo")


def calculate_expected_score(player_rating: int, opponent_rating: int) -> float:
    """Calculates the expected score for a player based on Elo ratings."""
    return 1 / (1 + 10 ** ((opponent_rating - player_rating) / 400))


class SnapshotRequest(BaseModel):
    username: Username
    time_control: Literal["rapid", "blitz", "bullet"]


class SnapshotResponse(BaseModel):
    rating: int
    recorded_at: datetime


@app.post(
    "/ratings/snapshot",
    response_model=SnapshotResponse,
    dependencies=[
        Depends(
            rate_limit("ratings_snapshot", default_limit=RATE_LIMIT_RATINGS_SNAPSHOT)
        )
    ],
)
async def create_rating_snapshot(
    request: SnapshotRequest,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Fetch current rating from Chess.com and store a snapshot."""
    assert_owns_username(account, request.username, db)
    try:
        stats = await get_player_stats(request.username)

        # Parse rating: { "chess_rapid": { "last": { "rating": ... } } }
        tc_key = f"chess_{request.time_control}"
        if not (rating := stats.get(tc_key, {}).get("last", {}).get("rating")):
            raise HTTPException(
                status_code=502,
                detail=f"Could not find rating for {request.time_control} in Chess.com response",
            )

        # Same-rating dedupe, mirroring ratings_auto.auto_snapshot: a repeat
        # call with an unchanged rating answers from the stored row instead of
        # writing a duplicate flat entry into the history the chart reads.
        latest_stmt = (
            select(RatingSnapshot)
            .where(
                RatingSnapshot.username == request.username,
                RatingSnapshot.time_control == request.time_control,
            )
            .order_by(RatingSnapshot.recorded_at.desc())
            .limit(1)
        )
        latest = db.scalars(latest_stmt).first()
        if latest and latest.rating == rating:
            return SnapshotResponse(
                rating=latest.rating, recorded_at=latest.recorded_at
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
    except HTTPException:
        # Domain-specific HTTPExceptions (e.g. the 502 above) are already safe.
        raise
    except Exception as e:
        # Unexpected failure: log the real error server-side but return a generic
        # message so raw exception/DB text (connection strings, SQL, etc.) never
        # reaches the caller (dim 23).
        logger.exception("Unexpected error creating rating snapshot")
        raise HTTPException(status_code=500, detail="Internal server error") from e


class SnapshotHistoryItem(BaseModel):
    rating: int
    recorded_at: datetime


@app.get("/ratings/history", response_model=list[SnapshotHistoryItem])
async def get_rating_history(
    username: Username,
    time_control: str = "rapid",
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Return chronological rating snapshot history for charting.

    Fetches the most recent `limit` snapshots (desc) then reverses to
    chronological order so the frontend chart always shows the latest window.
    """
    assert_owns_username(account, username, db)
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
    opponent_username: str | None = None
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
    # True when start/end were estimated from the player's own per-game Elo
    # headers (post-game ratings) rather than recorded snapshots. Kept for
    # older clients; new clients should read the per-anchor flags below.
    is_estimated: bool = False
    start_is_estimated: bool = False
    end_is_estimated: bool = False
    reference_rating: int
    reference_is_approx: bool


class TrajectoryPoint(BaseModel):
    played_at: datetime
    rating: int


class ChartPoint(BaseModel):
    at: datetime
    rating: int
    source: Literal["game", "snapshot"]


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
    # In-window games of this time control skipped because they were casual
    # (unrated) — casual games never move the Chess.com rating, so counting
    # them would corrupt the attribution.
    casual_games_excluded: int = 0


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
    # Player's own rating over the window, from per-game PGN Elo headers
    # (chronological). Lets the frontend chart real rating movement without
    # manual snapshots. Kept for older clients; chart_series supersedes it.
    trajectory: list[TrajectoryPoint] = []
    # Chart-ready fusion of per-game Elo points and the snapshot anchors that
    # won the start/end contests, in time order. Its endpoints always match
    # rating.start/rating.end, so clients must render this instead of picking
    # a source themselves. Empty when the window has no game points (clients
    # fall back to recorded snapshot history).
    chart_series: list[ChartPoint] = []
    # Canonical uncertainty signal (rated games in window). Drivers are
    # descriptive, not causal; below MIN_GAMES_FOR_RATING_DRIVERS no directional
    # driver is emitted and insufficient_data is True.
    confidence: Literal["low", "medium", "high"]
    insufficient_data: bool


@app.get("/ratings/explain", response_model=ExplainResponse)
async def explain_rating_changes(
    username: Username,
    time_control: str = "rapid",
    since_session_id: str | None = None,
    since: datetime | None = None,
    limit_games: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Explain rating drivers based on recent games."""
    from services.api.models import TrainingSession

    assert_owns_username(account, username, db)

    # Viewing insights is itself a reason to refresh the rating record:
    # snapshot opportunistically (throttled per username, best-effort) so the
    # end anchor stays fresh without the user ever pressing a button.
    await auto_snapshot_throttled(username, db)

    # 1. Determine Window
    now = datetime.now(timezone.utc)
    window_start: datetime
    source_type: str

    if since_session_id:
        session = db.get(TrainingSession, since_session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        # Don't leak another tenant's session window via a guessed id: 404.
        assert_owns_username(account, session.username, db, status_code=404)
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
    casual_excluded = 0

    count = 0
    for meta in all_metadata:
        if count >= limit_games:
            break
        if classify_time_control(meta.time_control) != time_control.lower():
            continue
        if meta.end_time < start_ts:
            break
        # Casual games never move the Chess.com rating: excluding them keeps
        # wins/losses, expected-score totals, and drivers about rated play only.
        if not meta.rated:
            casual_excluded += 1
            continue

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
        player_rating = get_player_rating_from_pgn(pgn, user_is_white)

        if opp_rating is None:
            missing_ratings += 1
        else:
            total_opp_rating += opp_rating
            opp_rating_count += 1
            opp_ratings.append(opp_rating)

        game_details.append(
            {
                "meta": meta,
                "opp_rating": opp_rating,
                # Player's own post-game Elo from the PGN — the most accurate
                # per-game reference for expected-score math.
                "player_rating": player_rating,
                "opponent_username": (
                    meta.black_username if user_is_white else meta.white_username
                ),
                "actual": result_score,
            }
        )

    player_ratings = [
        g["player_rating"] for g in game_details if g["player_rating"] is not None
    ]

    if reference_rating == 0:
        if player_ratings:
            # The player's own Elo headers beat any proxy. Averaging opponent
            # ratings instead would force expected scores toward 0.5 by
            # construction (you are your opponents' average only by accident).
            reference_rating = int(sum(player_ratings) / len(player_ratings))
        elif opp_rating_count > 0:
            reference_rating = int(total_opp_rating / opp_rating_count)
        else:
            reference_rating = 1200
        reference_is_approx = True

    # One owner for the per-game self-rating fallback: the player's own Elo at
    # that game when the PGN has it, else the window reference. Used by the
    # expected-score math and both vs-higher/vs-lower driver counts.
    for g in game_details:
        g["r_self"] = g["player_rating"] or reference_rating

    expected_total = 0.0
    actual_total_rated = 0.0
    # (surprise value, game) pairs — ranked on the unrounded value so ties
    # aren't manufactured by the 2-decimal display rounding.
    surprises: list[tuple[float, HighlightGame]] = []

    for item in game_details:
        if item["opp_rating"] is not None:
            r_opp = item["opp_rating"]
            # Prefer the player's own Elo at that game over the single
            # window-wide reference: expected score then reflects the actual
            # matchup, not an anchor that may be days stale.
            r_self = item["r_self"]
            expected = calculate_expected_score(r_self, r_opp)

            expected_total += expected
            actual_total_rated += item["actual"]

            surprises.append(
                (
                    item["actual"] - expected,
                    HighlightGame(
                        opponent_rating=r_opp,
                        opponent_username=item["opponent_username"],
                        result=(
                            "Win"
                            if item["actual"] == 1.0
                            else "Draw" if item["actual"] == 0.5 else "Loss"
                        ),
                        expected_score=round(expected, 2),
                        rating_diff=r_opp - r_self,
                        game_id=item["meta"].game_id,
                        played_at=datetime.fromtimestamp(
                            item["meta"].end_time, tz=timezone.utc
                        ),
                        url=item["meta"].url,
                    ),
                )
            )

    avg_opp = int(total_opp_rating / opp_rating_count) if opp_rating_count > 0 else None

    # Canonical uncertainty signal from the rated-game sample. Below the driver
    # threshold we must not present a small delta as a confident, causal trend.
    confidence = rating_confidence(opp_rating_count)
    insufficient_data = opp_rating_count < MIN_GAMES_FOR_RATING_DRIVERS

    drivers: list[Driver] = []
    diff = actual_total_rated - expected_total

    if insufficient_data:
        # Too few rated games to attribute drivers; stay descriptive + neutral.
        if opp_rating_count > 0:
            drivers.append(
                Driver(
                    text=(
                        f"Only {opp_rating_count} rated "
                        f"{time_control.lower()} game"
                        f"{'s' if opp_rating_count != 1 else ''} in this window — "
                        "not enough to explain rating changes confidently."
                    ),
                    severity="minor",
                    direction="neutral",
                )
            )
    elif diff > PERFORMANCE_DIFF_THRESHOLD:
        severity = (
            "major" if abs(diff) > 2.0 else "moderate" if abs(diff) > 1.0 else "minor"
        )
        drivers.append(
            Driver(
                text=(
                    f"You outperformed expectations by {diff:+.1f} points "
                    f"over {opp_rating_count} rated games (upward pressure)."
                ),
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
                text=(
                    f"You underperformed expectations by {diff:+.1f} points "
                    f"over {opp_rating_count} rated games (downward pressure)."
                ),
                severity=severity,
                direction="down",
            )
        )

    # Attribution drivers below require a sufficient rated sample.
    if not insufficient_data:
        wins_vs_higher = sum(
            1
            for g in game_details
            if g["opp_rating"]
            and g["opp_rating"] >= g["r_self"] + RATING_DIFFERENCE_THRESHOLD
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
            and g["opp_rating"] <= g["r_self"] - RATING_DIFFERENCE_THRESHOLD
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

    surprises.sort(key=lambda pair: pair[0], reverse=True)
    best_surprises = [g for val, g in surprises if val > 0][:3]
    worst_surprises = [g for val, g in surprises if val < 0][-3:]
    worst_surprises.reverse()

    # Player's own rating over the window from PGN Elo headers (game_details is
    # chronological). Powers the chart + start/end estimates below.
    trajectory = [
        TrajectoryPoint(
            played_at=datetime.fromtimestamp(g["meta"].end_time, tz=timezone.utc),
            rating=g["player_rating"],
        )
        for g in game_details
        if g["player_rating"] is not None
    ]

    def _snapshot_at(snapshot: RatingSnapshot) -> datetime:
        at = snapshot.recorded_at
        return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at

    # Start: prefer the pre-window snapshot (always the earliest evidence),
    # then the earliest in-window snapshot — but only if no game finished
    # before it (a snapshot recorded after games began is not the window's
    # starting rating) — then the first game's own Elo (estimated: it's the
    # rating *after* that game, so the first game's delta is not captured).
    start_is_estimated = False
    start_anchor_snapshot = None
    if pre_window_snapshot is not None:
        start_anchor_snapshot = pre_window_snapshot
    elif earliest_snapshot is not None and (
        not trajectory or _snapshot_at(earliest_snapshot) <= trajectory[0].played_at
    ):
        start_anchor_snapshot = earliest_snapshot

    if start_anchor_snapshot is not None:
        start_rating_val = start_anchor_snapshot.rating
    elif trajectory:
        start_rating_val = trajectory[0].rating
        start_is_estimated = True
    else:
        start_rating_val = (
            earliest_snapshot.rating if earliest_snapshot is not None else None
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

    # Use whichever evidence is fresher: an in-window snapshot or the last
    # game's own Elo. A snapshot recorded before the last game is stale.
    end_is_estimated = False
    end_anchor_snapshot = None
    if latest_in_window is not None and (
        not trajectory or _snapshot_at(latest_in_window) >= trajectory[-1].played_at
    ):
        end_rating_val = latest_in_window.rating
        end_anchor_snapshot = latest_in_window
    elif trajectory:
        end_rating_val = trajectory[-1].rating
        end_is_estimated = True
    else:
        end_rating_val = None

    net_change = None
    if start_rating_val is not None and end_rating_val is not None:
        net_change = end_rating_val - start_rating_val

    # Fused, chart-ready series: game points plus the snapshot anchors chosen
    # above, time-ordered. Built from the same anchor decisions as the card so
    # the line's endpoints always equal rating.start/rating.end. Only emitted
    # when the window has game points — with no games there is nothing to
    # fuse, and clients chart their recorded snapshot history instead.
    chart_series: list[ChartPoint] = []
    if trajectory:
        if start_anchor_snapshot is not None:
            chart_series.append(
                ChartPoint(
                    at=_snapshot_at(start_anchor_snapshot),
                    rating=start_anchor_snapshot.rating,
                    source="snapshot",
                )
            )
        chart_series.extend(
            ChartPoint(at=p.played_at, rating=p.rating, source="game")
            for p in trajectory
        )
        # Append even when the same snapshot also won the start contest (a
        # timestamp tie with every game): dropping it would leave the last
        # game's Elo as the series endpoint while the card shows the snapshot
        # rating. A duplicate point at the same timestamp is harmless.
        if end_anchor_snapshot is not None:
            chart_series.append(
                ChartPoint(
                    at=_snapshot_at(end_anchor_snapshot),
                    rating=end_anchor_snapshot.rating,
                    source="snapshot",
                )
            )
        # Stable sort: on equal timestamps the start anchor stays first and
        # the end anchor stays last, preserving endpoint agreement.
        chart_series.sort(key=lambda p: p.at)
        # Self-check the contract clients rely on instead of trusting
        # construction: a divergence here means a card/chart mismatch shipped.
        if chart_series and (
            chart_series[0].rating != start_rating_val
            or chart_series[-1].rating != end_rating_val
        ):
            logger.warning(
                "chart_series endpoints diverge from rating anchors: "
                "series %s..%s vs start=%s end=%s (username=%s tc=%s)",
                chart_series[0].rating,
                chart_series[-1].rating,
                start_rating_val,
                end_rating_val,
                username,
                time_control,
            )

    return ExplainResponse(
        time_control=time_control,
        window=RatingWindow(start=window_start, end=now, source=source_type),
        rating=RatingInfo(
            start=start_rating_val,
            end=end_rating_val,
            net_change=net_change,
            is_estimated=start_is_estimated or end_is_estimated,
            start_is_estimated=start_is_estimated,
            end_is_estimated=end_is_estimated,
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
            casual_games_excluded=casual_excluded,
        ),
        drivers=drivers,
        highlights=Highlights(
            best_surprises=best_surprises, worst_surprises=worst_surprises
        ),
        trajectory=trajectory,
        chart_series=chart_series,
        confidence=confidence,
        insufficient_data=insufficient_data,
    )
