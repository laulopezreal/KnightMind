---
last_edited_at: 2026-07-10T13:35:08+02:00
---
# KnightMind

Personal Chess Intelligence Platform - Analyze your games, track progress, and gain insights.


## Why KnightMind

KnightMind helps improving chess players turn raw game history into clear, repeatable training decisions. Instead of generic puzzles, it generates personalized puzzles from your own mistakes and tracks progress over time.

## Core Features

- **Personalized puzzle generation** from your imported Chess.com games
- **Spaced repetition training** to revisit the right puzzles at the right time
- **Progress dashboards** for momentum, streaks, and recurring motifs
- **Engine-assisted review** powered by local Stockfish

## Quickstart

1. Start backend (`services/api`) on `http://localhost:8000`
2. Start frontend (`apps/web`) on `http://localhost:5173`
3. Set Chess.com username in-app and run **Sync**
4. Generate puzzles and begin training

Detailed setup: [docs/onboarding.md](docs/onboarding.md)

## Documentation

- [Documentation index](docs/README.md)
- [Onboarding](docs/onboarding.md)
- [Architecture](docs/architecture.md)
- [Discoverability audit](docs/discoverability-audit.md)

## Findability (SEO & search intent)

This repository is intentionally optimized for searches like:

- "personal chess training app"
- "chess game analysis platform"
- "fastapi react chess dashboard"
- "stockfish puzzle generator"

Confirmed canonical public web URL is `https://guessme.world`. The public API URL is `https://api.guessme.world`, served by the claw-home Caddy ingress documented in `OPERATIONS.md`. If the public web URL changes, update `apps/web/index.html`, `apps/web/public/robots.txt`, `apps/web/public/sitemap.xml`, and `docs/discoverability-audit.md`.

## GitHub Discoverability

Recommended repository topics:

- `chess`
- `react`
- `fastapi`
- `stockfish`
- `spaced-repetition`
- `analytics`

Contribution entry points:

- Use the issue templates in `.github/ISSUE_TEMPLATE/` for bug reports, feature requests, and docs improvements.
- Use `.github/pull_request_template.md` to keep PR context and validation consistent.

## Project Structure

```
KnightMind/
├── apps/web/           # React + Vite + TypeScript + Tailwind frontend
├── services/api/       # FastAPI backend
├── services/ingest/    # Chess.com game import service
└── docs/               # Architecture documentation
```

## Prerequisites

- Node.js 22+ (CI builds against 22.x and 24.x)
- Python 3.11+
- npm
- Stockfish (for puzzle generation)

## Setup

All commands in this section assume you are in the project root (the KnightMind directory).

### Backend

Python dependencies are declared in the root `pyproject.toml` and installed as one
editable package covering `services/api`, `services/ingest`, and `scripts` — there is no
per-service `requirements.txt`. Create the virtualenv at the repo root:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

This is the same install CI runs (`.github/workflows/ci-backend.yaml`). The `[dev]`
extra adds pytest, ruff, and black.

On Debian/Ubuntu, `python -m venv` fails with `ensurepip is not available` unless the
venv package is installed:

```bash
sudo apt install python3-venv   # or python3.12-venv, matching your Python
```

> **Note:** a `uv.lock` is also committed, but nothing builds from it — CI and the
> Dockerfile both install with pip, so the pip flow above is the canonical one. If you
> use `uv`, run `uv lock --check` first to confirm the lockfile is still in step with
> `pyproject.toml`.

### Frontend (apps/web)

```bash
cd apps/web
npm install
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
source .venv/bin/activate
make back        # or: cd services/api && python -m uvicorn main:app --reload --port 8000
```

The API requires a database: set `DATABASE_URL` (e.g. in `services/api/.env`), or for a
local Postgres, `make docker-up` starts one.
Startup fails fast if neither is set.

API will be available at http://localhost:8000

### CORS configuration

The API reads allowed CORS origins from `KNIGHTMIND_CORS_ORIGINS` as a comma-separated list. If this value is unset or empty, the API does not allow cross-origin browser requests (same-origin is still allowed).

Example (local frontend + staging):
```bash
export KNIGHTMIND_CORS_ORIGINS="http://localhost:5173,https://staging.example.com"
```

### Start the web app

```bash
make front       # or: cd apps/web && npm run dev
```

`make dev` starts the API and the web app together.

Web app will be available at http://localhost:5173

The frontend proxies `/api/*` requests to the backend automatically.
For consistency (and to avoid CORS issues), frontend API calls should use the shared `/api` base in `apps/web/src/api/core.ts` rather than reaching directly for a raw backend URL.
If an environment ever needs a different base, update `API_BASE` in `apps/web/src/api/core.ts` so every consumer stays aligned.

## UX Assumptions

- Once a user has set their Chess.com username, the home screen emphasizes exploration and treats game syncing as an optional “Sync” action rather than a primary prompt to import games.
- Syncing reports “No new games found” when existing games are already in the database.
- Engine Analysis “Clue” mirrors the puzzle flow: first reveals which piece to move, then highlights the from/to squares for the best move. Analysis auto-runs after board changes, with manual re-run available.
- UI improvement proposals are prioritized for maximum visible impact with minimal engineering effort, assuming no backend/API contract changes are required for the first pass.
- Mobile navigation polish assumes existing routes and username/theme controls remain functionally unchanged (visual/responsive updates only).
- Shared page UI primitives (header + data states) are intentionally lightweight and style-only, so product copy and API behavior remain unchanged in this pass.
- This consistency pass extends shared header/error patterns to Library, Rating Insights, and Engine without changing filtering, analysis, or rating calculation logic.
- Loading-state consistency in Library, Rating Insights, and Engine is presentational only; fetch cadence and existing async behavior are intentionally unchanged.
- Session-window loading in Rating Insights now uses the shared loading primitive for visual consistency only, with identical session-fetch logic.
- Opening Explorer graph-loading state now uses the shared loading primitive; rendering and fetch flow are unchanged.

