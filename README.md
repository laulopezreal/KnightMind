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

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API info |
| POST | `/import/chesscom?username=...` | Import games from Chess.com |
| GET | `/openings` | Get opening tree data |

## Tech Stack

- **Frontend**: React, Vite, TypeScript, Tailwind CSS, D3.js
- **Backend**: FastAPI, Pydantic
- **Future**: Postgres, Neo4j, Stockfish

## License

MIT
