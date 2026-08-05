"""
Integration test for review endpoint with session tracking.
"""

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import services.api.main as main
from services.api.db import Base, get_db
from services.api.main import app
from services.api.models import Game, PuzzleReview, PuzzleStats, TrainingSession
from services.api.models import Puzzle as PuzzleModel


@pytest.fixture
def test_db(db_session):
    """The shared test session, under this module's historical fixture name."""
    return db_session


@pytest.fixture
def client(test_db):
    """Create a test client with the test database."""

    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _create_puzzle(
    db,
    puzzle_id: str,
    username: str,
    source_game_id: str,
    ply: int,
    accept_moves_uci: str | None = None,
):
    """Helper: create a Game + Puzzle in the DB.

    The puzzle FEN is the initial position and best_move_uci is "d2d4", so in
    tests: "d2d4" is legal+correct, "e2e4" is legal-but-wrong, and "e2e5" is
    illegal. Pass ``accept_moves_uci`` to widen the accepted-solution set.
    """
    existing_game = db.get(Game, (source_game_id, username))
    if not existing_game:
        db.add(
            Game(
                game_id=source_game_id,
                url=f"https://chess.com/game/{source_game_id}",
                username=username,
                white_username=username,
                black_username="opponent",
                white_result="win",
                black_result="lose",
                time_control="600",
                end_time=1704067200,
                rated=True,
            )
        )
    db.add(
        PuzzleModel(
            id=puzzle_id,
            username=username,
            source_game_id=source_game_id,
            ply=ply,
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            accept_moves_uci=accept_moves_uci,
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def test_review_endpoint_increments_session_counters(client, test_db):
    """
    Integration test: POST /puzzles/{id}/review increments session counters.
    """
    # 1. Create a training session
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="testuser",
        requested_n=5,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
    )
    test_db.add(session)
    test_db.commit()

    # 2. Create a puzzle for the user
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-1", 10)

    # 3. Submit a passing review
    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={
            "username": "testuser",
            "result": "pass",
            "time_spent_ms": 5000,
            "session_id": session_id,
        },
    )
    assert response.status_code == 200

    # 4. Verify counters were incremented
    stmt = select(TrainingSession).where(TrainingSession.id == session_id)
    updated_session = test_db.scalars(stmt).first()
    assert updated_session.pass_count == 1
    assert updated_session.fail_count == 0
    assert updated_session.total_time_ms == 5000
    assert updated_session.current_streak == 1
    assert updated_session.best_streak == 1

    # 5. Submit a failing review to reset streak
    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={
            "username": "testuser",
            "result": "fail",
            "time_spent_ms": 2000,
            "session_id": session_id,
        },
    )
    assert response.status_code == 200

    test_db.refresh(updated_session)
    assert updated_session.pass_count == 1
    assert updated_session.fail_count == 1
    assert updated_session.total_time_ms == 7000
    assert updated_session.current_streak == 0
    assert updated_session.best_streak == 1


def test_review_endpoint_idempotent_replay_with_client_key(client, test_db):
    """
    Regression: a retried / double-clicked review carrying the same
    client_review_id must be counted exactly ONCE.

    Before the idempotency fix, two identical POSTs recorded two review rows and
    double-counted attempts, pass_count, session pass/streak/time, and advanced
    scheduling twice (interval_days 1 -> 3).
    """
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="testuser",
        requested_n=5,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
    )
    test_db.add(session)
    test_db.commit()

    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-idem", 10)

    body = {
        "username": "testuser",
        "result": "pass",
        "time_spent_ms": 5000,
        "session_id": session_id,
        "client_review_id": "attempt-key-1",
    }

    r1 = client.post(f"/puzzles/{puzzle_id}/review", json=body)
    r2 = client.post(f"/puzzles/{puzzle_id}/review", json=body)  # double-submit
    assert r1.status_code == 200
    assert r2.status_code == 200
    # The replayed response mirrors the original outcome (scheduling not advanced)
    assert r2.json()["interval_days"] == r1.json()["interval_days"] == 1

    # Exactly one review row, counted once
    review_count = test_db.scalar(select(func.count()).select_from(PuzzleReview))
    assert review_count == 1

    stats = test_db.scalars(
        select(PuzzleStats).where(PuzzleStats.puzzle_id == puzzle_id)
    ).first()
    assert stats.attempts == 1
    assert stats.pass_count == 1
    assert stats.interval_days == 1  # NOT advanced to 3

    test_db.refresh(session)
    assert session.pass_count == 1
    assert session.current_streak == 1
    assert session.best_streak == 1
    assert session.total_time_ms == 5000


