"""Tenant-isolation suite — run with KNIGHTMIND_REQUIRE_AUTH ON.

Two accounts, A (owns "alice") and B (owns "bob"), plus data owned by alice.
Every check asserts that B (or an anonymous caller) cannot read, mutate, cancel,
or review A's data, that ID-addressed routes return 404 (never confirming a
foreign id), and that credentials are validated (missing/invalid/expired → 401).

Flag-OFF behavior (the live app is unaffected on merge) is proven separately by
the full existing services/api suite, which stays green with the flag unset.
"""

import time
import uuid
from datetime import datetime, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from services.api import security
from services.api.security import create_access_token, hash_password

SECRET = "isolation-suite-secret-0123456789-abcdefghij"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed(session):
    """Seed accounts A/B and a full set of alice-owned rows. Returns an id map."""
    from services.api.models import (
        Account,
        AccountChessUsername,
        Game,
        ImportSummary,
        Job,
        JobStatus,
        Puzzle,
        PuzzleResult,
        PuzzleReview,
        PuzzleStats,
        RatingSnapshot,
        TrainingSession,
    )

    now = datetime.now(timezone.utc)

    account_a = Account(email="alice@example.com", password_hash=hash_password("pw-a"))
    account_b = Account(email="bob@example.com", password_hash=hash_password("pw-b"))
    session.add_all([account_a, account_b])
    session.flush()

    session.add_all(
        [
            AccountChessUsername(account_id=account_a.id, username="alice"),
            AccountChessUsername(account_id=account_b.id, username="bob"),
        ]
    )

    session.add(
        Game(
            game_id="g-alice",
            url="https://chess.com/game/g-alice",
            username="alice",
            white_username="alice",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=int(now.timestamp()),
            rated=True,
            pgn_blob='[Event "Test"]\n[WhiteElo "1500"]\n[BlackElo "1520"]\n\n1. e4 e5 1-0',
        )
    )
    session.add(
        Puzzle(
            id="p-alice",
            username="alice",
            source_game_id="g-alice",
            ply=1,
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.2,
            eval_after=-1.0,
            swing=1.2,
        )
    )
    session.add(
        PuzzleStats(
            puzzle_id="p-alice",
            username="alice",
            title="Alice puzzle",
            primary_motif="Fork",
            attempts=2,
            pass_count=1,
            fail_count=1,
            last_reviewed_at=now,
        )
    )
    session.add(
        PuzzleReview(
            puzzle_id="p-alice",
            username="alice",
            result=PuzzleResult.PASS,
        )
    )

    alice_job = Job(username="alice", status=JobStatus.QUEUED, message="queued")
    alice_session = TrainingSession(
        id=str(uuid.uuid4()), username="alice", requested_n=5
    )
    session.add_all([alice_job, alice_session])

    session.add(
        RatingSnapshot(
            username="alice",
            source="chesscom",
            time_control="rapid",
            rating=1500,
            recorded_at=now,
        )
    )
    session.add(ImportSummary(username="alice", last_imported_at=now, last_new_games=3))
    session.flush()

    return {
        "account_a": account_a.id,
        "account_b": account_b.id,
        "email_a": account_a.email,
        "email_b": account_b.email,
        "alice_job": alice_job.id,
        "alice_session": alice_session.id,
    }


@pytest.fixture
def iso(monkeypatch, tmp_path):
    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    monkeypatch.setenv("KNIGHTMIND_JWT_SECRET", SECRET)

    db_path = tmp_path / f"iso_{uuid.uuid4()}.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from services.api import db as db_module
    from services.api import worker as worker_module

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(worker_module, "SessionLocal", TestingSessionLocal)

    from services.api.models import Base

    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    ctx = _seed(session)
    session.commit()

    from services.api.db import get_db
    from services.api.main import app

    app.dependency_overrides[get_db] = lambda: session

    token_a = create_access_token(ctx["account_a"], ctx["email_a"])
    token_b = create_access_token(ctx["account_b"], ctx["email_b"])

    try:
        with TestClient(app) as client:
            yield {
                "client": client,
                "session": session,
                "ctx": ctx,
                "token_a": token_a,
                "token_b": token_b,
            }
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


# --- 1. Read another user's data → 403 --------------------------------------

READ_ROUTES_ALICE = [
    "/users/alice/status",
    "/users/alice/motifs/performance",
    "/users/alice/dashboard",
    "/users/alice/trends",
    "/users/alice/puzzles/tricky",
    "/openings?username=alice",
    "/import/status?username=alice",
    "/puzzles/due?username=alice",
    "/puzzles/list?username=alice",
    "/puzzles/p-alice?username=alice",
    "/ratings/history?username=alice",
    "/ratings/explain?username=alice",
    "/sessions/recent?username=alice",
]


@pytest.mark.parametrize("route", READ_ROUTES_ALICE)
def test_b_cannot_read_alice(iso, route):
    resp = iso["client"].get(route, headers=_auth(iso["token_b"]))
    assert resp.status_code == 403, f"{route} -> {resp.status_code}"


