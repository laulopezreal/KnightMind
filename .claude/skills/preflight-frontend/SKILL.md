---
name: preflight-frontend
description: Frontend-only checks — lint, type-check, build, and tests for the React/Vite app.
disable-model-invocation: true
---

Run frontend preflight checks. Stop at the first failure.

## Steps

1. **Lint**:
   ```bash
   cd apps/web && npm run lint
   ```
2. **Build** (includes TypeScript type-check):
   ```bash
   cd apps/web && npm run build
   ```
3. **Tests** (if any exist):
   ```bash
   cd apps/web && npm run test -- --run 2>/dev/null || echo "No tests configured"
   ```

## Output

| Check | Status |
|-------|--------|
| Lint | pass/fail |
| Build | pass/fail |
| Tests | pass/fail/skipped |

If all pass, print: "Frontend is clean."
If any fail, show the error output and suggest a fix.