def test_review_endpoint_distinct_client_keys_both_count(client, test_db):
    """A genuinely new attempt (different client_review_id) is still recorded."""
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-idem2", 10)

    base = {"username": "testuser", "result": "pass", "time_spent_ms": 1000}
    client.post(f"/puzzles/{puzzle_id}/review", json={**base, "client_review_id": "k1"})
    client.post(f"/puzzles/{puzzle_id}/review", json={**base, "client_review_id": "k2"})

    review_count = test_db.scalar(select(func.count()).select_from(PuzzleReview))
    assert review_count == 2
    stats = test_db.scalars(
        select(PuzzleStats).where(PuzzleStats.puzzle_id == puzzle_id)
    ).first()
    assert stats.attempts == 2


def test_review_endpoint_empty_then_null_session_id_is_idempotent(client, test_db):
    """dim 14: an empty-string session_id then a NULL retry must replay, not 500.

    The unique index keys on COALESCE(session_id, ''), so "" and None collapse to
    the same value. Before the fix, _find_existing_review matched session_id with
    plain equality (IS NULL for None), so a first submit with session_id="" then a
    retry with session_id=null (same client_review_id) missed the replay fast path
    AND the IntegrityError-replay lookup, surfacing a 500 and never counting once.
    """
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-empty-null", 10)

    base = {
        "username": "testuser",
        "result": "pass",
        "time_spent_ms": 1000,
        "client_review_id": "empty-null-key",
    }

    # First submit carries an empty-string session_id.
    r1 = client.post(f"/puzzles/{puzzle_id}/review", json={**base, "session_id": ""})
    # Retry (same key) carries a NULL session_id.
    r2 = client.post(f"/puzzles/{puzzle_id}/review", json={**base, "session_id": None})

    assert r1.status_code == 200
    assert r2.status_code == 200  # not a 500

    # Counted exactly once across the two representations of "no session".
    review_count = test_db.scalar(select(func.count()).select_from(PuzzleReview))
    assert review_count == 1
    stats = test_db.scalars(
        select(PuzzleStats).where(PuzzleStats.puzzle_id == puzzle_id)
    ).first()
    assert stats.attempts == 1
    assert stats.pass_count == 1