@pytest.mark.parametrize("route", READ_ROUTES_ALICE)
def test_owner_can_read_own(iso, route):
    resp = iso["client"].get(route, headers=_auth(iso["token_a"]))
    # Owner is authorized; may be 200 or a domain 404 (no data), never 401/403.
    assert resp.status_code not in (401, 403), f"{route} -> {resp.status_code}"


# --- 2. Modify another user's data → 403 ------------------------------------


def test_b_cannot_review_alice_puzzle(iso):
    resp = iso["client"].post(
        "/puzzles/p-alice/review",
        json={"username": "alice", "result": "pass"},
        headers=_auth(iso["token_b"]),
    )
    assert resp.status_code == 403


def test_b_cannot_check_alice_puzzle(iso):
    resp = iso["client"].post(
        "/puzzles/p-alice/check",
        json={"username": "alice", "attempted_move": "d2d4"},
        headers=_auth(iso["token_b"]),
    )
    assert resp.status_code == 403


def test_b_cannot_reveal_alice_puzzle(iso):
    resp = iso["client"].post(
        "/puzzles/p-alice/reveal",
        json={"username": "alice"},
        headers=_auth(iso["token_b"]),
    )
    assert resp.status_code == 403


def test_owner_can_check_own_puzzle(iso):
    resp = iso["client"].post(
        "/puzzles/p-alice/check",
        json={"username": "alice", "attempted_move": "d2d4"},
        headers=_auth(iso["token_a"]),
    )
    assert resp.status_code == 200
    assert resp.json()["correct"] is True


def test_owner_reveal_does_not_leak_via_due(iso, monkeypatch):
    """The scored /puzzles/due payload must not carry the solution even for the
    owner — the answer is only available through the explicit reveal path.

    Asserts the strict anti-cheat mode (KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS on)."""
    monkeypatch.setenv("KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS", "true")
    resp = iso["client"].get(
        "/puzzles/due?username=alice", headers=_auth(iso["token_a"])
    )
    assert resp.status_code == 200
    for p in resp.json()["puzzles"]:
        assert "best_move_uci" not in p
        assert "accept_moves_uci" not in p


def test_b_cannot_create_alice_daily_session(iso):
    resp = iso["client"].post(
        "/daily-puzzle-sessions",
        json={"username": "alice", "n": 5},
        headers=_auth(iso["token_b"]),
    )
    assert resp.status_code == 403


def test_b_cannot_snapshot_alice(iso):
    resp = iso["client"].post(
        "/ratings/snapshot",
        json={"username": "alice", "time_control": "rapid"},
        headers=_auth(iso["token_b"]),
    )
    assert resp.status_code == 403


def test_b_cannot_start_alice_session(iso):
    resp = iso["client"].post(
        "/sessions/start",
        json={"username": "alice", "n": 5},
        headers=_auth(iso["token_b"]),
    )
    assert resp.status_code == 403


def test_b_cannot_import_alice_handle(iso):
    # "alice" is already owned by A → B's import is a claim conflict → 403.
    resp = iso["client"].post(
        "/import/chesscom?username=alice", headers=_auth(iso["token_b"])
    )
    assert resp.status_code == 403


# --- 3. Cancel/complete another user's work (id-addressed) → 404 ------------


def test_b_cannot_get_alice_job(iso):
    resp = iso["client"].get(
        f"/jobs/{iso['ctx']['alice_job']}", headers=_auth(iso["token_b"])
    )
    assert resp.status_code == 404


def test_b_cannot_cancel_alice_job(iso):
    resp = iso["client"].post(
        f"/jobs/{iso['ctx']['alice_job']}/cancel", headers=_auth(iso["token_b"])
    )
    assert resp.status_code == 404


def test_b_cannot_get_alice_session(iso):
    resp = iso["client"].get(
        f"/sessions/{iso['ctx']['alice_session']}", headers=_auth(iso["token_b"])
    )
    assert resp.status_code == 404


def test_b_cannot_complete_alice_session(iso):
    resp = iso["client"].post(
        f"/sessions/{iso['ctx']['alice_session']}/complete",
        json={"username": "alice"},
        headers=_auth(iso["token_b"]),
    )
    assert resp.status_code == 404


def test_b_cannot_use_hint_alice_session(iso):
    resp = iso["client"].post(
        f"/sessions/{iso['ctx']['alice_session']}/use_hint",
        json={"username": "alice"},
        headers=_auth(iso["token_b"]),
    )
    assert resp.status_code == 404


# --- 4. IDOR by guessed id → 404 --------------------------------------------


def test_idor_guessed_job_id_404(iso):
    resp = iso["client"].get(f"/jobs/{uuid.uuid4()}", headers=_auth(iso["token_b"]))
    assert resp.status_code == 404


def test_idor_guessed_session_id_404(iso):
    resp = iso["client"].get(f"/sessions/{uuid.uuid4()}", headers=_auth(iso["token_b"]))
    assert resp.status_code == 404


# --- 5. ratings/explain cross-session leak → 404 ----------------------------


