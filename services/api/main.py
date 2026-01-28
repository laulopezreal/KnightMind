import sys
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add services directory to path for package imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from ingest import (
    get_player_archives,
    fetch_games_from_archive,
    parse_game,
    UserNotFoundError,
    RateLimitError,
    NetworkError,
    ImportError as ChessComImportError,
)
from storage import get_storage

app = FastAPI(title="KnightMind API", version="0.1.0")

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ImportResponse(BaseModel):
    message: str
    games_count: int
    new_games: int
    skipped_duplicates: int


class OpeningNode(BaseModel):
    name: str
    moves: str
    count: int
    children: list["OpeningNode"] | None = None


# Mock opening tree data
MOCK_OPENINGS: OpeningNode = OpeningNode(
    name="Start",
    moves="",
    count=150,
    children=[
        OpeningNode(
            name="King's Pawn",
            moves="1.e4",
            count=80,
            children=[
                OpeningNode(
                    name="Sicilian Defense",
                    moves="1.e4 c5",
                    count=35,
                    children=[
                        OpeningNode(name="Najdorf", moves="1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6", count=15),
                        OpeningNode(name="Dragon", moves="1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 g6", count=12),
                    ],
                ),
                OpeningNode(
                    name="French Defense",
                    moves="1.e4 e6",
                    count=25,
                    children=[
                        OpeningNode(name="Winawer", moves="1.e4 e6 2.d4 d5 3.Nc3 Bb4", count=10),
                    ],
                ),
                OpeningNode(name="Caro-Kann", moves="1.e4 c6", count=20),
            ],
        ),
        OpeningNode(
            name="Queen's Pawn",
            moves="1.d4",
            count=50,
            children=[
                OpeningNode(name="Queen's Gambit", moves="1.d4 d5 2.c4", count=30),
                OpeningNode(name="King's Indian", moves="1.d4 Nf6 2.c4 g6", count=20),
            ],
        ),
        OpeningNode(name="English", moves="1.c4", count=20),
    ],
)


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


@app.get("/openings", response_model=OpeningNode)
async def get_openings():
    """
    Get the opening tree for the user's games.
    Currently returns mocked data.
    """
    return MOCK_OPENINGS