def _make_race_find(monkeypatch):
    """Force the endpoint's *initial* replay SELECT to miss exactly once.

    Simulates a concurrent same-key submit that slips past the replay-before-
    mutate fast path (the winning row was not yet committed when this request's
    SELECT ran), so this request proceeds to INSERT and hits the unique index.
    Subsequent calls (the post-IntegrityError replay) use the real lookup.
    """
    import services.api.main as main_module

    real_find = main_module._find_existing_review
    state = {"calls": 0}

    def fake_find(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            return None  # pretend the winner's row is not visible yet
        return real_find(*args, **kwargs)

    monkeypatch.setattr(main_module, "_find_existing_review", fake_find)


def test_review_endpoint_concurrent_same_key_replays_with_session(
    client, test_db, monkeypatch
):
    """Concurrent same-key submit (session present) => graceful replay, no 500,
    no double-count. The unique index rejects the duplicate INSERT and the
    endpoint replays the winner's outcome."""
    session_id = str(uuid.uuid4())
    test_db.add(
        TrainingSession(
            id=session_id,
            username="testuser",
            requested_n=5,
            pass_count=0,
            fail_count=0,
            total_time_ms=0,
        )
    )
    test_db.commit()

    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-race", 10)

    body = {
        "username": "testuser",
        "result": "pass",
        "time_spent_ms": 5000,
        "session_id": session_id,
        "client_review_id": "race-key",
    }

    # Winner: first request records the review + stats + session counters.
    assert client.post(f"/puzzles/{puzzle_id}/review", json=body).status_code == 200

    # Loser: initial replay SELECT is forced to miss, so it tries to INSERT a
    # duplicate and must recover via IntegrityError -> replay.
    _make_race_find(monkeypatch)
    resp = client.post(f"/puzzles/{puzzle_id}/review", json=body)
    assert resp.status_code == 200  # graceful, not 500
    assert resp.json()["interval_days"] == 1  # scheduling NOT advanced to 3

    assert test_db.scalar(select(func.count()).select_from(PuzzleReview)) == 1
    stats = test_db.scalars(
        select(PuzzleStats).where(PuzzleStats.puzzle_id == puzzle_id)
    ).first()
    assert stats.attempts == 1
    assert stats.pass_count == 1
    assert stats.interval_days == 1

    session = test_db.get(TrainingSession, session_id)
    test_db.refresh(session)
    assert session.pass_count == 1
    assert session.current_streak == 1
    assert session.total_time_ms == 5000


def test_review_endpoint_concurrent_same_key_replays_session_less(
    client, test_db, monkeypatch
):
    """Concurrent same-key submit with NO session must also dedupe.

    Guards the COALESCE(session_id, '') index: a plain multi-column unique index
    treats each NULL session as distinct, so both session-less inserts would
    succeed and double-count. Here the duplicate INSERT must be rejected and the
    endpoint must replay the winner's outcome.
    """
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-race2", 10)

    body = {
        "username": "testuser",
        "result": "pass",
        "time_spent_ms": 3000,
        "client_review_id": "race-key-no-session",
    }

    assert client.post(f"/puzzles/{puzzle_id}/review", json=body).status_code == 200

    _make_race_find(monkeypatch)
    resp = client.post(f"/puzzles/{puzzle_id}/review", json=body)
    assert resp.status_code == 200  # graceful, not 500
    assert resp.json()["interval_days"] == 1

    assert test_db.scalar(select(func.count()).select_from(PuzzleReview)) == 1
    stats = test_db.scalars(
        select(PuzzleStats).where(PuzzleStats.puzzle_id == puzzle_id)
    ).first()
    assert stats.attempts == 1
    assert stats.pass_count == 1


def test_review_endpoint_rolls_back_atomically_on_failure(client, test_db, monkeypatch):
    """
    If the review flow fails midway (after the review row and session counters
    are staged but before scheduling is updated), NOTHING may be persisted.

    Regression test for a partial-commit bug: the storage helpers used to
    commit internally, so a failure in update_puzzle_stats persisted the
    review row and session counters while next_due_at/interval were lost.
    """
    # 1. Create a training session and a puzzle
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="testuser",
        requested_n=5,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
    )
    test_db.add(session)
    test_db.commit()

    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-atomic", 10)

    # 2. Make update_puzzle_stats fail midway through the flow
    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure during stats update")

    monkeypatch.setattr("services.api.main.update_puzzle_stats", boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        client.post(
            f"/puzzles/{puzzle_id}/review",
            json={
                "username": "testuser",
                "result": "pass",
                "time_spent_ms": 5000,
                "session_id": session_id,
            },
        )

    # 3. Discard any uncommitted (flushed-only) state, then verify that
    #    nothing was persisted: no review row, no stats row, counters intact.
    test_db.rollback()

    review_count = test_db.scalar(select(func.count()).select_from(PuzzleReview))
    assert review_count == 0

    stats_count = test_db.scalar(select(func.count()).select_from(PuzzleStats))
    assert stats_count == 0

    persisted_session = test_db.get(TrainingSession, session_id)
    assert persisted_session.pass_count == 0
    assert persisted_session.fail_count == 0
    assert persisted_session.total_time_ms == 0
    assert persisted_session.current_streak == 0
    assert persisted_session.best_streak == 0


def test_review_endpoint_session_not_found(client, test_db):
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-1", 10)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={
            "username": "testuser",
            "result": "pass",
            "session_id": "missing-session",
        },
    )

    assert response.status_code == 404
    assert "session not found" in response.json()["detail"].lower()


