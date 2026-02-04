---
name: preflight-deploy
description: Full pre-deployment checklist — runs frontend and backend checks before any push or deploy.
disable-model-invocation: true
---

Run the full preflight checklist before deployment. Stop at the first failure and report it clearly.

## Steps

1. **Git status** — Confirm working tree is clean, all changes committed.
2. **Frontend checks**:
   ```bash
   cd apps/web && npm run lint && npm run build
   ```
3. **Backend checks**:
   ```bash
   cd /Users/laura/git/KnightMind && python -m pytest
   ```
4. **Database migrations** — Confirm no pending migrations:
   ```bash
   cd /Users/laura/git/KnightMind && python -m alembic -c services/api/alembic.ini current
   ```
5. **Branch check** — Confirm on `dev` branch (all PRs target dev).

## Output

Print a summary table:

| Check | Status |
|-------|--------|
| Git clean | pass/fail |
| Frontend lint | pass/fail |
| Frontend build | pass/fail |
| Backend tests | pass/fail |
| Migrations current | pass/fail |
| On dev branch | pass/fail |

If all pass, print: "Ready to deploy."
If any fail, print the failure details and stop.
