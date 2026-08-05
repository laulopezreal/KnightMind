"""Stockfish status and single-position evaluation.

Third slice of the main.py split (see ratings.py and openings_routes.py).

/engine/eval is the only route here that costs real CPU, and it is
unauthenticated, so it carries two independent limits: a per-principal rate
limit like the other expensive routes, and a per-process in-flight cap so a
caller cannot pile up unbounded Stockfish work even within their rate budget.
"""

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.api.engine import (
    EngineNotAvailableError,
    InvalidFenError,
    get_or_compute_eval,
    is_engine_available,
)
from services.api.identity import require_account
from services.api.models import Account
from services.api.ratelimit import rate_limit

router = APIRouter(tags=["engine"])

# Per-principal rate limit (audit gate 10); see services/api/ratelimit.py.
RATE_LIMIT_ENGINE_EVAL = 30  # Stockfish CPU; also has a per-process in-flight cap

# A FEN is bounded in length (piece placement + 5 short fields); anything much
# longer than a legal position is junk. Reject oversized input with 400 before
# it reaches the engine, so a caller can't ship a giant body to /engine/eval.
MAX_FEN_LENGTH = 120


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


@router.get("/engine/status", response_model=EngineStatusResponse)
def get_engine_status():
    """Check if the Stockfish engine is available.

    Sync on purpose: is_engine_available() spawns a Stockfish subprocess and
    does a round of IPC before tearing it down. On the event loop that stalled
    every other in-flight request for the life of the probe.
    """
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


@router.post(
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