def test_review_endpoint_session_wrong_user(client, test_db):
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="owner",
        requested_n=5,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
    )
    test_db.add(session)
    test_db.commit()

    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-2", 12)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={"username": "testuser", "result": "pass", "session_id": session_id},
    )

    assert response.status_code == 403
    assert "different user" in response.json()["detail"].lower()


def test_review_endpoint_session_completed(client, test_db):
    session_id = str(uuid.uuid4())
    session = TrainingSession(
        id=session_id,
        username="testuser",
        requested_n=5,
        pass_count=0,
        fail_count=0,
        total_time_ms=0,
        completed_at=datetime.now(timezone.utc),
    )
    test_db.add(session)
    test_db.commit()

    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-3", 14)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={"username": "testuser", "result": "pass", "session_id": session_id},
    )

    assert response.status_code == 400
    assert "already completed" in response.json()["detail"].lower()


# ─── Server-verified training integrity (audit gate 7) ──────────────────────
#
# Before this gate the endpoint recorded the client's self-reported pass/fail
# verbatim, so a modified client could POST {"result": "pass"} with no (or a
# wrong) move and manufacture a perfect solve. These tests pin the server as the
# authority: when the played move is supplied, the SERVER decides the outcome.


def _get_review(db, puzzle_id, username):
    return db.scalars(
        select(PuzzleReview).where(
            PuzzleReview.puzzle_id == puzzle_id,
            PuzzleReview.username == username,
        )
    ).first()


def test_server_verifies_correct_move_as_pass(client, test_db):
    """Legal + correct move → server-verified pass."""
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-verify-1", 10)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={"username": "testuser", "result": "pass", "attempted_move": "d2d4"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "pass"
    assert body["verified"] is True
    assert body["source"] == "server_verified"

    review = _get_review(test_db, puzzle_id, "testuser")
    assert review.result == "pass"
    assert review.attempted_move == "d2d4"
    assert review.client_result == "pass"
    assert review.verified is True
    assert review.source == "server_verified"

    stats = test_db.get(PuzzleStats, puzzle_id)
    assert stats.pass_count == 1
    assert stats.fail_count == 0


def test_equivalent_accepted_move_is_a_pass(client, test_db):
    """A move in the multi-PV equivalence set (not the single best move) passes."""
    puzzle_id = str(uuid.uuid4())
    # best is d2d4; g1f3 is an accepted equivalent.
    _create_puzzle(
        test_db,
        puzzle_id,
        "testuser",
        "game-verify-2",
        10,
        accept_moves_uci="d2d4,g1f3",
    )

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={"username": "testuser", "result": "pass", "attempted_move": "g1f3"},
    )
    assert response.status_code == 200
    assert response.json()["result"] == "pass"

    review = _get_review(test_db, puzzle_id, "testuser")
    assert review.result == "pass"
    assert review.verified is True


