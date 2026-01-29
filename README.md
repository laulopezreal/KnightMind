# KnightMind

Personal Chess Intelligence Platform - Analyze your games, track progress, and gain insights.

## Project Structure

```
KnightMind/
├── apps/web/           # React + Vite + TypeScript + Tailwind frontend
├── services/api/       # FastAPI backend
├── services/ingest/    # Chess.com game import service
└── docs/               # Architecture documentation
```

## Prerequisites

- Node.js 18+
- Python 3.11+
- npm or yarn
- Stockfish (for puzzle generation)

## Setup

### Frontend (apps/web)

```bash
cd apps/web
npm install
```

### Backend (services/api)

```bash
cd services/api
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Stockfish Engine

Stockfish is required for position evaluation and puzzle generation.

**macOS:**
```bash
brew install stockfish
```

**Ubuntu/Debian:**
```bash
sudo apt install stockfish
```

**Windows:**
Download from https://stockfishchess.org/download/ and add to PATH.

**Verify installation:**
```bash
stockfish --version
```

**Configuration (optional):**
```bash
export STOCKFISH_PATH=/path/to/stockfish  # Custom binary path
export STOCKFISH_DEPTH=12                  # Analysis depth (default: 12)
export STOCKFISH_MOVETIME_MS=200           # Or use movetime instead of depth
```

## Running Locally

### Start the API server

```bash
cd services/api
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

API will be available at http://localhost:8000

### Start the web app

```bash
cd apps/web
npm run dev
```

Web app will be available at http://localhost:5173

The frontend proxies `/api/*` requests to the backend automatically.

## Testing

### Frontend

```bash
cd apps/web
npm run lint      # Lint code
npm run build     # Type check and build
```

### Backend

```bash
cd services/api
source venv/bin/activate
pytest            # Run tests
```

## Data Schema

### Games Storage

Games are stored as PGN files with JSON metadata:

```
data/
  pgn/<username>/<game_id>.pgn
  metadata/<username>/<game_id>.json
  index/<username>.json
```

**GameMetadata fields:**
- `game_id`: SHA256 hash of game URL (unique identifier)
- `url`: Chess.com game URL
- `username`: User who imported the game
- `white_username`, `black_username`: Player usernames
- `white_result`, `black_result`: Game results
- `time_control`: Time control string
- `end_time`: Unix timestamp
- `rated`: Boolean
- `imported_at`: ISO timestamp

### Puzzles Storage

Puzzles are generated from user blunders and stored as JSON:

```
data/
  puzzles/<username>/<puzzle_id>.json
  puzzle_index/<username>.json
```

**Puzzle fields:**
- `id`: UUID (unique identifier)
- `username`: User who played the game
- `source_game_id`: ID of the game this puzzle came from
- `ply`: Half-move number where blunder occurred
- `fen`: Position BEFORE the user's move
- `side_to_move`: "white" or "black"
- `played_move_uci`: The move the user actually played (blunder)
- `best_move_uci`: The best move according to engine
- `eval_before`: Evaluation before the move (in pawns)
- `eval_after`: Evaluation after the move (in pawns)
- `swing`: `eval_before - eval_after` (magnitude of blunder)
- `created_at`: ISO timestamp
- `used_on`: Date when puzzle was used (YYYY-MM-DD), null if unused

**Unique constraint:** `(username, source_game_id, ply)` prevents duplicate puzzles.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API info |
| POST | `/import/chesscom?username=...` | Import games from Chess.com |
| GET | `/openings?username=...&color=...` | Get opening tree data |
| POST | `/engine/eval` | Evaluate position (body: `{fen: string}`) |
| GET | `/engine/status` | Check Stockfish availability |
| POST | `/puzzles/generate?username=...&max_games=30&max_puzzles=30` | Generate puzzles from user games |
| GET | `/puzzles/daily?username=...&n=5` | Get daily puzzle set (rotates) |

## Tech Stack

- **Frontend**: React, Vite, TypeScript, Tailwind CSS, D3.js
- **Backend**: FastAPI, Pydantic, python-chess
- **Engine**: Stockfish (via `stockfish` PyPI package)
- **Future**: Postgres, Neo4j

## License

MIT
