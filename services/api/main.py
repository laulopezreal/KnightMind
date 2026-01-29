from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from services.api.engine import (
    EngineNotAvailableError,
    InvalidFenError,
    evaluate_position,
    is_engine_available,
)
from services.api.openings import build_opening_tree
from services.api.puzzles import generate_puzzles
from services.api.storage import get_storage
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
)

app = FastAPI(title="KnightMind API", version="0.1.0")

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


class EvalRequest(BaseModel):
    fen: str


class EvalResponse(BaseModel):
    best_move_uci: str
    eval: float  # In pawns, from side-to-move perspective


class EngineStatusResponse(BaseModel):
    available: bool
    message: str


class PuzzleGenerationResponse(BaseModel):
    message: str
    generated: int
    skipped: int
    analyzed_positions: int


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
    
    Args:
        request: Request with FEN string
        
    Returns:
        Best move in UCI format and evaluation in pawns
    """
    try:
        result = evaluate_position(request.fen)
        return EvalResponse(best_move_uci=result.best_move_uci, eval=result.eval)
    except EngineNotAvailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except InvalidFenError as e:
        raise HTTPException(status_code=400, detail=str(e))