def test_check_endpoint_accepts_equivalent_best_move(client, test_db):
    """dim 11: POST /puzzles/{id}/check honors the accepted-solution set, not just
    the single best_move_uci.

    Confirms #195's accept_moves + #200/#203 grading end-to-end on the live
    training-board path: an equivalent best move from the multi-PV set grades
    ``correct`` while a legal-but-wrong or illegal move does not. The /review
    path already proves this (test_equivalent_accepted_move_is_a_pass); this pins
    the same equivalence at the server-authoritative /check endpoint the Train
    board actually calls for live feedback.
    """
    puzzle_id = str(uuid.uuid4())
    # best is d2d4; g1f3 is an accepted multi-PV equivalent.
    _create_puzzle(
        test_db,
        puzzle_id,
        "testuser",
        "game-check-eq",
        10,
        accept_moves_uci="d2d4,g1f3",
    )

    def _check(move):
        return client.post(
            f"/puzzles/{puzzle_id}/check",
            json={"username": "testuser", "attempted_move": move},
        )

    # A legacy (NULL solution_pv) puzzle still grades against the accepted-solution
    # set, and its single correct move completes the puzzle. (The multi-move
    # response also carries reply/complete/next_ply_index; for a legacy puzzle a
    # correct move has no forced reply and completes immediately.)
    def _assert(move, *, correct):
        body = _check(move).json()
        assert body["correct"] is correct
        assert body["result"] == ("pass" if correct else "fail")
        # A single-move puzzle never streams a forced reply or a follow-up ply.
        assert body["reply"] is None
        assert body["next_ply_index"] is None
        assert body["complete"] is correct

    # Single best move -> correct.
    _assert("d2d4", correct=True)
    # Equivalent from the accept set (NOT the single best move) -> correct.
    _assert("g1f3", correct=True)
    # Legal but not in the accept set -> incorrect.
    _assert("e2e4", correct=False)
    # Illegal move -> incorrect.
    _assert("e2e5", correct=False)


def test_wrong_move_is_a_fail_even_when_client_claims_pass(client, test_db):
    """Reproduction + fix: a spoofed 'pass' with a wrong move is recorded FAIL.

    e2e4 is legal but not the solution. The client claims pass; the server
    overrides to fail and preserves the client's bogus claim for audit.
    """
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-verify-3", 10)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={"username": "testuser", "result": "pass", "attempted_move": "e2e4"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "fail"
    assert body["verified"] is True

    review = _get_review(test_db, puzzle_id, "testuser")
    assert review.result == "fail"
    assert review.client_result == "pass"  # the spoofed claim is retained
    assert review.attempted_move == "e2e4"

    stats = test_db.get(PuzzleStats, puzzle_id)
    assert stats.pass_count == 0
    assert stats.fail_count == 1


def test_illegal_move_is_a_fail(client, test_db):
    """An illegal/malformed move can never be a solve."""
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-verify-4", 10)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        # e2e5 is not a legal move from the initial position.
        json={"username": "testuser", "result": "pass", "attempted_move": "e2e5"},
    )
    assert response.status_code == 200
    assert response.json()["result"] == "fail"

    review = _get_review(test_db, puzzle_id, "testuser")
    assert review.result == "fail"
    assert review.verified is True


def test_missing_attempted_move_is_recorded_client_reported(client, test_db):
    """Legacy client (no move) → unverified, client-reported, outcome trusted."""
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-verify-5", 10)

    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={"username": "testuser", "result": "pass"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "pass"
    assert body["verified"] is False
    assert body["source"] == "client_reported"

    review = _get_review(test_db, puzzle_id, "testuser")
    assert review.result == "pass"
    assert review.attempted_move is None
    assert review.verified is False
    assert review.source == "client_reported"


def test_wrong_move_resets_session_streak(client, test_db):
    """A server-detected fail must not inflate session pass_count/streak."""
    session_id = str(uuid.uuid4())
    test_db.add(
        TrainingSession(
            id=session_id,
            username="testuser",
            requested_n=5,
            pass_count=0,
            fail_count=0,
            total_time_ms=0,
        )
    )
    test_db.commit()
    puzzle_id = str(uuid.uuid4())
    _create_puzzle(test_db, puzzle_id, "testuser", "game-verify-6", 10)

    # Client claims pass, but the move is wrong.
    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={
            "username": "testuser",
            "result": "pass",
            "attempted_move": "e2e4",
            "session_id": session_id,
        },
    )
    assert response.status_code == 200

    session = test_db.get(TrainingSession, session_id)
    assert session.pass_count == 0
    assert session.fail_count == 1
    assert session.current_streak == 0


