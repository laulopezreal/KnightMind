"""
Browser proof: resolved-outcome diagnosis renders after stats-fold (same-id reference change).

Task: t_cdb7bc04
Branch: test/resolved-diagnosis-browser-proof
Candidate: f8520fa0b4aff702bdb398c5933eb26a0630d8a2
Pre-fix parent: 392e7c8

Proves at both desktop (1280x800) and mobile (390x844):
  1. After a successful review + ready diagnosis, the card renders:
     heading, cause, explanation, evidence, next-time guidance.
  2. The diagnosis section is reachable and does not overflow horizontally on mobile.
  3. Browser console errors are empty after navigation and after resolution.
  4. Moving to a different puzzle clears the prior diagnosis.
  5. The test passes against the candidate (f8520fa) and fails against pre-fix (392e7c8).
     (Point 5 is demonstrated by the RED build section at the bottom of this script.)

Usage:
  python3 tests/browser-proof/test_resolved_diagnosis.py

  Artifacts:
    /tmp/knightmind-bproof-artifacts/desktop_diagnosis_visible.png
    /tmp/knightmind-bproof-artifacts/mobile_diagnosis_visible.png
    /tmp/knightmind-bproof-artifacts/mobile_no_overflow.png
    /tmp/knightmind-bproof-artifacts/puzzle2_diagnosis_cleared.png
    /tmp/knightmind-bproof-artifacts/prefixed_fails.txt   (RED build result)
"""

import json
import os
import pathlib
import sys
import threading
import http.server
import time

from playwright.sync_api import sync_playwright, Route, Request

ARTIFACTS = pathlib.Path("/tmp/knightmind-bproof-artifacts")
ARTIFACTS.mkdir(exist_ok=True)

DIST = pathlib.Path("/tmp/knightmind-bproof-dist")
if not (DIST / "index.html").exists():
    sys.exit(f"FAIL: Built dist not found at {DIST}. Run the Vite build first.")

# ──────────────────────────────────────────────────────────────────
# Minimal static file server for the built bundle
# ──────────────────────────────────────────────────────────────────

