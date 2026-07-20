---
alwaysApply: true
---

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
- Stockfish (local binary, chess engine analysis)

## Infrastructure & Deployment

### Current State
- **Database**: Self-hosted Postgres (`postgres:16-alpine`) running under Docker Compose alongside the API on the production VPS (claw-home). Supabase was an earlier migration step and has been retired — it is no longer part of the running stack.
- **API**: FastAPI connects to Postgres via `DATABASE_URL` (direct connection string). The API fails fast at startup when `DATABASE_URL` is unset; `KNIGHTMIND_DEV_SQLITE=1` is a local-dev-only opt-in for a throwaway SQLite file.
- **Stockfish**: Local binary (`STOCKFISH_PATH`), not a separate service.
- **Auth / multi-tenancy**: Multi-user auth (JWT bearer + per-username ownership checks) lives behind `KNIGHTMIND_REQUIRE_AUTH` (default OFF — single-user behaviour). See `services/api/identity.py`.
- **Abuse resistance**: Per-principal rate limits + payload size caps on expensive routes (`/engine/eval`, `/puzzles/generate`, `/import/chesscom`, `/ratings/snapshot`). See `services/api/ratelimit.py`.
- **Engine cache**: The FEN eval cache is version-aware — cache keys fold in the scheme, eval-conversion, and engine versions so stale entries self-invalidate. See `services/api/engine/stockfish.py`.
- **Puzzle reviews**: Review outcomes are server-verified (the solution is checked server-side, not trusted from the client).

### Deployment topology
- **VPS (claw-home)**: FastAPI + Stockfish + Postgres on one machine via Docker Compose (project `knightmind`). Operational SSOT is `OPERATIONS.md`.
- **Frontend**: React/Vite static site on Cloudflare Pages, serving the canonical domain `https://guessme.world` (API at `https://api.guessme.world`).
- **Future**: Optional Neo4j for graph analysis.

## Development Rules

1. **Small incremental commits** – Keep changes focused and atomic
2. **Don't refactor unrelated code** – Only change what's necessary for the task
3. **Prefer simple solutions** – Choose straightforward approaches over complex ones
4. **Explain decisions briefly** – Document why, not just what
5. **UI Consistency** – All UI changes must follow `apps/web/DESIGN_GUIDE.md` and reuse existing patterns.
6. **Pull Requests** – All Pull Requests must target the `dev` branch, not `main`.
7. **Post-PR Checks** – When running the `postpr` workflow, you must also check for and fix any CI/CD failures.

## File Scope Discovery Rule

- When file scope is unclear:
  - First run a discovery-only step.
  - Do not implement or edit files during discovery.
  - Wait for explicit approval of the file list before modifying code.
  - The agent must end Discovery with exactly:
  WAITING FOR FILE SELECTION.


## Commands to Run

### Frontend
```bash
npm run lint      # Lint code
npm run build     # Build for production
```

### Backend
```bash
pytest            # Run tests
ruff              # Lint (if configured)
black             # Format (if configured)
```

## Creating Pull Requests

When creating PRs, use the following approach:

### Standard PR Creation
```bash
# Use --base dev (all PRs target dev branch)
# Use required_permissions: ["all"] to bypass sandbox restrictions
gh pr create --title "feat(scope): description" --base dev --body "PR description here"
```

### If `gh pr create` fails with network/TLS errors:
1. **Try with `["all"]` permissions** – This bypasses sandbox restrictions
2. **Use `--web` flag** – Opens browser instead of API calls: `gh pr create --web`
3. **Push to existing PR branch** – If PR exists, just push commits and use `gh pr edit <PR_NUMBER>`

### Common Issues & Solutions:
- **TLS certificate errors**: Use `required_permissions: ["all"]`
- **credential-gh not found**: Ignore warning, command usually still works
- **"Device not configured"**: Push may still succeed despite error message

### Example Working Command:
```bash
gh pr create --title "feat(web): add feature" --base dev --body "Summary of changes"
# With permissions: required_permissions: ["all"]
```

### After PR Creation:
1. Run `gh pr list` to confirm PR was created
2. Run `gh pr checks <PR_NUMBER>` to monitor CI status
3. Use `gh pr edit <PR_NUMBER> --body "..."` to update description

## Output Rule

After making changes, always:
1. **Summarize what changed** – Brief description of modifications
2. **How to run locally** – Commands needed to test the changes
