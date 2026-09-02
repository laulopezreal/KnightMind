#!/usr/bin/env bash
# Browser proof runner — resolved-outcome diagnosis
# Runs from any directory. Derives the repo root from this script's location.
#
# Usage:
#   bash tests/browser-proof/run.sh           # from repo root
#   bash /path/to/repo/tests/browser-proof/run.sh
#
# What it does:
#   1. Refuses dirty or environment-modified inputs, then builds the candidate
#      Vite bundle to /tmp/knightmind-bproof-dist and writes a manifest bound
#      to the exact checkout SHA
#   2. Runs the GREEN test (should PASS on the candidate branch)
#   3. Optionally runs the RED pre-fix probe if PREFIXED_DIST is set
#
# Requirements:
#   - Node.js (for Vite)
#   - python3 with Playwright installed:
#       python3 -m playwright install chromium

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST="/tmp/knightmind-bproof-dist"
COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"

echo "=== KnightMind browser proof: resolved-outcome diagnosis ==="
echo "Repo root : $REPO_ROOT"
echo "Dist      : $DIST"
echo ""

# ── 1. Build ───────────────────────────────────────────────────────
echo "--- Building Vite production bundle ---"
python3 - "$SCRIPT_DIR" "$REPO_ROOT" <<'PY'
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from bundle_provenance import assert_build_inputs_clean

assert_build_inputs_clean(pathlib.Path(sys.argv[2]))
PY
rm -rf -- "$DIST"
cd "$REPO_ROOT/apps/web"
npx vite build --outDir "$DIST"
cd "$REPO_ROOT"
python3 "$SCRIPT_DIR/bundle_provenance.py" certify "$REPO_ROOT" "$DIST" "$COMMIT"

# ── 2. GREEN test ──────────────────────────────────────────────────
echo ""
echo "--- Running GREEN test (candidate) ---"
python3 "$SCRIPT_DIR/test_resolved_diagnosis.py"

# ── 3. RED probe (optional) ────────────────────────────────────────
PREFIXED_DIST="${PREFIXED_DIST:-/tmp/knightmind-prefix-dist}"
if [ -f "$PREFIXED_DIST/index.html" ]; then
    echo ""
    echo "--- Running RED probe (pre-fix bundle at $PREFIXED_DIST) ---"
    python3 "$SCRIPT_DIR/test_prefixed_red.py"
else
    echo ""
    echo "[SKIP] RED probe: pre-fix bundle not found at $PREFIXED_DIST"
    echo "       Build 392e7c8 to that path to enable the RED probe."
fi
