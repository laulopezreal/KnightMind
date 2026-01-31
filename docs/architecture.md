# KnightMind Architecture

## Overview

KnightMind is a personal chess intelligence platform that helps players analyze their games, track progress, and gain insights using AI-powered analysis.

## System Components

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                              │
│                   (React + Vite + TS)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │    Home     │  │  Openings   │  │   (future)  │         │
│  │   Import    │  │    Tree     │  │  Analysis   │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP/REST
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      API Service                             │
│                       (FastAPI)                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │   Import    │  │  Openings   │  │   (future)  │         │
│  │  Endpoint   │  │  Endpoint   │  │   Engine    │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
┌──────────────┐  ┌────────────┐  ┌────────────┐
│    Ingest    │  │  Database  │  │ (future)   │
│   Service    │  │ (Postgres) │  │ Stockfish  │
│ (Chess.com)  │  │            │  │  Service   │
└──────────────┘  └────────────┘  └────────────┘
```

## Directory Structure

```
KnightMind/
├── apps/
│   └── web/              # React frontend
├── services/
│   ├── api/              # FastAPI backend
│   └── ingest/           # Game import service
├── docs/                 # Documentation
└── AGENTS.md             # AI agent instructions
```

## Data Flow

### Game Import
1. User enters Chess.com username in web UI
2. Frontend calls `POST /api/import/chesscom?username=...`
3. API triggers ingest service to fetch games
4. Games are parsed and stored in the configured database (Postgres recommended)
5. Response returns count of imported games

### Opening Analysis
1. User navigates to Openings page
2. Frontend calls `GET /api/openings`
3. API queries game data and builds opening tree
4. D3.js renders interactive tree visualization

## Future Enhancements

- **Stockfish Integration**: Deep position analysis
- **Neo4j Graph DB**: Store opening repertoire as a graph
- **Pattern Recognition**: Identify recurring mistakes
- **Progress Tracking**: ELO trends and improvement metrics
