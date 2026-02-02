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

All commands in this section assume you are in the project root (the KnightMind directory).

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

## UX Assumptions

- Once a user has set their Chess.com username, the home screen emphasizes exploration and treats game syncing as an optional “Sync” action rather than a primary prompt to import games.
- Syncing reports “No new games found” when existing games are already in the database.

## Admin Ops Utilities

The Ops page now surfaces admin-oriented endpoints to keep operational workflows close to the UI. (Assumption: these remain admin-only until role-based access is in place.)

- **User index (`GET /users`)** – Powers the Ops “User Switcher,” letting admins set the active username without retyping it.
- **Storage parity report (`GET /ops/storage/report`)** – Powers the Ops “Data Integrity” panel, reporting filesystem vs. database gaps per user to support migration checks.

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

### Storage Modes (Games + Puzzles)

KnightMind now supports database-backed metadata for games and puzzles with a dual-write migration path.
Set `KNIGHTMIND_STORAGE_MODE` to control behavior:

- `filesystem` (default): read/write from `data/` directories only.
- `dual`: write to both filesystem and Postgres; read from DB first and fall back to filesystem when missing.
- `database`: read/write from Postgres only (filesystem untouched).

During migration, run `python scripts/backfill_storage.py` to ingest legacy `data/` files into Postgres and
print a per-user parity report. For auditability, the database stores `imported_at` and `source_path` for
every imported record.

Assumption: PGN content is stored in `games.pgn_blob` when using database-backed storage.

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

Database table: `games` (includes `pgn_blob`, `imported_at`, and `source_path`).
### Import Status

The API stores the last import timestamp and number of new games per user to surface sync status in the UI.

```
data/
  imports/<username>.json
```

**Import status fields:**
- `last_imported_at`: ISO timestamp
- `last_new_games`: integer count of new games in the last import

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

Database table: `puzzles` (includes `created_at`, `used_on`, `imported_at`, and `source_path`).

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
