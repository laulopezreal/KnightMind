import logging
import os
import sys
from pathlib import Path

# Load .env before any project imports read os.environ
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Annotated

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Add project root to path to verify imports work even if CWD is services/api
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import asyncio

from services.api.auth import require_operator
from services.api.db import SessionLocal, get_db
from services.api.diagnosis.job import (
    DIAGNOSIS_BATCH_DEFAULT,
    DIAGNOSIS_BATCH_MAX,
    SCOPE_PENDING,
    SCOPE_REENRICH,
)
from services.api.identity import (
    assert_owns_username,
    claim_username_if_unowned,
    require_account,
)
from services.api.jobs.cleanup_sessions import (
    run_session_cleanup,
)
from services.api.models import (
    Account,
    Job,
    JobStatus,
    JobType,
)
from services.api.motifs import MotifPerformanceResponse, get_user_motif_performance
from services.api.openings import warm as warm_eco
from services.api.puzzles.identity import backfill_puzzle_identity
from services.api.ratelimit import rate_limit
from services.api.ratings_auto import auto_snapshot
from services.api.storage import GameRepository, PuzzleRepository
from services.api.storage.diagnosis_repository import DiagnosisRepository
from services.api.storage.spaced_repetition import (
    get_next_due_date,
    get_trainable_puzzle_count,
    get_trainable_puzzle_ids,
)
from services.api.usernames import Username, canonical_username
from services.api.worker import worker
from services.ingest import (
    ChessGame,
    NetworkError,
    RateLimitError,
    UserNotFoundError,
    get_player_profile,
    import_all_games,
)
from services.ingest import (
    ImportError as ChessComImportError,
)

# Commit imported games in batches instead of once per game: a full import can
# span tens of thousands of games, and per-game commits hammer Postgres.
IMPORT_COMMIT_BATCH_SIZE = 200

# Per-principal rate limits (audit gate 10). Defaults are per 60s window and can
# be overridden per route via RATE_LIMIT_<NAME>[ _WINDOW] env vars (0 disables).
# See services/api/ratelimit.py for the algorithm and the multi-worker caveat.
RATE_LIMIT_IMPORT_CHESSCOM = 5  # heavy Chess.com fetch + bulk DB writes
RATE_LIMIT_DIAGNOSE = 5  # enqueues a whole-corpus analysis job