# ─── Real multi-connection Postgres concurrency (audit dims 14 & 15) ─────────
#
# The tests above simulate the same-key race in a single SQLite session by
# forcing the replay SELECT to miss (``_make_race_find``). SQLite serializes
# writers and shares one connection, so it can't reproduce a *true* race. The
# tests below run two requests on separate Postgres connections/threads and let
# the database arbitrate, exercising the #194 IntegrityError-replay backstop and
# the COALESCE(session_id, '') unique index under genuine contention.
#
# Determinism (no sleeps): a monkeypatched sync point makes the FIRST call from
# each of the two racing threads block on a shared Barrier so both cross the
# patched point together; later calls (the post-IntegrityError replay lookups)
# pass straight through. The barrier is placed BEFORE either request commits, so
# both are guaranteed to overlap without a fragile timing window.

POSTGRES_URL = os.getenv("KNIGHTMIND_TEST_POSTGRES_URL")

# The skip itself is applied centrally (root conftest.py) so the marker is also
# selectable: CI runs `pytest -m postgres` over the whole suite.
requires_postgres = pytest.mark.postgres

# Child-first delete order so a clean-up never trips a foreign key.
_PG_CLEANUP_MODELS = (PuzzleReview, PuzzleStats, TrainingSession, PuzzleModel, Game)


@contextmanager
def _pg_review_harness():
    """Yield a ``PgSession`` sessionmaker bound to a clean Postgres schema, with
    the app's ``get_db`` overridden to hand each request its OWN per-connection
    session (mirroring production's one-session-per-request).

    A shared SQLite session (as the other fixtures use) would force every thread
    onto one connection and defeat the point; here two threads genuinely race on
    two connections.
    """
    engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(engine)
    PgSession = sessionmaker(bind=engine)
    with PgSession() as s:
        for model in _PG_CLEANUP_MODELS:
            s.query(model).delete()
        s.commit()

    def override_get_db():
        db = PgSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield PgSession
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _install_race_barrier(monkeypatch, attr):
    """Patch ``services.api.main.<attr>`` so the FIRST invocation from each of two
    racing threads blocks on a shared 2-party barrier, guaranteeing both threads
    cross the patched point concurrently before either proceeds to commit.

    Subsequent invocations (e.g. the endpoint's post-IntegrityError replay, which
    re-calls ``_find_existing_review``) pass through untouched so the recovery
    path is not deadlocked.
    """
    barrier = threading.Barrier(2, timeout=30)
    real = getattr(main, attr)
    lock = threading.Lock()
    crossed = {"n": 0}

    def wrapper(*args, **kwargs):
        with lock:
            first = crossed["n"] < 2
            if first:
                crossed["n"] += 1
        if first:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
        return real(*args, **kwargs)

    monkeypatch.setattr(main, attr, wrapper)
    return barrier


def _race_review_posts(puzzle_id, body):
    """Fire two concurrent POST /review with independent TestClients (independent
    event loops) so the two async handlers truly overlap. Returns {idx: status}.
    """
    statuses: dict[int, int] = {}

    def submit(idx):
        resp = TestClient(app).post(f"/puzzles/{puzzle_id}/review", json=body)
        statuses[idx] = resp.status_code

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return statuses