## Admin Ops Utilities

The **Ops** page (`/ops` in the web app) surfaces admin-oriented endpoints to keep operational workflows close to the UI. (Assumption: these remain admin-only until role-based access is in place.)

- **User index (`GET /users`)** – Powers the Ops "User Switcher," letting admins set the active username without retyping it.

## Testing

The `Makefile` is the canonical entry point; every target runs from the repo root.

```bash
make test         # Backend (pytest) + frontend (vitest)
make lint         # ruff + black + eslint
make preflight    # Everything CI checks, before you push
```

### Backend

```bash
source .venv/bin/activate
make test-back    # python -m pytest
make lint-back    # python -m ruff check . && python -m black --check .
```

Pytest collects from `services/api` and `services/ingest` (see `[tool.pytest.ini_options]`
in `pyproject.toml`). The root `conftest.py` requires `KNIGHTMIND_TEST_DATABASE_URL` so the suite
runs without a Postgres instance.

### Frontend

```bash
make test-front   # vitest --run
make lint-front   # eslint
make build-front  # tsc -b && vite build (type check + build)
```

## Data Schema

### Games (`games` table)

All game data is stored in the database. PGN content is stored inline in `pgn_blob`.

**Columns:**
- `game_id` (PK): SHA256 hash of game URL
- `url`: Chess.com game URL
- `username`: User who imported the game (lowercased)
- `white_username`, `black_username`: Player usernames
- `white_result`, `black_result`: Game results
- `time_control`: Time control string
- `end_time`: Unix timestamp
- `rated`: Boolean
- `pgn_blob`: Full PGN text
- `imported_at`: Timestamp
- `source_path`: Original filesystem path (for audit trail)

### Import Status (`import_summaries` table)

Tracks the last import per user to surface sync status in the UI.

- `username` (PK): Lowercased username
- `last_imported_at`: Timestamp of last import
- `last_new_games`: Number of new games in the last import

### Puzzles (`puzzles` table)

Puzzles are generated from user blunders and stored in the database.

**Columns:**
- `id` (PK): UUID
- `username`: User who played the game (lowercased)
- `source_game_id`: FK to `games.game_id`
- `ply`: Half-move number where blunder occurred
- `fen`: Position before the user's move
- `side_to_move`: "white" or "black"
- `played_move_uci`: The move the user actually played (blunder)
- `best_move_uci`: The best move according to engine
- `eval_before`, `eval_after`: Evaluation in pawns
- `swing`: `eval_before - eval_after` (magnitude of blunder)
- `created_at`: Timestamp
- `used_on`: Date when puzzle was used, null if unused

**Unique constraint:** `(username, source_game_id, ply)` prevents duplicate puzzles.

### Spaced Repetition Data

Spaced repetition statistics and review history are stored in the database for persistence and efficient querying:

#### `puzzle_stats` Table

Tracks aggregate performance and scheduling info for each puzzle.
- `puzzle_id`: FK to `puzzles.id`
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

KnightMind is **Postgres-only**, in production and locally. The connection is configured
via `DATABASE_URL`, which is **required**: the API fails fast at startup when it is unset,
and refuses a SQLite URL outright rather than silently writing to an ephemeral file. The
test suite runs on Postgres too — there is no second backend to fall back to, which is the
point.

- **Postgres example:** `postgresql+psycopg://user:password@host:5432/knightmind`
- **Local development:** point `DATABASE_URL` at the compose Postgres (`make docker-up`).

This paragraph used to say SQLite was supported for local development. It has not been
since the fallback was removed; `services/api/db.py` rejects any URL beginning `sqlite`.

Alembic migrations load `services/api/.env` (the same file the API reads) and use the same
`DATABASE_URL` value, so `alembic upgrade head` runs against the database the app uses.

## Operations & Deployment

 ### Database Migrations

 Operations on the database (like creating tables) are managed by Alembic. When deploying to Render or running locally after an update, ensure you run:

 ```bash
 alembic upgrade head
 ```

 This should be part of your Build Command or Run (Start) Command script.

 ### Legacy Filesystem Migration

 If migrating from an older version that stored data in `data/` directories, run:

 ```bash
 python -m scripts.migrate_to_db --data-dir services/api/data
 ```

 Use `--dry-run` to preview counts before committing. The script is idempotent and skips duplicates.

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
| POST | `/puzzles/{puzzle_id}/review` | Record a review; the outcome is verified server-side before SR scheduling |

**Auth, ownership, and rate limits:** Multi-user auth is gated by `KNIGHTMIND_REQUIRE_AUTH` (default OFF — the API stays single-user and the current frontend sends no token). When enabled, expensive routes require a valid bearer token and enforce per-user ownership of the target username (`services/api/identity.py`). Regardless of auth, the expensive routes (`/engine/eval`, `/puzzles/generate`, `/import/chesscom`, `/ratings/snapshot`) carry per-principal rate limits and request-size caps (`services/api/ratelimit.py`). Puzzle solutions are not sent to the client with the training payload; `/puzzles/{puzzle_id}/review` re-checks the submitted move server-side.

## Tech Stack

- **Frontend**: React, Vite, TypeScript, Tailwind CSS, D3.js
- **Backend**: FastAPI, Pydantic, python-chess
- **Database**: Postgres (via `DATABASE_URL`) everywhere — production, local dev and tests
- **Engine**: Stockfish (via `stockfish` PyPI package)
- **Future**: Neo4j

## License

MIT