def _worker_runs_elsewhere() -> bool:
    """Whether this process should skip starting the worker and cleanup loop.

    Two different reasons, deliberately separate env vars: DISABLED means there
    is no worker in this deployment at all, EXTERNAL means it runs in its own
    container. /ops/health has to tell them apart -- see the comment there.
    """
    return (
        os.environ.get("KNIGHTMIND_WORKER_DISABLED") == "true"
        or os.environ.get("KNIGHTMIND_WORKER_EXTERNAL") == "true"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prevent worker startup in tests, if explicitly disabled, or when the
    # worker runs as its own service (docker-compose `worker`). Starting it here
    # as well would put Stockfish back on the request path -- the thing
    # separating them was for -- while both processes competed for one queue.
    if not _worker_runs_elsewhere():
        worker.start()

    # Backfill identity (title/motif) for any existing puzzles missing them.
    # Run in a thread to avoid blocking the async event loop.
    def _run_backfill():
        with SessionLocal() as db:
            backfill_puzzle_identity(db)

    await anyio.to_thread.run_sync(_run_backfill)

    # Build the ECO table off the request path. /openings now runs on the
    # threadpool, so a lazy build would no longer stall unrelated requests —
    # but it would still make whichever user arrives first after a deploy wait
    # ~370ms of python-chess replay, and several concurrent first-requests
    # would each pay it (lru_cache dedupes the result, not the work).
    await anyio.to_thread.run_sync(warm_eco)

    # Start session cleanup background task if not disabled
    # Housekeeping runs wherever the worker runs -- one process, whichever it
    # is. Widening this guard to cover EXTERNAL without moving the loop into the
    # worker is how it stopped running at all: the API skipped it because the
    # worker was elsewhere, and the worker never started it. Abandoned sessions
    # accumulated and AI audit rows outlived their retention window, silently.
    cleanup_task = None
    if not _worker_runs_elsewhere():
        cleanup_task = asyncio.create_task(run_session_cleanup())

    yield

    # Cancel cleanup task on shutdown
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    # Symmetric with the guarded start above. stop() now ends by DELETEing this
    # process's heartbeat row, and worker_id defaults to the hostname but is
    # overridable with KNIGHTMIND_WORKER_ID — which .env.docker sets for BOTH
    # services, because they share one env_file. An operator naming the worker
    # per the runbook would therefore have every API restart delete the live
    # worker's beat, and /ops/health would report not_running (503) until the
    # next beat landed. Do not stop a worker this process never started.
    if not _worker_runs_elsewhere():
        await worker.stop()


app = FastAPI(title="KnightMind API", version="0.1.0", lifespan=lifespan)

logger = logging.getLogger("knightmind.api")

from services.api.ops import router as ops_router

app.include_router(ops_router)

from services.api.sessions import focus_practice_candidate_count
from services.api.sessions import router as sessions_router

app.include_router(sessions_router)

from services.api.dashboard import router as dashboard_router

app.include_router(dashboard_router)

from services.api.auth_routes import router as auth_router

app.include_router(auth_router)

from services.api.ratings import router as ratings_router

app.include_router(ratings_router)

from services.api.openings_routes import router as openings_router

app.include_router(openings_router)

from services.api.engine_routes import router as engine_router

app.include_router(engine_router)

from services.api.jobs_routes import JobStatusResponse
from services.api.jobs_routes import router as jobs_router

app.include_router(jobs_router)

from services.api.puzzles_routes import router as puzzles_router

app.include_router(puzzles_router)


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
def get_users(db: Session = Depends(get_db)):
    """Get list of all users who have imported games.

    Operator-only (enumerates every account) — gated to the tailnet. The public
    app only ever looks up a single known username via /users/{username}/...
    """
    game_repository = GameRepository(db)
    users = game_repository.get_users()
    return {"users": users}


@app.get("/users/{username}/status", response_model=UserStatusResponse)
def get_user_status(
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
def get_motif_performance(
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
def get_import_status(
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


@app.get("/")
async def root():
    return {"message": "KnightMind API", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Mistake diagnosis
# ---------------------------------------------------------------------------


class PendingDiagnosisResponse(BaseModel):
    username: str
    pending: int
    # Already diagnosed, but by the rules alone. Non-zero on a deployment that
    # backfilled before ANTHROPIC_API_KEY was set: those rows are not pending
    # (their data versions are current) and no ordinary run will revisit them.
    unenriched: int = 0


class MistakeCause(BaseModel):
    """One cause, with how often it explains this user's mistakes.

    ``mistakes`` is a direct count and always trustworthy — every puzzle in the
    corpus is a real blunder from a real game.

    ``accuracy`` is a proportion over *server-verified* training attempts only,
    and is None until the sample supports it. Self-reported results are
    excluded: the codebase refuses to present them as verified skill, and a
    pass rate is exactly the claim that would launder them.

    ``insufficient_data`` says the cause has not been seen often enough to call
    a tendency. The UI must not rank or recommend against it — below the
    threshold, one bad afternoon looks identical to a habit.
    """

    cause: str
    label: str
    mistakes: int
    dominant_phase: str | None = None
    # The opening this cause concentrates in, when one actually dominates.
    dominant_opening: str | None = None
    verified_attempts: int = 0
    # How many distinct puzzles those attempts covered. Exposed so the UI can
    # say how broad the sample is, not just how large.
    verified_puzzles: int = 0
    accuracy: float | None = None
    insufficient_data: bool = True
    # "Cause unclear" is an honest bucket, not a weakness to train. Flagged so
    # the UI can show it as coverage information without recommending practice.
    is_unclassified: bool = False


class MistakeCausesResponse(BaseModel):
    username: str
    causes: list[MistakeCause]
    total_diagnosed: int
    # Diagnoses still owed. Without it a nearly-empty list is indistinguishable
    # from "you make no mistakes".
    pending: int
    min_for_ranking: int


class MistakePattern(BaseModel):
    """A named, described habit — the coaching layer over a raw cause.

    Only causes that have come up often enough to be a tendency become
    patterns. A cause below the threshold stays a count on the causes endpoint
    and is deliberately absent here: naming something "Loose Piece Syndrome"
    off two occurrences would be the overreach this whole feature avoids.

    ``priority`` orders one person's patterns against each other. It is not a
    probability and means nothing across users.
    """

    cause: str
    name: str
    description: str
    mistakes: int
    recent_mistakes: int
    dominant_phase: str | None = None
    accuracy: float | None = None
    priority: float = 0.0


class MistakePatternsResponse(BaseModel):
    username: str
    patterns: list[MistakePattern]
    # Causes that exist but are not yet tendencies. Reported as a number so the
    # UI can say "and 3 more not seen often enough yet" instead of implying the
    # named list is everything.
    below_threshold: int
    pending: int


@app.get("/users/{username}/mistake-patterns", response_model=MistakePatternsResponse)
def get_mistake_patterns(
    username: Username,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """This user's mistake habits, named and ordered by how much they matter.

    Computed on demand from the diagnoses rather than from a stored clustering.
    At this corpus size the grouping is a millisecond query, so persisting it
    would buy a job, two tables and a staleness window for nothing measurable.
    """
    from services.api.diagnosis.patterns import identify, priority_score

    assert_owns_username(account, username, db)

    repo = DiagnosisRepository(db)
    stats = repo.cause_breakdown(username)

    patterns = []
    below = 0
    for stat in stats:
        identity = identify(stat.cause, stat.dominant_phase)
        # No identity means either "unclassified" or a cause with no written
        # pattern yet. Neither should be invented on the fly.
        if identity is None:
            continue
        if stat.insufficient_data:
            below += 1
            continue
        patterns.append(
            MistakePattern(
                cause=stat.cause,
                name=identity.name,
                description=identity.description,
                mistakes=stat.mistakes,
                recent_mistakes=stat.recent_mistakes,
                dominant_phase=stat.dominant_phase,
                accuracy=stat.accuracy,
                priority=priority_score(
                    stat.mistakes, stat.accuracy, stat.recent_mistakes
                ),
            )
        )

    patterns.sort(key=lambda p: (-p.priority, p.cause))
    return MistakePatternsResponse(
        username=username,
        patterns=patterns,
        below_threshold=below,
        pending=repo.pending_count(username),
    )


class TodaysFocus(BaseModel):
    cause: str
    name: str
    description: str
    mistakes: int
    recent_mistakes: int
    accuracy: float | None = None
    priority: float = 0.0
    # The numbers the choice rests on, so the user can disagree on evidence.
    rationale: str
    runner_up: str | None = None
    # How many puzzles of this cause the user could train *right now*. Counted
    # against the trainable set, not the corpus: "train 8 puzzles" when six of
    # them are scheduled for next week would be a promise the session cannot
    # keep, and training them early would corrupt their intervals anyway.
    trainable_now: int = 0
    # Unlike trainable_now, this includes future-scheduled positions that the
    # dedicated Focus Practice endpoint can safely record without changing
    # their spaced-repetition schedule.
    practice_candidate_count: int = 0
    practice_available: bool = False


class TodaysFocusResponse(BaseModel):
    """The one habit worth working on today, or an honest absence of one.

    ``focus`` is None whenever no pattern has come up often enough to be called
    a tendency. The card renders that as "not yet" rather than falling back to
    whatever happens to be most frequent — a plan built on two occurrences is a
    guess, and this product does not dress guesses as findings.
    """

    username: str
    focus: TodaysFocus | None = None
    below_threshold: int = 0
    pending: int = 0


@app.get("/users/{username}/todays-focus", response_model=TodaysFocusResponse)
def get_todays_focus(
    username: Username,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """What to train today, and why.

    Reads the same cause breakdown the Insights cards use; it computes nothing
    the other surfaces would disagree with.
    """
    from services.api.diagnosis.patterns import identify
    from services.api.diagnosis.planner import plan_focus

    assert_owns_username(account, username, db)

    repo = DiagnosisRepository(db)
    stats = repo.cause_breakdown(username)
    chosen = plan_focus(stats)

    trainable_now = 0
    practice_candidate_count = 0
    if chosen is not None:
        cause_ids = repo.puzzle_ids_for_cause(username, chosen.cause)
        if cause_ids:
            trainable_now = len(
                get_trainable_puzzle_ids(db, username, sorted(cause_ids))
            )
            practice_candidate_count = focus_practice_candidate_count(
                db, username, chosen.cause
            )

    # Counted exactly as the patterns endpoint counts it: a cause with no
    # written pattern — "unclassified" above all — is not a habit awaiting more
    # evidence, so it must not read as "nearly a pattern" here while the card
    # beside it correctly ignores it.
    below = sum(
        1
        for s in stats
        if s.insufficient_data and identify(s.cause, s.dominant_phase) is not None
    )

    return TodaysFocusResponse(
        username=username,
        focus=(
            TodaysFocus(
                **asdict(chosen),
                trainable_now=trainable_now,
                practice_candidate_count=practice_candidate_count,
                practice_available=practice_candidate_count >= 2,
            )
            if chosen
            else None
        ),
        below_threshold=below,
        pending=repo.pending_count(username),
    )


class OpeningPracticeResponse(BaseModel):
    """What practice a given opening line can actually offer.

    Reports both granularities and which one to use, rather than making the
    client choose from counts it would have to interpret. ``scope`` is the
    honest label: "line" when the exact line has enough puzzles to be worth
    drilling, "family" when it does not, "none" when neither does.

    The family is derived server-side from the same split the extraction uses.
    Deriving it in the frontend instead is how the two drift the first time
    that rule changes.
    """

    username: str
    opening_name: str
    opening_family: str
    line_count: int
    family_count: int
    scope: str  # "line" | "family" | "none"


# Below this a "line" is not worth drilling on its own — a two-puzzle session
# that keeps repeating is worse practice than a broader one.
MIN_PUZZLES_FOR_LINE_PRACTICE = 3


@app.get("/users/{username}/opening-practice", response_model=OpeningPracticeResponse)
def get_opening_practice(
    username: Username,
    opening_name: str = Query(
        ..., description="Full opening name from the explorer tree node"
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Whether an explorer line has puzzles to practise, and at what granularity."""
    assert_owns_username(account, username, db)

    line_count, family_count, family = DiagnosisRepository(db).opening_practice_counts(
        username, opening_name
    )

    if line_count >= MIN_PUZZLES_FOR_LINE_PRACTICE:
        scope = "line"
    elif family_count > 0:
        scope = "family"
    else:
        scope = "none"

    return OpeningPracticeResponse(
        username=username,
        opening_name=opening_name,
        opening_family=family,
        line_count=line_count,
        family_count=family_count,
        scope=scope,
    )


@app.get("/users/{username}/mistake-causes", response_model=MistakeCausesResponse)
def get_mistake_causes(
    username: Username,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Aggregate this user's diagnosed mistakes by cause.

    Descriptive only. A cause below ``MIN_DIAGNOSES_FOR_CAUSE_RANK`` is
    returned with ``insufficient_data`` set rather than withheld — the count is
    real and worth showing; what it does not yet support is being called a
    tendency.
    """
    from services.api.analytics_confidence import MIN_DIAGNOSES_FOR_CAUSE_RANK
    from services.api.diagnosis.causes import CAUSE_LABELS, UNCLASSIFIED

    assert_owns_username(account, username, db)

    repo = DiagnosisRepository(db)
    stats = repo.cause_breakdown(username)

    return MistakeCausesResponse(
        username=username,
        causes=[
            MistakeCause(
                cause=s.cause,
                label=CAUSE_LABELS.get(s.cause, s.cause),
                mistakes=s.mistakes,
                dominant_phase=s.dominant_phase,
                dominant_opening=s.dominant_opening,
                verified_attempts=s.verified_attempts,
                verified_puzzles=s.verified_puzzles,
                accuracy=s.accuracy,
                insufficient_data=s.insufficient_data,
                is_unclassified=s.cause == UNCLASSIFIED,
            )
            for s in stats
        ],
        total_diagnosed=sum(s.mistakes for s in stats),
        pending=repo.pending_count(username),
        min_for_ranking=MIN_DIAGNOSES_FOR_CAUSE_RANK,
    )


@app.get("/users/{username}/diagnosis/pending", response_model=PendingDiagnosisResponse)
def get_pending_diagnoses(
    username: Username,
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """How many puzzles still need diagnosing — drives the backfill CTA."""
    assert_owns_username(account, username, db)
    repo = DiagnosisRepository(db)
    return PendingDiagnosisResponse(
        username=username,
        pending=repo.pending_count(username),
        unenriched=repo.unenriched_count(username),
    )


@app.post(
    "/users/{username}/diagnose",
    response_model=JobStatusResponse,
    dependencies=[Depends(rate_limit("diagnose", default_limit=RATE_LIMIT_DIAGNOSE))],
)
def diagnose_puzzles_endpoint(
    username: Username,
    limit: int = Query(
        DIAGNOSIS_BATCH_DEFAULT,
        ge=1,
        le=DIAGNOSIS_BATCH_MAX,
        description="Maximum puzzles to analyse in this run",
    ),
    scope: str = Query(
        SCOPE_PENDING,
        description=(
            "'pending' analyses puzzles with no diagnosis yet. 'reenrich' "
            "re-runs puzzles already diagnosed without the model — use it once "
            "after adding ANTHROPIC_API_KEY to a deployment that backfilled "
            "without one."
        ),
    ),
    db: Session = Depends(get_db),
    account: Account | None = Depends(require_account),
):
    """Queue a diagnosis run over the puzzles that still need one.

    Scoped to its own job type, so it can run alongside puzzle generation --
    that is what the (username, type) active-job index exists for. A duplicate
    request returns the in-flight job rather than erroring, mirroring
    /puzzles/generate.

    ``scope=reenrich`` exists because version-based staleness cannot see a
    configuration change: adding an API key moves no data version, so a corpus
    diagnosed while the key was absent would stay rules-only forever. It is an
    explicit operator action rather than a standing rule — see
    ``DiagnosisRepository.unenriched_puzzle_ids`` for why a standing rule would
    re-attempt a rejected puzzle every day.
    """
    assert_owns_username(account, username, db)
    if scope not in (SCOPE_PENDING, SCOPE_REENRICH):
        raise HTTPException(
            status_code=422,
            detail=f"scope must be '{SCOPE_PENDING}' or '{SCOPE_REENRICH}'",
        )
    try:
        job = Job(
            username=username,
            type=JobType.DIAGNOSIS,
            status=JobStatus.QUEUED,
            message="Queued for diagnosis",
            params={"limit": limit, "scope": scope},
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
