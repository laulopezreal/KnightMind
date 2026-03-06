# KnightMind Onboarding

This guide helps first-time users get KnightMind running and complete the first useful loop:

1. connect username,
2. sync games,
3. generate puzzles,
4. solve a session.

## Prerequisites

- Node.js 18+
- Python 3.11+
- Stockfish installed and available in PATH (or set via `STOCKFISH_PATH`)

## 1) Start backend

```bash
cd services/api
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Backend URL: `http://localhost:8000`

## 2) Start frontend

```bash
cd apps/web
npm install
npm run dev
```

Frontend URL: `http://localhost:5173`

## 3) First-use workflow

1. Open the web app.
2. Set your Chess.com username.
3. Click **Sync** to import games.
4. Let puzzle generation finish.
5. Go to **Puzzles** and run a session.

## 4) Expected success checks

- Dashboard shows non-zero game count.
- Puzzle count increases after generation.
- You can complete at least one puzzle review.

## Common setup issues

- **No engine analysis results**: verify `stockfish --version` works and `STOCKFISH_PATH` is valid.
- **Frontend cannot call backend**: ensure API is on port `8000` and frontend runs on `5173`.
- **No games imported**: verify Chess.com username spelling and account visibility.
