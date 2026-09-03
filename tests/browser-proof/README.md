# Browser proof: resolved-outcome diagnosis

last_edited_at: 2026-09-02T18:40:38+02:00

Candidate: the exact commit checked out when the proof runs, printed as `Candidate commit (runtime checkout)`
Original proof branch: `test/resolved-diagnosis-browser-proof`
Pre-fix comparison bundle: `392e7c8` (used only by the optional RED probe)
Task: `t_cdb7bc04`

## What this proves

`test_resolved_diagnosis.py` runs a headless Chromium browser session (Playwright system install) against the candidate Vite production bundle, with all API calls intercepted via synthetic fixtures. No production state is touched.

### Checks (both desktop 1280x800 and mobile 390x844)

1. After a correct move and a successful review, the diagnosis card renders visibly:
   - Heading ("Mistake diagnosis")
   - Cause label ("Loose piece awareness")
   - Explanation prose
   - Evidence (best move, eval swing)
   - "Next time" recommendation
2. No horizontal overflow at 390x844.
3. Zero real browser console errors (assertion — any non-allowlisted error fails the test).
4. Moving to the next puzzle clears the prior diagnosis.
5. `test_prefixed_red.py` confirms the same sequence against the pre-fix comparison bundle (`392e7c8`) produces NO diagnosis card (RED).
6. The server uses an immutable in-memory artifact, and an intentional post-validation
   on-disk replacement cannot change the bytes returned to the browser.

## How to run

From the repository root, use the included runner:

```bash
PYTHON_BIN=python3.12 bash tests/browser-proof/run.sh
```

Or run steps individually:

```bash
# Build the candidate bundle once in a private staging directory. First fail closed
# if any tracked/untracked build input, VITE_* override, or production env file could alter it.
python3 tests/browser-proof/bundle_provenance.py check .
STAGING="$(mktemp -d /tmp/knightmind-bproof.XXXXXX)"
export BPROOF_DIST="$STAGING/dist"
cd apps/web
npx vite build --outDir "$BPROOF_DIST"
cd ../..
python3 tests/browser-proof/bundle_provenance.py certify \
  . "$BPROOF_DIST" "$(git rev-parse HEAD)"

# Run GREEN test (the current checkout, whose SHA is recorded at runtime)
python3 tests/browser-proof/test_resolved_diagnosis.py
rm -rf -- "$STAGING"

# Run RED test (pre-fix 392e7c8) — confirms original bug
# Requires pre-fix bundle at /tmp/knightmind-prefix-dist
python3 tests/browser-proof/test_prefixed_red.py
```

`test_resolved_diagnosis.py` rejects a missing, malformed, stale, or mismatched
manifest before it starts a static server or browser runtime. Certification records
the exact sorted SHA-256 inventory of all regular bundle files. Validation recomputes
that inventory and rejects changed, added, deleted, unreadable, symlinked, nonregular,
or unsafe entries before browser execution. It then loads every manifest-bound file
into immutable memory, verifies each loaded digest, revalidates the directory, and
serves only those detached bytes. The normal runner also rejects dirty tracked and
untracked Vite inputs plus Vite environment overrides/files before the build, then
rechecks them immediately before writing the manifest. Direct/manual builds are
provenance-ready only when they use the same private staging, pre-build guard, and
post-build certification command shown above.

## Results (run 2026-08-31)

GREEN: `RESULT: PASS` — both desktop and mobile
RED: `RED CONFIRMED` — pre-fix build shows no diagnosis card

Artifacts (screenshots) written to `/tmp/knightmind-bproof-artifacts/`.

## Constraints

- No new npm dependency added. Uses system Playwright (`python3.12 -m playwright` / `playwright@1.58.0`).
- No lockfile or CI workflow changes.
- No production API called. Playwright `route()` intercepts all `https://knightmind-api.onrender.com/**` calls.
- No product behavior changed. Tests only read the DOM, never write to it beyond filling the move input.