def test_explain_cross_session_id_404(iso):
    # B owns bob, so username=bob passes ownership; but the session id belongs to
    # alice → the session-ownership guard 404s without leaking the window.
    resp = iso["client"].get(
        f"/ratings/explain?username=bob&since_session_id={iso['ctx']['alice_session']}",
        headers=_auth(iso["token_b"]),
    )
    assert resp.status_code == 404


# --- 6. Username normalization / spoofing -----------------------------------


@pytest.mark.parametrize("handle", ["alice", "Alice", "ALICE", " alice "])
def test_owner_access_is_case_and_whitespace_insensitive(iso, handle):
    resp = iso["client"].get(
        "/puzzles/list", params={"username": handle}, headers=_auth(iso["token_a"])
    )
    assert resp.status_code == 200


def test_cyrillic_homoglyph_is_rejected(iso):
    # 'аlice' with a Cyrillic 'а' must NOT match A's Latin 'alice' claim. It is
    # now rejected at the canonicalization boundary (422, non-ASCII handle)
    # before ownership is even evaluated, so it can neither borrow A's data nor
    # silently spin up a distinct empty user.
    resp = iso["client"].get(
        "/puzzles/list", params={"username": "аlice"}, headers=_auth(iso["token_a"])
    )
    assert resp.status_code == 422


# --- 7. Missing / invalid credentials → 401 ---------------------------------


def test_missing_token_401(iso):
    assert iso["client"].get("/users/alice/status").status_code == 401


@pytest.mark.parametrize("header", ["Bearer", "Bearer ", "Basic abc", "token xyz"])
def test_malformed_authorization_401(iso, header):
    resp = iso["client"].get("/users/alice/status", headers={"Authorization": header})
    assert resp.status_code == 401


def test_expired_token_401(iso):
    now = int(time.time())
    token = jwt.encode(
        {"sub": iso["ctx"]["account_a"], "iat": now - 120, "exp": now - 60},
        SECRET,
        algorithm=security.JWT_ALGORITHM,
    )
    resp = iso["client"].get("/users/alice/status", headers=_auth(token))
    assert resp.status_code == 401


def test_tampered_token_401(iso):
    token = iso["token_a"]
    tampered = token[:-3] + ("abc" if token[-3:] != "abc" else "xyz")
    resp = iso["client"].get("/users/alice/status", headers=_auth(tampered))
    assert resp.status_code == 401


def test_disabled_account_token_401(iso):
    from services.api.models import Account

    account = iso["session"].get(Account, iso["ctx"]["account_a"])
    account.disabled = True
    iso["session"].commit()
    resp = iso["client"].get("/users/alice/status", headers=_auth(iso["token_a"]))
    assert resp.status_code == 401


def test_unknown_subject_token_401(iso):
    token = create_access_token("no-such-account", "ghost@example.com")
    resp = iso["client"].get("/users/alice/status", headers=_auth(token))
    assert resp.status_code == 401


# --- 8. Ordinary account cannot reach the operator surface → 404 ------------


def test_account_token_cannot_hit_ops_status(iso):
    # /ops/status is tailnet-gated (require_operator); a bearer token carries no
    # Tailscale identity header, so it must still 404.
    resp = iso["client"].get("/ops/status", headers=_auth(iso["token_a"]))
    assert resp.status_code == 404


# --- 9. Claim semantics (first-importer-wins) -------------------------------


def test_first_import_claims_then_conflicts(iso, monkeypatch):
    from services.api import main as main_module

    async def _empty_import(username, since=None):
        # An async generator that yields no games.
        return
        yield  # pragma: no cover

    monkeypatch.setattr(main_module, "import_all_games", _empty_import)

    client = iso["client"]

    # A imports the unowned handle "carol" → claims it.
    r1 = client.post("/import/chesscom?username=carol", headers=_auth(iso["token_a"]))
    assert r1.status_code == 200

    # B now tries the same handle → claim conflict → 403.
    r2 = client.post("/import/chesscom?username=carol", headers=_auth(iso["token_b"]))
    assert r2.status_code == 403

    # A re-imports its own claimed handle → still fine.
    r3 = client.post("/import/chesscom?username=carol", headers=_auth(iso["token_a"]))
    assert r3.status_code == 200


# --- 10. Login + /auth/me ----------------------------------------------------


def test_login_success_and_me(iso):
    client = iso["client"]
    login = client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "pw-a"}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = client.get("/auth/me", headers=_auth(token))
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "alice@example.com"
    assert body["usernames"] == ["alice"]


def test_login_wrong_password_401(iso):
    resp = iso["client"].post(
        "/auth/login", json={"email": "alice@example.com", "password": "nope"}
    )
    assert resp.status_code == 401


def test_login_unknown_email_401(iso):
    resp = iso["client"].post(
        "/auth/login", json={"email": "ghost@example.com", "password": "whatever"}
    )
    assert resp.status_code == 401


def test_me_requires_token(iso):
    assert iso["client"].get("/auth/me").status_code == 401


# --- 11. Public routes stay open --------------------------------------------


@pytest.mark.parametrize("route", ["/", "/ops/ping"])
def test_public_routes_need_no_auth(iso, route):
    assert iso["client"].get(route).status_code == 200
