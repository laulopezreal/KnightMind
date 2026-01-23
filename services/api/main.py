from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
    Currently returns mocked data - actual implementation will call Chess.com API.
    """
    # TODO: Implement actual Chess.com API integration
    # For now, return mock response
    return ImportResponse(
        message=f"Successfully imported games for {username}",
        games_count=42,  # Mock count
    )


@app.get("/openings", response_model=OpeningNode)
async def get_openings():
    """
    Get the opening tree for the user's games.
    Currently returns mocked data.
    """
    return MOCK_OPENINGS
