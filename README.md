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
cd /path/to/KnightMind
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install .[dev]
```

The backend and ingest services both install dependencies from the root `pyproject.toml` to keep a single source of truth. If you prefer editable installs during development, use `pip install -e .[dev]`.

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

**Stockfish as a separate service (recommended):**

Run Stockfish as its own process so the API talks to it over HTTP. Start the service first, then the API with `STOCKFISH_SERVICE_URL` set.

```bash
# Terminal 1: Stockfish service (port 8001)
cd services/stockfish
pip install -r requirements.txt
uvicorn main:app --port 8001
```

In `services/api/.env` (or env):
```bash
STOCKFISH_SERVICE_URL=http://localhost:8001
```

The Stockfish service uses `STOCKFISH_PATH` and `STOCKFISH_DEPTH`; ensure the binary is on PATH or set `STOCKFISH_PATH`.

**Local binary (optional):** If `STOCKFISH_SERVICE_URL` is unset, the API spawns Stockfish itself. Then:
```bash
export STOCKFISH_PATH=/path/to/stockfish  # Custom binary path
export STOCKFISH_DEPTH=12
```

## Running Locally

### Start the Stockfish service (if using separate service)

```bash
cd services/stockfish
uvicorn main:app --port 8001
```

Stockfish service will be at http://localhost:8001

### Start the API server

```bash
source venv/bin/activate
cd services/api
uvicorn main:app --reload --port 8000
```

API will be available at http://localhost:8000

### uv commands (from repo root)

After `uv sync`, you can use:

- **Start the backend:** `uv run start-backend` — runs the API on port 8000 (with reload).
- **Kill backends:** `uv run kill-backends` — sends SIGTERM to processes on ports 8000 and 8001 (API and Stockfish).

### CORS configuration

The API reads allowed CORS origins from `KNIGHTMIND_CORS_ORIGINS` as a comma-separated list. If this value is unset or empty, the API does not allow cross-origin browser requests (same-origin is still allowed).

Example (local frontend + staging):
```bash
export KNIGHTMIND_CORS_ORIGINS="http://localhost:5173,https://staging.example.com"
```

### Start the web app

```bash
cd apps/web
npm run dev
```

Web app will be available at http://localhost:5173

The frontend proxies `/api/*` requests to the backend automatically.
For consistency (and to avoid CORS issues), frontend API calls should use the shared `/api` base in `apps/web/src/api/core.ts` rather than reaching directly for a raw backend URL.
If an environment ever needs a different base, update `API_BASE` in `apps/web/src/api/core.ts` so every consumer stays aligned.

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

### Spaced Repetition Data

Spaced repetition statistics and review history are stored in the database for persistence and efficient querying:

#### `puzzle_stats` Table

Tracks aggregate performance and scheduling info for each puzzle.
- `puzzle_id`: Links to the JSON file ID
- `username`: Owner of the puzzle
- `attempts`: Total sessions
- `pass_count`: Sessions marked 'pass'
- `fail_count`: Sessions marked 'fail'
- `last_reviewed_at`: Timestamp of most recent attempt
- `last_result`: Result of most recent attempt ('pass' | 'fail')
- `next_due_at`: When the puzzle should be reviewed next
- `interval_days`: Current interval length
- `ease_factor`: SMT-style multiplier for interval calculation

#### `puzzle_reviews` Table

Audit log of every review session.
- `id`: Unique session ID
- `puzzle_id`: Links to `puzzle_stats`
- `username`: User who performed the review
- `reviewed_at`: Timestamp of the session
- `result`: Outcome ('pass' | 'fail')
- `time_spent_ms`: Duration of the session

### Spaced Repetition Logic

The system uses a simple deterministic scheduling algorithm:

- **On FAIL**:
  - `interval_days = 1`
  - `ease_factor = max(1.3, ease_factor - 0.2)`
  - `next_due_at = now + 1 day`
- **On PASS**:
  - If new (no interval): `interval_days = 1`
  - If `interval_days == 1`: `interval_days = 3`
  - Otherwise: `interval_days = round(interval_days * ease_factor)`
  - `ease_factor = min(2.8, ease_factor + 0.05)`
  - `next_due_at = now + interval_days`

## Database Configuration

KnightMind supports SQLite for local development and Postgres for production. Configure the
database connection via `DATABASE_URL`.

- **Default (local):** `sqlite:///./knightmind.db`
- **Postgres example:** `postgresql+psycopg://user:password@host:5432/knightmind`

Alembic migrations read the same `DATABASE_URL` value, so ensure it is set before running
`alembic upgrade head`.

## Operations & Deployment
 
 ### Database Migrations
 
 Operations on the database (like creating tables) are managed by Alembic. When deploying to Render or running locally after an update, ensure you run:
 
 ```bash
 alembic upgrade head
 ```
 
 This should be part of your Build Command or Run (Start) Command script.
 
 ### Background Worker
 
 The API process runs a background worker to generate puzzles.
 
 - **Single Instance**: Ensure you run only **one** instance of the API service (WEB_CONCURRENCY=1) to prevent double-processing/locking issues, as the worker runs in-process.
 - **Crash Recovery**: If the service restarts, the worker automatically resets any "running" jobs to "queued" to prevent stuck jobs.
 
 ## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API info |
| POST | `/import/chesscom?username=...` | Import games from Chess.com |
| GET | `/openings?username=...&color=...` | Get opening tree data |
| POST | `/engine/eval` | Evaluate position (body: `{fen: string}`) |
| GET | `/engine/status` | Check Stockfish availability |
| POST | `/puzzles/generate?username=...&max_games=30&max_puzzles=30` | Start puzzle generation job (Async) |
| GET | `/jobs/{job_id}` | Get job status (queued, running, succeeded, failed) |
| GET | `/puzzles/daily?username=...&n=5` | Get daily puzzle set (legacy selection) |
| GET | `/puzzles/due?username=...&n=5` | Get puzzles prioritized by SR (due first, then new) |
| POST | `/puzzles/{puzzle_id}/review` | Record review result and update SR scheduling |

## Tech Stack

- **Frontend**: React, Vite, TypeScript, Tailwind CSS, D3.js
- **Backend**: FastAPI, Pydantic, python-chess
- **Database**: Postgres (recommended via `DATABASE_URL`), SQLite (default for local)
- **Engine**: Stockfish (via `stockfish` PyPI package)
- **Future**: Neo4j

## License

MIT