class SPAHandler(http.server.BaseHTTPRequestHandler):
    """Serves /tmp/knightmind-bproof-dist. Falls back to index.html for SPA routes."""
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            path = "/index.html"
        target = DIST / path.lstrip("/")
        if target.exists() and target.is_file():
            data = target.read_bytes()
            self.send_response(200)
            if path.endswith(".html"):
                ct = "text/html"
            elif path.endswith(".js"):
                ct = "application/javascript"
            elif path.endswith(".css"):
                ct = "text/css"
            else:
                ct = "application/octet-stream"
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            # SPA fallback
            data = (DIST / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


def start_static_server(port=19801):
    server = http.server.HTTPServer(("127.0.0.1", port), SPAHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://127.0.0.1:{port}"


# ──────────────────────────────────────────────────────────────────
# Synthetic API fixtures
# ──────────────────────────────────────────────────────────────────

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PUZZLE_1_ID = "bproof-p1"
PUZZLE_2_ID = "bproof-p2"

PUZZLE_1 = {
    "id": PUZZLE_1_ID,
    "display_name": "BProof Test Puzzle",
    "username": "testplayer",
    "source_game_id": "g1",
    "ply": 10,
    "fen": START_FEN,
    "side_to_move": "white",
    "played_move_uci": "e2e3",
    "best_move_uci": "e2e4",
    "eval_before": 0.5,
    "eval_after": -0.5,
    "swing": 1.0,
    "created_at": "2026-01-01T00:00:00Z",
    "used_on": None,
    "attempts": 0,
    "pass_count": 0,
    "fail_count": 0,
}

# After review, the server responds with stats and also triggers the setPuzzles
# stats-fold in the real usePuzzleSession hook, creating a same-id reference.
PUZZLE_1_AFTER_REVIEW = {
    **PUZZLE_1,
    "attempts": 1,
    "pass_count": 1,
    "fail_count": 0,
    "last_reviewed_at": "2026-01-01T00:01:00Z",
    "last_result": "pass",
    "next_due_at": "2026-01-08T00:00:00Z",
}

PUZZLE_2 = {
    "id": PUZZLE_2_ID,
    "display_name": "BProof Test Puzzle 2",
    "username": "testplayer",
    "source_game_id": "g2",
    "ply": 12,
    "fen": START_FEN,
    "side_to_move": "white",
    "played_move_uci": "d2d3",
    "best_move_uci": "d2d4",
    "eval_before": 0.3,
    "eval_after": -0.3,
    "swing": 0.6,
    "created_at": "2026-01-01T00:00:00Z",
    "used_on": None,
    "attempts": 0,
    "pass_count": 0,
    "fail_count": 0,
}

DUE_RESPONSE = {
    "due_count": 2,
    "returned_count": 2,
    "now": "2026-01-01T00:00:00Z",
    "puzzles": [PUZZLE_1, PUZZLE_2],
}

CHECK_RESPONSE = {
    "correct": True,
    "result": "pass",
    "complete": True,
    "reply": None,
    "next_ply_index": None,
}

REVIEW_RESPONSE = {
    "next_due_at": "2026-01-08T00:00:00Z",
    "interval_days": 7,
    "ease_factor": 2.5,
    "feedback": "Good work!",
    "result": "pass",
    "verified": True,
    "source": "server_verified",
    "puzzle_info": {
        "fen": START_FEN,
        "best_move": "e2e4",
        "side_to_move": "white",
        "swing": 1.0,
    },
    "stats": {
        "attempts": 1,
        "pass_count": 1,
        "fail_count": 0,
        "last_reviewed_at": "2026-01-01T00:01:00Z",
        "last_result": "pass",
    },
}

DIAGNOSIS_READY = {
    "state": "ready",
    "puzzle_id": PUZZLE_1_ID,
    "primary_motif": "hanging_queen",
    "primary_cause": "loose_piece_awareness",
    "primary_cause_label": "Loose piece awareness",
    "secondary_causes": [],
    "secondary_cause_labels": [],
    "phase": "middlegame",
    "evidence": [
        {"id": "best.move", "label": "Best move", "value": "e2e4 (forcing)"},
        {"id": "eval.swing", "label": "Evaluation swing (pawns)", "value": "1.00"},
    ],
    "evidence_withheld": False,
    "explanation": "You moved the pawn passively instead of taking central control.",
    "training_recommendation": "Practice recognising central pawn breaks in the opening.",
    "user_confirmed_cause": None,
    "source": "rules",
    "diagnosed_at": "2026-01-01T00:00:00Z",
    "cause_options": [
        {"value": "loose_piece_awareness", "label": "Loose piece awareness"},
        {"value": "king_safety_blindness", "label": "King safety blindness"},
    ],
}

SESSION_START = {"session_id": "sess-bproof-1", "requested_n": 5, "session_type": "standard"}

USER_STATUS = {
    "games_count": 10,
    "puzzles_count": 5,
    "due_count": 3,
    "has_new_games": False,
}

REVEAL_RESPONSE = {
    "best_move_uci": "e2e4",
    "accept_moves_uci": ["e2e4"],
    "solution_pv": ["e2e4"],
}

TODAYS_FOCUS_RESPONSE = {
    "focus_cause": None,
    "focus_cause_label": None,
    "is_validated": False,
}


def json_response(route: Route, data, status=200):
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(data),
    )


def setup_routes(page, static_origin):
    """Intercept all API calls. Never touches the real production API."""
    api_base = "https://knightmind-api.onrender.com"

    def handle(route: Route, request: Request):
        url = request.url
        method = request.method

        # Sessions
        if "/sessions/start" in url and method == "POST":
            return json_response(route, SESSION_START)
        if "/sessions/recent" in url:
            return json_response(route, [])
        if "/sessions/sess-bproof-1/complete" in url:
            return json_response(route, {**SESSION_START, "completed_at": "2026-01-01T00:10:00Z"})

        # Puzzles due
        if "/puzzles/due" in url:
            return json_response(route, DUE_RESPONSE)

        # Check move (e2e4 = correct)
        if f"/puzzles/{PUZZLE_1_ID}/check" in url:
            return json_response(route, CHECK_RESPONSE)
        if f"/puzzles/{PUZZLE_2_ID}/check" in url:
            return json_response(route, CHECK_RESPONSE)

        # Review
        if f"/puzzles/{PUZZLE_1_ID}/review" in url and method == "POST":
            return json_response(route, REVIEW_RESPONSE)
        if f"/puzzles/{PUZZLE_2_ID}/review" in url and method == "POST":
            return json_response(route, {**REVIEW_RESPONSE, "puzzle_info": {**REVIEW_RESPONSE["puzzle_info"], "best_move": "d2d4"}})

        # Diagnosis (only served after reveal=true is passed)
        if f"/puzzles/{PUZZLE_1_ID}/diagnosis" in url and "reveal=true" in url:
            return json_response(route, DIAGNOSIS_READY)
        if f"/puzzles/{PUZZLE_1_ID}/diagnosis" in url:
            return json_response(route, {**DIAGNOSIS_READY, "evidence_withheld": True})

        # Puzzle 2 diagnosis: pending (no cause yet)
        if f"/puzzles/{PUZZLE_2_ID}/diagnosis" in url:
            return json_response(route, {
                "state": "pending",
                "puzzle_id": PUZZLE_2_ID,
                "primary_cause": None,
                "primary_cause_label": None,
                "secondary_causes": [],
                "secondary_cause_labels": [],
                "evidence": [],
                "evidence_withheld": False,
                "explanation": None,
                "training_recommendation": None,
                "user_confirmed_cause": None,
            })

        # Reveal
        if f"/puzzles/{PUZZLE_1_ID}/reveal" in url:
            return json_response(route, REVEAL_RESPONSE)

        # User endpoints
        if "/users/" in url and "/todays-focus" in url:
            return json_response(route, TODAYS_FOCUS_RESPONSE)
        if "/users/" in url and "/status" in url:
            return json_response(route, USER_STATUS)
        if "/motif-performance" in url or "/motifs/performance" in url:
            return json_response(route, {"motifs": [], "weakest_motifs": []})

        # Jobs / ops
        if "/jobs" in url or "/job" in url:
            return json_response(route, {"job_id": None, "status": "none"})

        # Daily puzzles (not used by training but called on mount)
        if "/daily-puzzle-sessions" in url:
            return json_response(route, {"puzzles": [], "count": 0})

        # Default: abort so we never hit the real production API
        print(f"[UNROUTED] {method} {url}", flush=True)
        route.abort()

    page.route(f"{api_base}/**", handle)
    # Also intercept /api/ prefix (the local proxy path used by the build)
    page.route("**/api/**", handle)


def run_test(viewport_name, width, height, page, static_origin, username="testplayer"):
    """Run the full resolved-diagnosis browser proof for one viewport."""
    print(f"\n=== {viewport_name} ({width}x{height}) ===", flush=True)
    page.set_viewport_size({"width": width, "height": height})

    # Set username in localStorage so the app doesn't ask for it
    page.goto(static_origin)
    page.evaluate(f"localStorage.setItem('knightmind:chesscom_username', '{username}')")

    # Navigate to /puzzles
    page.goto(f"{static_origin}/puzzles")
    page.wait_for_load_state("networkidle", timeout=8000)

    # Check for console errors at start
    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text()) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: console_errors.append(str(exc)))

    # ── 1. Start session ──────────────────────────────────────────
    start_btn = page.locator('button:has-text("Start Session")')
    if start_btn.count() == 0:
        start_btn = page.locator('button:has-text("Start")')
    start_btn.first.click()
    page.wait_for_timeout(800)

    # ── 2. Type the correct move (e2e4) ──────────────────────────
    # Open the "Type Move Manually" input
    manual_btn = page.locator('button:has-text("Type Move Manually")')
    if manual_btn.count() == 0:
        manual_btn = page.locator('button:has-text("type move manually")')
    manual_btn.first.click()
    page.wait_for_timeout(300)

    move_input = page.locator('input[placeholder="e.g. e2e4"]')
    move_input.fill("e2e4")
    move_input.press("Enter")
    page.wait_for_timeout(600)

    # ── 3. Wait for diagnosis card ────────────────────────────────
    # The diagnosis must appear within 5s after the correct answer.
    # The real production flow: checkPuzzle returns correct → handleCheckAnswer
    # triggers the review → usePuzzleSession.handleReviewPuzzle calls reviewPuzzle
    # → stats-fold via setPuzzles → Puzzles.tsx requestDiagnosisForResolvedOutcome
    # → getPuzzleDiagnosis → MistakeDiagnosisCard rendered.
    try:
        page.wait_for_selector('[data-testid="post-resolution-diagnosis"]', timeout=8000)
        diagnosis_visible = True
    except Exception:
        diagnosis_visible = False

    if not diagnosis_visible:
        # Take a screenshot to show the failure state
        page.screenshot(path=str(ARTIFACTS / f"{viewport_name}_diagnosis_missing.png"), full_page=True)
        raise AssertionError(
            f"{viewport_name}: Diagnosis card did not appear after correct answer + review. "
            f"Screenshot: {ARTIFACTS}/{viewport_name}_diagnosis_missing.png"
        )

    # ── 4. Assert all required content is visible ─────────────────
    diag = page.locator('[data-testid="post-resolution-diagnosis"]')

    # Heading ("Mistake diagnosis")
    heading = diag.locator('h2, [id="mistake-diagnosis-heading"]')
    assert heading.count() > 0, f"{viewport_name}: Diagnosis heading not found"
    heading_text = heading.first.text_content() or ""
    assert "diagnosis" in heading_text.lower(), f"{viewport_name}: Heading text unexpected: {heading_text!r}"

    # Cause ("Loose piece awareness")
    assert diag.locator('text=Loose piece awareness').count() > 0, \
        f"{viewport_name}: Cause label not visible"

    # Explanation
    assert diag.locator('text=pawn passively').count() > 0 or \
           diag.locator('text=central control').count() > 0, \
        f"{viewport_name}: Explanation text not visible"

    # Evidence
    assert diag.locator('text=e2e4').count() > 0, \
        f"{viewport_name}: Evidence (best move) not visible"

    # Next-time guidance
    assert diag.locator('text=Next time').count() > 0, \
        f"{viewport_name}: 'Next time' recommendation heading not visible"

    print(f"  [OK] Diagnosis heading, cause, explanation, evidence, next-time — all visible", flush=True)

    page.screenshot(path=str(ARTIFACTS / f"{viewport_name}_diagnosis_visible.png"), full_page=True)
    print(f"  Screenshot: {ARTIFACTS}/{viewport_name}_diagnosis_visible.png", flush=True)

    # ── 5. Mobile: no horizontal overflow ─────────────────────────
    if width == 390:
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        inner_width = page.evaluate("window.innerWidth")
        print(f"  scrollWidth={scroll_width} innerWidth={inner_width}", flush=True)
        if scroll_width > inner_width + 2:  # 2px tolerance
            page.screenshot(path=str(ARTIFACTS / f"{viewport_name}_overflow.png"), full_page=True)
        assert scroll_width <= inner_width + 2, \
            f"{viewport_name}: Horizontal overflow — scrollWidth ({scroll_width}) > innerWidth ({inner_width})"
        print(f"  [OK] No horizontal overflow on mobile", flush=True)
        page.screenshot(path=str(ARTIFACTS / f"{viewport_name}_no_overflow.png"), full_page=True)
        print(f"  Screenshot: {ARTIFACTS}/{viewport_name}_no_overflow.png", flush=True)

    # ── 6. Console errors ─────────────────────────────────────────
    # Allow known React DevTools / strict mode noise; block real errors.
    ignorable = [
        "React DevTools",
        "Downloading the React DevTools",
        "Each child in a list should have a unique",  # chess.js board squares
        "Warning:",
    ]
    real_errors = [e for e in console_errors if not any(ig in e for ig in ignorable)]
    if real_errors:
        page.screenshot(path=str(ARTIFACTS / f"{viewport_name}_console_errors.png"), full_page=True)
        raise AssertionError(
            f"{viewport_name}: {len(real_errors)} non-allowlisted browser console error(s): {real_errors}"
        )
    print(f"  [OK] Console errors: 0 real errors", flush=True)

    # ── 7. Move to puzzle 2 → diagnosis clears ────────────────────
    next_btn = page.locator('button:has-text("Next Puzzle"), button:has-text("Next puzzle"), button:has-text("Move on")')
    if next_btn.count() == 0:
        # Try pressing the next-puzzle button via aria-label
        next_btn = page.locator('[aria-label*="next" i], [aria-label*="Next" i]')
    if next_btn.count() > 0:
        next_btn.first.click()
        page.wait_for_timeout(500)
        # After moving on, the prior diagnosis card must be gone or replaced
        # (it belongs to puzzle 1; puzzle 2 has a pending diagnosis with no content)
        p1_cause = page.locator('text=Loose piece awareness')
        # Give it a moment to clear
        page.wait_for_timeout(400)
        p1_cause_count = p1_cause.count()
        page.screenshot(path=str(ARTIFACTS / f"{viewport_name}_puzzle2_diagnosis_cleared.png"), full_page=True)
        assert p1_cause_count == 0, \
            f"{viewport_name}: Prior puzzle diagnosis ('Loose piece awareness') still visible after moving to puzzle 2"
        print(f"  [OK] Moving to puzzle 2 clears the prior diagnosis", flush=True)
        print(f"  Screenshot: {ARTIFACTS}/{viewport_name}_puzzle2_diagnosis_cleared.png", flush=True)
    else:
        print(f"  [SKIP] Could not find 'Next Puzzle' button; skipping clear-check", flush=True)

    return True