@requires_postgres
def test_review_concurrent_same_key_one_row_postgres(monkeypatch):
    """dim 14: two REAL concurrent same-``client_review_id`` submits over separate
    Postgres connections record exactly ONE review, increment puzzle stats and
    session counters exactly ONCE, and neither returns 500.

    This is the true-race counterpart to the simulated
    ``test_review_endpoint_concurrent_same_key_replays_with_session``: the loser's
    duplicate INSERT is rejected by the unique index, and the endpoint's
    IntegrityError -> replay backstop (#194) returns the winner's outcome.
    """
    with _pg_review_harness() as PgSession:
        session_id = str(uuid.uuid4())
        puzzle_id = str(uuid.uuid4())
        with PgSession() as s:
            _create_puzzle(
                s, puzzle_id, "testuser", "pg-race-14", 10, accept_moves_uci="d2d4"
            )
            s.add(
                TrainingSession(
                    id=session_id,
                    username="testuser",
                    requested_n=5,
                    pass_count=0,
                    fail_count=0,
                    total_time_ms=0,
                )
            )
            s.commit()

        _install_race_barrier(monkeypatch, "_find_existing_review")

        body = {
            "username": "testuser",
            "result": "pass",
            "time_spent_ms": 1000,
            "session_id": session_id,
            "attempted_move": "d2d4",
            "client_review_id": "race-key-pg",
        }
        statuses = _race_review_posts(puzzle_id, body)

        # Both graceful (no 500), whichever won the INSERT.
        assert statuses == {0: 200, 1: 200}

        with PgSession() as s:
            assert s.scalar(select(func.count()).select_from(PuzzleReview)) == 1
            stats = s.get(PuzzleStats, puzzle_id)
            assert stats.attempts == 1
            assert stats.pass_count == 1
            assert stats.interval_days == 1  # scheduling advanced exactly once
            sess = s.get(TrainingSession, session_id)
            assert sess.pass_count == 1
            assert sess.current_streak == 1
            assert sess.total_time_ms == 1000  # counted once, not doubled


@requires_postgres
def test_review_concurrent_same_key_session_less_postgres(monkeypatch):
    """dim 14: the SESSION-LESS same-key race must also dedupe to one row.

    Guards the COALESCE(session_id, '') functional index: a plain multi-column
    unique index treats each NULL session_id as distinct (both SQLite and
    Postgres), so two concurrent no-session submits with the same
    client_review_id would BOTH insert and double-count. Under a real
    two-connection race exactly one row must survive.
    """
    with _pg_review_harness() as PgSession:
        puzzle_id = str(uuid.uuid4())
        with PgSession() as s:
            _create_puzzle(
                s, puzzle_id, "testuser", "pg-race-14b", 10, accept_moves_uci="d2d4"
            )
            s.commit()

        _install_race_barrier(monkeypatch, "_find_existing_review")

        body = {
            "username": "testuser",
            "result": "pass",
            "time_spent_ms": 1000,
            "attempted_move": "d2d4",
            "client_review_id": "race-key-no-session",
        }
        statuses = _race_review_posts(puzzle_id, body)

        assert statuses == {0: 200, 1: 200}
        with PgSession() as s:
            assert s.scalar(select(func.count()).select_from(PuzzleReview)) == 1
            assert s.get(PuzzleStats, puzzle_id).attempts == 1


