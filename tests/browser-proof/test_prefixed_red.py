"""
RED test: runs the same browser proof against the pre-fix bundle (392e7c8).
Expected to FAIL (diagnosis card does not appear after same-id stats-fold).
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

PRE_FIX_DIST = pathlib.Path("/tmp/knightmind-prefix-dist")

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PUZZLE_1_ID = "bproof-p1"
PUZZLE_2_ID = "bproof-p2"

PUZZLE_1 = {
    "id": PUZZLE_1_ID, "display_name": "BProof Test Puzzle", "username": "testplayer",
    "source_game_id": "g1", "ply": 10, "fen": START_FEN, "side_to_move": "white",
    "played_move_uci": "e2e3", "best_move_uci": "e2e4",
    "eval_before": 0.5, "eval_after": -0.5, "swing": 1.0,
    "created_at": "2026-01-01T00:00:00Z", "used_on": None, "attempts": 0, "pass_count": 0, "fail_count": 0,
}
PUZZLE_2 = {
    "id": PUZZLE_2_ID, "display_name": "BProof Test Puzzle 2", "username": "testplayer",
    "source_game_id": "g2", "ply": 12, "fen": START_FEN, "side_to_move": "white",
    "played_move_uci": "d2d3", "best_move_uci": "d2d4",
    "eval_before": 0.3, "eval_after": -0.3, "swing": 0.6,
    "created_at": "2026-01-01T00:00:00Z", "used_on": None, "attempts": 0, "pass_count": 0, "fail_count": 0,
}
DUE_RESPONSE = {"due_count": 2, "returned_count": 2, "now": "2026-01-01T00:00:00Z", "puzzles": [PUZZLE_1, PUZZLE_2]}
CHECK_RESPONSE = {"correct": True, "result": "pass", "complete": True, "reply": None, "next_ply_index": None}
REVIEW_RESPONSE = {
    "next_due_at": "2026-01-08T00:00:00Z", "interval_days": 7, "ease_factor": 2.5,
    "feedback": "Good work!", "result": "pass", "verified": True, "source": "server_verified",
    "puzzle_info": {"fen": START_FEN, "best_move": "e2e4", "side_to_move": "white", "swing": 1.0},
    "stats": {"attempts": 1, "pass_count": 1, "fail_count": 0, "last_reviewed_at": "2026-01-01T00:01:00Z", "last_result": "pass"},
}
DIAGNOSIS_READY = {
    "state": "ready", "puzzle_id": PUZZLE_1_ID,
    "primary_motif": "hanging_queen", "primary_cause": "loose_piece_awareness",
    "primary_cause_label": "Loose piece awareness",
    "secondary_causes": [], "secondary_cause_labels": [], "phase": "middlegame",
    "evidence": [
        {"id": "best.move", "label": "Best move", "value": "e2e4 (forcing)"},
        {"id": "eval.swing", "label": "Evaluation swing (pawns)", "value": "1.00"},
    ],
    "evidence_withheld": False,
    "explanation": "You moved the pawn passively instead of taking central control.",
    "training_recommendation": "Practice recognising central pawn breaks in the opening.",
    "user_confirmed_cause": None, "source": "rules", "diagnosed_at": "2026-01-01T00:00:00Z",
}
SESSION_START = {"session_id": "sess-bproof-1", "requested_n": 5, "session_type": "standard"}
USER_STATUS = {"games_count": 10, "puzzles_count": 5, "due_count": 3, "has_new_games": False}
REVEAL_RESPONSE = {"best_move_uci": "e2e4", "accept_moves_uci": ["e2e4"], "solution_pv": ["e2e4"]}
TODAYS_FOCUS_RESPONSE = {"focus_cause": None, "focus_cause_label": None, "is_validated": False}


class SPAHandler(http.server.BaseHTTPRequestHandler):
    DIST = PRE_FIX_DIST
    def log_message(self, *args): pass
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/": path = "/index.html"
        target = self.DIST / path.lstrip("/")
        if target.exists() and target.is_file():
            data = target.read_bytes()
            ct = ("text/html" if path.endswith(".html") else
                  "application/javascript" if path.endswith(".js") else
                  "text/css" if path.endswith(".css") else "application/octet-stream")
            self.send_response(200); self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)
        else:
            data = (self.DIST / "index.html").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)


def start_static_server(port=19802):
    server = http.server.HTTPServer(("127.0.0.1", port), SPAHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://127.0.0.1:{port}"


def json_response(route, data, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(data))


def setup_routes(page, static_origin):
    api_base = "https://knightmind-api.onrender.com"
    def handle(route, request):
        url = request.url; method = request.method
        if "/sessions/start" in url and method == "POST": return json_response(route, SESSION_START)
        if "/sessions/recent" in url: return json_response(route, [])
        if "/sessions/sess-bproof-1/complete" in url: return json_response(route, {**SESSION_START, "completed_at": "2026-01-01T00:10:00Z"})
        if "/puzzles/due" in url: return json_response(route, DUE_RESPONSE)
        if f"/puzzles/{PUZZLE_1_ID}/check" in url: return json_response(route, CHECK_RESPONSE)
        if f"/puzzles/{PUZZLE_2_ID}/check" in url: return json_response(route, CHECK_RESPONSE)
        if f"/puzzles/{PUZZLE_1_ID}/review" in url and method == "POST": return json_response(route, REVIEW_RESPONSE)
        if f"/puzzles/{PUZZLE_2_ID}/review" in url and method == "POST": return json_response(route, REVIEW_RESPONSE)
        if f"/puzzles/{PUZZLE_1_ID}/diagnosis" in url and "reveal=true" in url: return json_response(route, DIAGNOSIS_READY)
        if f"/puzzles/{PUZZLE_1_ID}/diagnosis" in url: return json_response(route, {**DIAGNOSIS_READY, "evidence_withheld": True})
        if f"/puzzles/{PUZZLE_2_ID}/diagnosis" in url: return json_response(route, {"state": "pending", "puzzle_id": PUZZLE_2_ID, "primary_cause": None, "primary_cause_label": None, "secondary_causes": [], "secondary_cause_labels": [], "evidence": [], "evidence_withheld": False, "explanation": None, "training_recommendation": None, "user_confirmed_cause": None})
        if f"/puzzles/{PUZZLE_1_ID}/reveal" in url: return json_response(route, REVEAL_RESPONSE)
        if "/users/" in url and "/todays-focus" in url: return json_response(route, TODAYS_FOCUS_RESPONSE)
        if "/users/" in url and "/status" in url: return json_response(route, USER_STATUS)
        if "/motif-performance" in url or "/motifs/performance" in url: return json_response(route, {"motifs": [], "weakest_motifs": []})
        if "/jobs" in url or "/job" in url: return json_response(route, {"job_id": None, "status": "none"})
        if "/daily-puzzle-sessions" in url: return json_response(route, {"puzzles": [], "count": 0})
        route.abort()
    page.route(f"{api_base}/**", handle)
    page.route("**/api/**", handle)


def main():
    print("KnightMind browser proof: RED test (pre-fix 392e7c8)", flush=True)
    print("Expected: diagnosis card does NOT appear (that is the bug being fixed)", flush=True)

    server, origin = start_static_server(port=19802)
    print(f"Pre-fix static server at {origin}", flush=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        setup_routes(page, origin)

        page.goto(origin)
        page.evaluate("localStorage.setItem('knightmind:chesscom_username', 'testplayer')")
        page.goto(f"{origin}/puzzles")
        page.wait_for_load_state("networkidle", timeout=8000)

        start_btn = page.locator('button:has-text("Start Session")')
        if start_btn.count() == 0:
            start_btn = page.locator('button:has-text("Start")')
        start_btn.first.click()
        page.wait_for_timeout(800)

        manual_btn = page.locator('button:has-text("Type Move Manually")')
        manual_btn.first.click()
        page.wait_for_timeout(300)

        move_input = page.locator('input[placeholder="e.g. e2e4"]')
        move_input.fill("e2e4")
        move_input.press("Enter")

        # Wait 6s — on the pre-fix code the diagnosis never appears
        page.wait_for_timeout(6000)

        diagnosis_present = page.locator('[data-testid="post-resolution-diagnosis"]').count() > 0
        loose_present = page.locator('text=Loose piece awareness').count() > 0

        page.screenshot(path=str(ARTIFACTS / "prefixed_desktop_state.png"), full_page=True)
        print(f"  Screenshot: {ARTIFACTS}/prefixed_desktop_state.png", flush=True)

        context.close()
        browser.close()

    server.shutdown()

    result_path = ARTIFACTS / "prefixed_fails.txt"
    if not diagnosis_present and not loose_present:
        result_path.write_text(
            "RED CONFIRMED: pre-fix 392e7c8 — diagnosis card absent after correct answer + review (stats-fold bug).\n"
            "diagnosis_present=False, loose_piece_awareness_visible=False\n"
            "Screenshot: prefixed_desktop_state.png\n"
        )
        print("\nRED CONFIRMED: Diagnosis card absent on pre-fix build (expected).", flush=True)
        print(f"Evidence: {result_path}", flush=True)
        sys.exit(0)  # RED is the expected result for this test
    else:
        result_path.write_text(
            f"UNEXPECTED: pre-fix 392e7c8 showed diagnosis_present={diagnosis_present}, loose={loose_present}.\n"
            "The bug may have been fixed earlier than expected, or the intercepts differ.\n"
        )
        print(f"\nUNEXPECTED: pre-fix bundle showed diagnosis. Investigate. {result_path}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
