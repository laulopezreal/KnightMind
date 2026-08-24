"""Contract tests for the server-owned Focus Practice session."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.api.db import get_db
from services.api.main import app
from services.api.models import (
    DiagnosisStatus,
    Game,
    PuzzleDiagnosis,
    PuzzleReview,
    PuzzleStats,
)
from services.api.models import Puzzle as PuzzleModel

USER = "focus-user"
CAUSE = "loose_piece_awareness"


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_focus_candidate(db, puzzle_id: str, game_id: str, *, due_at=None):
    db.add(
        Game(
            game_id=game_id,
            url=f"https://chess.com/game/{game_id}",
            username=USER,
            white_username=USER,
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
            username=USER,
            source_game_id=game_id,
            ply=20,
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci="d2d4",
            eval_before=0.5,
            eval_after=-1.5,
            swing=2.0,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        PuzzleDiagnosis(
            puzzle_id=puzzle_id,
            username=USER,
            status=DiagnosisStatus.OK,
            primary_cause=CAUSE,
            primary_motif="fork",
            secondary_causes=[],
            insufficient_evidence=False,
            source="rules",
        )
    )
    if due_at is not None:
        db.add(
            PuzzleStats(
                puzzle_id=puzzle_id,
                username=USER,
                attempts=2,
                pass_count=2,
                fail_count=0,
                ease_factor=2.1,
                interval_days=3,
                next_due_at=due_at,
            )
        )


def test_focus_practice_start_persists_server_owned_snapshot(client, db_session):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_focus_candidate(db_session, "due", "game-due", due_at=now - timedelta(days=1))
    _seed_focus_candidate(
        db_session, "future", "game-future", due_at=now + timedelta(days=7)
    )
    _seed_focus_candidate(db_session, "new", "game-new")
    _seed_focus_candidate(
        db_session, "future-later", "game-future-later", due_at=now + timedelta(days=14)
    )
    db_session.commit()

    response = client.post(
        "/sessions/focus-practice/start",
        json={"username": USER, "focus_cause": CAUSE, "n": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_type"] == "focus_practice"
    assert [puzzle["id"] for puzzle in body["puzzles"]] == ["due", "new", "future"]
    assert [puzzle["review_policy"] for puzzle in body["puzzles"]] == [
        "normal_review",
        "normal_review",
        "practice_only",
    ]


def test_future_focus_practice_records_telemetry_without_mutating_stats(
    client, db_session
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_focus_candidate(db_session, "due", "game-due", due_at=now - timedelta(days=1))
    _seed_focus_candidate(
        db_session, "future", "game-future", due_at=now + timedelta(days=7)
    )
    _seed_focus_candidate(db_session, "new", "game-new")
    _seed_focus_candidate(db_session, "extra", "game-extra")
    db_session.commit()
    before = db_session.get(PuzzleStats, "future")
    original = (
        before.attempts,
        before.pass_count,
        before.fail_count,
        before.next_due_at,
    )

    started = client.post(
        "/sessions/focus-practice/start",
        json={"username": USER, "focus_cause": CAUSE, "n": 4},
    ).json()
    response = client.post(
        "/puzzles/future/review",
        json={
            "username": USER,
            "result": "pass",
            "session_id": started["session_id"],
            "client_review_id": "focus-future-1",
        },
    )

    assert response.status_code == 200
    assert response.json()["review_context"] == "focus_practice"
    assert response.json()["affects_scheduling"] is False
    db_session.refresh(before)
    assert (
        before.attempts,
        before.pass_count,
        before.fail_count,
        before.next_due_at,
    ) == original
    review = db_session.query(PuzzleReview).one()
    assert review.review_context == "focus_practice"
    assert review.affects_scheduling is False


def test_generic_session_start_rejects_forged_focus_practice_type(client):
    response = client.post(
        "/sessions/start",
        json={"username": USER, "n": 5, "session_type": "focus_practice"},
    )
    assert response.status_code == 422


def test_focus_practice_resume_uses_the_saved_order_and_policy(client, db_session):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_focus_candidate(db_session, "due", "game-due", due_at=now - timedelta(days=1))
    _seed_focus_candidate(
        db_session, "future", "game-future", due_at=now + timedelta(days=7)
    )
    _seed_focus_candidate(db_session, "new", "game-new")
    _seed_focus_candidate(db_session, "extra", "game-extra")
    db_session.commit()

    started = client.post(
        "/sessions/focus-practice/start",
        json={"username": USER, "focus_cause": CAUSE, "n": 4},
    ).json()

    # The live diagnosis can change after the session starts. Resume must use
    # the persisted snapshot, not re-select against current focus state.
    db_session.get(PuzzleDiagnosis, ("due", USER)).user_confirmed_cause = (
        "king_safety_blindness"
    )
    db_session.commit()

    resumed = client.get(f"/sessions/{started['session_id']}")

    assert resumed.status_code == 200
    body = resumed.json()
    assert body["focus_cause"] == CAUSE
    assert body["focus_name"] == started["focus"]["name"]
    started_items = [
        (puzzle["id"], puzzle["review_policy"]) for puzzle in started["puzzles"]
    ]
    assert [
        (item["puzzle_id"], item["review_policy"]) for item in body["selected_items"]
    ] == started_items
    assert [
        (puzzle["id"], puzzle["review_policy"]) for puzzle in body["puzzles"]
    ] == started_items