@requires_postgres
def test_review_same_session_atomic_no_partial_state_postgres(monkeypatch):
    """dim 15: a losing concurrent submit commits ATOMICALLY or not at all — the
    session/stat/review are one transaction boundary with no partial leak.

    The losing request stages a session counter increment (``pass_count += 1``,
    ``total_time_ms += ...``) and a review row BEFORE it flushes and trips the
    unique index. The endpoint must roll ALL of that back (not just the review)
    and replay the winner's outcome. We assert the session counters reflect a
    single review — proof the loser's staged increments were discarded, not
    half-committed.
    """
    with _pg_review_harness() as PgSession:
        session_id = str(uuid.uuid4())
        puzzle_id = str(uuid.uuid4())
        with PgSession() as s:
            _create_puzzle(
                s, puzzle_id, "testuser", "pg-race-15", 10, accept_moves_uci="d2d4"
            )
            s.add(
                TrainingSession(
                    id=session_id,
                    username="testuser",
                    requested_n=5,
                    pass_count=0,
                    fail_count=0,
                    total_time_ms=0,
                )
            )
            s.commit()

        _install_race_barrier(monkeypatch, "_find_existing_review")

        body = {
            "username": "testuser",
            "result": "pass",
            "time_spent_ms": 2500,
            "session_id": session_id,
            "attempted_move": "d2d4",
            "client_review_id": "race-key-atomic",
        }
        statuses = _race_review_posts(puzzle_id, body)
        assert statuses == {0: 200, 1: 200}

        with PgSession() as s:
            # Exactly one of every artefact — no orphan review, no orphan stats,
            # counters incremented once. If the loser's session increment had
            # leaked, pass_count/total_time_ms would show two.
            assert s.scalar(select(func.count()).select_from(PuzzleReview)) == 1
            assert s.scalar(select(func.count()).select_from(PuzzleStats)) == 1
            sess = s.get(TrainingSession, session_id)
            assert sess.pass_count == 1
            assert sess.fail_count == 0
            assert sess.current_streak == 1
            assert sess.total_time_ms == 2500


@requires_postgres
def test_review_distinct_reviews_same_session_no_lost_increment_postgres(monkeypatch):
    """dim 15: two concurrent reviews of DIFFERENT puzzles in the SAME session
    must not lose a counter increment.

    Both reviews are legitimate (distinct puzzles, distinct idempotency keys), so
    the session must end with pass_count == 2 and total_time_ms == 2000.

    Regression guard for a lost-update bug: session counters were a Python-side
    read-modify-write (``session.pass_count += 1``) with no row lock, so under
    Postgres READ COMMITTED both requests read the stale count and one increment
    was silently lost (final pass_count == 1). The fix takes a
    ``SELECT ... FOR UPDATE`` on the session row so concurrent same-session
    reviews serialize. The two requests are released together at the entry
    replay-SELECT; the session lock then forces the second to read the first's
    committed count.
    """
    with _pg_review_harness() as PgSession:
        session_id = str(uuid.uuid4())
        puzzle_a = str(uuid.uuid4())
        puzzle_b = str(uuid.uuid4())
        with PgSession() as s:
            _create_puzzle(
                s, puzzle_a, "testuser", "pg-race-15b-a", 10, accept_moves_uci="d2d4"
            )
            _create_puzzle(
                s, puzzle_b, "testuser", "pg-race-15b-b", 12, accept_moves_uci="d2d4"
            )
            s.add(
                TrainingSession(
                    id=session_id,
                    username="testuser",
                    requested_n=5,
                    pass_count=0,
                    fail_count=0,
                    total_time_ms=0,
                )
            )
            s.commit()

        # Release both requests together at the entry replay-SELECT (before the
        # session lock). Without the fix both would then read pass_count=0 and
        # lose an increment; with the FOR UPDATE lock the second blocks until the
        # first commits and reads the fresh count.
        _install_race_barrier(monkeypatch, "_find_existing_review")

        statuses: dict[int, int] = {}

        def submit(idx, puzzle_id, key):
            resp = TestClient(app).post(
                f"/puzzles/{puzzle_id}/review",
                json={
                    "username": "testuser",
                    "result": "pass",
                    "time_spent_ms": 1000,
                    "session_id": session_id,
                    "attempted_move": "d2d4",
                    "client_review_id": key,
                },
            )
            statuses[idx] = resp.status_code

        threads = [
            threading.Thread(target=submit, args=(0, puzzle_a, "key-a")),
            threading.Thread(target=submit, args=(1, puzzle_b, "key-b")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert statuses == {0: 200, 1: 200}
        with PgSession() as s:
            # Both distinct reviews are genuinely recorded ...
            assert s.scalar(select(func.count()).select_from(PuzzleReview)) == 2
            # ... but the aggregate session counters must reflect BOTH.
            sess = s.get(TrainingSession, session_id)
            assert sess.pass_count == 2
            assert sess.total_time_ms == 2000
