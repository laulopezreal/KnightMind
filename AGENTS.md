# AGENTS.md

You are Codex working in this repo.

## Project Goal
**KnightMind – Personal Chess Intelligence Platform**

A platform for analyzing chess games, tracking progress, and gaining insights using AI-powered analysis.

## Tech Stack

### Frontend
- React + Vite
- TypeScript
- Tailwind CSS

### Backend
- FastAPI (Python)
- Postgres (metadata storage)
- Optional: Neo4j (graph analysis, future)
- Stockfish (chess engine analysis service)

## Development Rules

1. **Small incremental commits** – Keep changes focused and atomic
2. **Don't refactor unrelated code** – Only change what's necessary for the task
3. **Prefer simple solutions** – Choose straightforward approaches over complex ones
4. **Explain decisions briefly** – Document why, not just what
5. **UI Consistency** – All UI changes must follow `apps/web/DESIGN_GUIDE.md` and reuse existing patterns.
6. **Pull Requests** – All Pull Requests must target the `dev` branch, not `main`.
7. **Post-PR Checks** – When running the `postpr` workflow, you must also check for and fix any CI/CD failures.

## Commands to Run

### Frontend
```bash
npm test          # Run tests
npm run lint      # Lint code
npm run build     # Build for production
```

### Backend
```bash
pytest            # Run tests
ruff              # Lint (if configured)
black             # Format (if configured)
```

## Output Rule

After making changes, always:
1. **Summarize what changed** – Brief description of modifications
2. **How to run locally** – Commands needed to test the changes
