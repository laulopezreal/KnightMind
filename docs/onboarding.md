# KnightMind Onboarding

This guide helps first-time users get KnightMind running and complete the first useful loop:

1. connect username,
2. sync games,
3. generate puzzles,
4. solve a session.

## Prerequisites

- Node.js 22+ (CI builds against 22.x and 24.x)
- Python 3.11+
- Stockfish installed and available in PATH (or set via `STOCKFISH_PATH`)

All commands below run from the repo root.

## 1) Install dependencies

Python dependencies live in the root `pyproject.toml` and install as a single editable
package — there is no `services/api/requirements.txt`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cd apps/web && npm install && cd -
```

## 2) Start backend

The API needs a database and fails fast at startup without one. For a local run with no
Postgres, opt into the SQLite dev fallback:

```bash
export DATABASE_URL=postgresql+psycopg://knightmind:knightmind@localhost:5432/knightmind
make back
```

Backend URL: `http://localhost:8000`

## 3) Start frontend

```bash
make front
```

Frontend URL: `http://localhost:5173`

(`make dev` starts both at once.)

## 4) First-use workflow

1. Open the web app.
2. Set your Chess.com username.
3. Click **Sync** to import games.
4. Let puzzle generation finish.
5. Go to **Puzzles** and run a session.

## 5) Expected success checks

- Dashboard shows non-zero game count.
- Puzzle count increases after generation.
- You can complete at least one puzzle review.

## Common setup issues

- **No engine analysis results**: verify `stockfish --version` works and `STOCKFISH_PATH` is valid.
- **Frontend cannot call backend**: ensure API is on port `8000` and frontend runs on `5173`.
- **No games imported**: verify Chess.com username spelling and account visibility.
- **API exits immediately on startup**: `DATABASE_URL` is unset, or points at SQLite, which
  is not enabled. Set one of them.
- **`pip install -r requirements.txt` fails**: that file does not exist. Install from the
  repo root with `pip install -e ".[dev]"`.
- **`python -m venv` fails with `ensurepip is not available`** (Debian/Ubuntu): install
  the venv package first — `sudo apt install python3-venv`.
