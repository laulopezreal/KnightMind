"""
Integration test for review endpoint with session tracking.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.db import Base, get_db
from services.api.main import app
from services.api.models import Game, PuzzleReview, PuzzleStats, TrainingSession
from services.api.models import Puzzle as PuzzleModel


@pytest.fixture
def test_db():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


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