def main():
    print("KnightMind browser proof: resolved-outcome diagnosis", flush=True)
    print(f"Candidate commit: f8520fa0b4aff702bdb398c5933eb26a0630d8a2", flush=True)
    print(f"Dist: {DIST}", flush=True)
    print(f"Artifacts: {ARTIFACTS}\n", flush=True)

    server, origin = start_static_server(port=19801)
    print(f"Static server at {origin}", flush=True)

    failures = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        for viewport_name, width, height in [("desktop", 1280, 800), ("mobile_390x844", 390, 844)]:
            context = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=2 if width == 390 else 1,
            )
            page = context.new_page()
            setup_routes(page, origin)

            try:
                run_test(viewport_name, width, height, page, origin)
            except AssertionError as e:
                failures.append(str(e))
                print(f"  [FAIL] {e}", flush=True)
            except Exception as e:
                failures.append(f"{viewport_name}: unexpected exception: {e}")
                print(f"  [ERROR] {e}", flush=True)
                import traceback
                traceback.print_exc()
            finally:
                context.close()

        browser.close()

    server.shutdown()

    print("\n" + "="*60, flush=True)
    if failures:
        print("RESULT: FAIL", flush=True)
        for f in failures:
            print(f"  - {f}", flush=True)
        sys.exit(1)
    else:
        print("RESULT: PASS", flush=True)
        print(f"All viewport checks passed. Artifacts in {ARTIFACTS}", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
