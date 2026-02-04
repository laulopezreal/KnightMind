---
name: preflight-backend
description: Backend-only checks — pytest, linting, and migration status for the FastAPI app.
disable-model-invocation: true
---

Run backend preflight checks. Stop at the first failure.

## Steps

1. **Tests**:
   ```bash
   cd /Users/laura/git/KnightMind && python -m pytest
   ```
2. **Lint with ruff** (if configured):
   ```bash
   cd /Users/laura/git/KnightMind && python -m ruff check . 2>/dev/null || echo "ruff not configured"
   ```
3. **Format check with black** (if configured):
   ```bash
   cd /Users/laura/git/KnightMind && python -m black --check . 2>/dev/null || echo "black not configured"
   ```
4. **Migration status**:
   ```bash
   cd /Users/laura/git/KnightMind && python -m alembic -c services/api/alembic.ini current
   ```

## Output

| Check | Status |
|-------|--------|
| Tests | pass/fail |
| Ruff lint | pass/fail/skipped |
| Black format | pass/fail/skipped |
| Migrations current | pass/fail |

If all pass, print: "Backend is clean."
If any fail, show the error output and suggest a fix.
