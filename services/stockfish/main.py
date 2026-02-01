"""
Standalone Stockfish HTTP service.

Runs a single Stockfish process and exposes /status and /eval.
Configure with STOCKFISH_PATH, STOCKFISH_DEPTH. Default port 8001.
"""

import os
import shutil
import logging
from contextlib import asynccontextmanager

# Load .env from this directory so STOCKFISH_PATH is set
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

MATE_EVALUATION = 100.0
_engine = None


def _get_path() -> str:
    return os.environ.get("STOCKFISH_PATH", "stockfish")


def _get_depth() -> int:
    raw = os.environ.get("STOCKFISH_DEPTH")
    return int(raw) if raw else 12


def _convert_eval_to_pawns(evaluation: dict) -> float:
    eval_type = evaluation.get("type")
    value = evaluation.get("value", 0)
    if eval_type == "cp":
        return value / 100.0
    if eval_type == "mate":
        if value > 0:
            return MATE_EVALUATION
        if value < 0:
            return -MATE_EVALUATION
        return 0.0
    return 0.0


def _create_engine():
    try:
        from stockfish import Stockfish
    except ImportError:
        logger.error(
            "Stockfish Python package not installed. Run: pip install stockfish"
        )
        return None
    path = _get_path()
    # Resolve path so we can fail before creating Stockfish (avoids library __del__ error on failure)
    resolved = path if os.path.isabs(path) else shutil.which(path)
    if not resolved or not os.path.isfile(resolved):
        logger.error(
            "Stockfish binary not found at %r. Install it (e.g. brew install stockfish), "
            "then set STOCKFISH_PATH in services/stockfish/.env to the full path (e.g. /opt/homebrew/bin/stockfish).",
            path,
        )
        return None
    try:
        engine = Stockfish(path=resolved)
        engine.set_depth(_get_depth())
        engine.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        return engine
    except Exception as e:
        logger.error(
            "Stockfish failed to start at %r: %s. Check the binary runs (e.g. stockfish --version).",
            resolved,
            e,
        )
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine
    _engine = _create_engine()
    if _engine is None:
        logger.warning("Stockfish service started but engine is not available")
    yield
    _engine = None


app = FastAPI(title="KnightMind Stockfish Service", lifespan=lifespan)


class EvalRequest(BaseModel):
    fen: str


class EvalResponse(BaseModel):
    best_move_uci: str
    eval: float


@app.get("/status")
def status():
    """Health/readiness: is Stockfish available."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Stockfish not available")
    return {"available": True, "message": "Stockfish is ready"}


@app.post("/eval", response_model=EvalResponse)
def eval_fen(req: EvalRequest):
    """Evaluate a position; returns best move (UCI) and eval in pawns."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="Stockfish not available")
    try:
        if not _engine.is_fen_valid(req.fen):
            raise HTTPException(status_code=400, detail="Invalid FEN")
        _engine.set_fen_position(req.fen)
        best_move = _engine.get_best_move()
        if not best_move:
            raise HTTPException(status_code=400, detail="No legal moves")
        evaluation = _engine.get_evaluation()
        eval_pawns = _convert_eval_to_pawns(evaluation)
        return EvalResponse(best_move_uci=best_move, eval=eval_pawns)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Eval failed")
        raise HTTPException(status_code=500, detail=str(e))
