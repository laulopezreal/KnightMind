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
    TrainingSession,
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


def _seed_focus_candidate(
    db,
    puzzle_id: str,
    game_id: str,
    *,
    due_at=None,
    ply=20,
    primary_cause=CAUSE,
    confirmed_cause=None,
    status=DiagnosisStatus.OK,
    username=USER,
):
    if db.get(Game, (game_id, username)) is None:
        db.add(
            Game(
                game_id=game_id,
                url=f"https://chess.com/game/{game_id}",
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
        db.flush()
    db.add(
        PuzzleModel(
            id=puzzle_id,
            username=username,
            source_game_id=game_id,
            ply=ply,
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
            username=username,
            status=status,
            primary_cause=primary_cause,
            user_confirmed_cause=confirmed_cause,
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
                username=username,
                attempts=2,
                pass_count=2,
                fail_count=0,
                ease_factor=2.1,
                interval_days=3,
                next_due_at=due_at,
            )
        )


def test_focus_practice_preserves_schedule_tiers_through_diversity(client, db_session):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_focus_candidate(
        db_session, "due-one", "game-due", due_at=now - timedelta(days=2)
    )
    _seed_focus_candidate(
        db_session, "due-two", "game-due", due_at=now - timedelta(days=1), ply=22
    )
    _seed_focus_candidate(db_session, "new", "game-new")
    _seed_focus_candidate(
        db_session, "future", "game-future", due_at=now + timedelta(days=1)
    )
    db_session.commit()

    response = client.post(
        "/sessions/focus-practice/start",
        json={"username": USER, "focus_cause": CAUSE, "n": 4},
    )

    assert response.status_code == 200
    assert [puzzle["id"] for puzzle in response.json()["puzzles"]] == [
        "due-one",
        "due-two",
        "new",
        "future",
    ]


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


_STATS_FIELDS = (
    "attempts",
    "pass_count",
    "fail_count",
    "last_reviewed_at",
    "last_result",
    "next_due_at",
    "interval_days",
    "ease_factor",
    "primary_motif",
    "title",
    "title_source",
)


def _stats_snapshot(stats):
    return tuple(getattr(stats, field) for field in _STATS_FIELDS)


def _start(client, n=2):
    response = client.post(
        "/sessions/focus-practice/start",
        json={"username": USER, "focus_cause": CAUSE, "n": n},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_focus_practice_duplicate_review_replays_original_policy_once(
    client, db_session
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_focus_candidate(db_session, "due", "game-due", due_at=now - timedelta(days=1))
    _seed_focus_candidate(db_session, "new", "game-new")
    _seed_focus_candidate(db_session, "extra", "game-extra")
    _seed_focus_candidate(db_session, "fourth", "game-fourth")
    db_session.commit()
    started = _start(client)

    payload = {
        "username": USER,
        "result": "pass",
        "attempted_move": "d2d4",
        "time_spent_ms": 123,
        "session_id": started["session_id"],
        "client_review_id": "focus-replay",
    }
    first = client.post("/puzzles/due/review", json=payload)
    assert first.status_code == 200
    first_body = first.json()
    stats = db_session.get(PuzzleStats, "due")
    after_first = _stats_snapshot(stats)
    session = db_session.get(TrainingSession, started["session_id"])
    counters_after_first = (
        session.pass_count,
        session.fail_count,
        session.total_time_ms,
    )

    replay = client.post("/puzzles/due/review", json={**payload, "result": "fail"})
    assert replay.status_code == 200
    assert (
        replay.json()["review_context"]
        == first_body["review_context"]
        == "focus_practice"
    )
    assert (
        replay.json()["affects_scheduling"] is first_body["affects_scheduling"] is True
    )
    assert replay.json()["result"] == first_body["result"] == "pass"
    db_session.refresh(stats)
    db_session.refresh(session)
    assert _stats_snapshot(stats) == after_first
    assert (
        session.pass_count,
        session.fail_count,
        session.total_time_ms,
    ) == counters_after_first
    assert db_session.query(PuzzleReview).count() == 1


def test_focus_practice_rejects_out_of_snapshot_and_foreign_reviews_without_mutation(
    client, db_session
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_focus_candidate(db_session, "due", "game-due", due_at=now - timedelta(days=1))
    _seed_focus_candidate(db_session, "new", "game-new")
    _seed_focus_candidate(
        db_session,
        "other",
        "game-other",
        primary_cause="king_safety_blindness",
    )
    _seed_focus_candidate(
        db_session,
        "foreign",
        "game-foreign",
        primary_cause="king_safety_blindness",
        username="another-user",
    )
    _seed_focus_candidate(db_session, "extra", "game-extra")
    _seed_focus_candidate(db_session, "fourth", "game-fourth")
    db_session.commit()
    started = _start(client)

    out_of_snapshot = client.post(
        "/puzzles/other/review",
        json={"username": USER, "result": "pass", "session_id": started["session_id"]},
    )
    assert out_of_snapshot.status_code == 409
    assert out_of_snapshot.json()["detail"]["code"] == "session_item_mismatch"
    foreign_response = client.post(
        "/puzzles/foreign/review",
        json={"username": USER, "result": "pass", "session_id": started["session_id"]},
    )
    assert foreign_response.status_code == 404
    session = db_session.get(TrainingSession, started["session_id"])
    assert (session.pass_count, session.fail_count, session.total_time_ms) == (0, 0, 0)
    assert db_session.query(PuzzleReview).count() == 0


@pytest.mark.parametrize("result", ["pass", "fail"])
def test_future_focus_practice_never_mutates_any_stats_field(
    client, db_session, result
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_focus_candidate(
        db_session, "future", "game-future", due_at=now + timedelta(days=1)
    )
    _seed_focus_candidate(
        db_session, "future-two", "game-future-two", due_at=now + timedelta(days=2)
    )
    _seed_focus_candidate(
        db_session, "future-three", "game-future-three", due_at=now + timedelta(days=3)
    )
    _seed_focus_candidate(
        db_session, "future-four", "game-future-four", due_at=now + timedelta(days=4)
    )
    db_session.commit()
    before = db_session.get(PuzzleStats, "future")
    snapshot = _stats_snapshot(before)
    started = _start(client)

    response = client.post(
        "/puzzles/future/review",
        json={
            "username": USER,
            "result": result,
            "attempted_move": "d2d4" if result == "pass" else "e2e4",
            "session_id": started["session_id"],
            "client_review_id": f"future-{result}",
        },
    )
    assert response.status_code == 200
    assert response.json()["affects_scheduling"] is False
    db_session.refresh(before)
    assert _stats_snapshot(before) == snapshot


@pytest.mark.parametrize("puzzle_id,due_at", [("due", "due"), ("new", None)])
def test_due_and_new_focus_items_follow_normal_verified_scheduling(
    client, db_session, puzzle_id, due_at
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    target_due_at = now - timedelta(days=1) if due_at else None
    _seed_focus_candidate(
        db_session, puzzle_id, f"game-{puzzle_id}", due_at=target_due_at
    )
    _seed_focus_candidate(db_session, "other", "game-other")
    _seed_focus_candidate(db_session, "extra", "game-extra")
    _seed_focus_candidate(db_session, "fourth", "game-fourth")
    db_session.commit()
    started = _start(client, n=4)
    response = client.post(
        f"/puzzles/{puzzle_id}/review",
        json={
            "username": USER,
            "result": "fail",
            "attempted_move": "d2d4",
            "session_id": started["session_id"],
            "client_review_id": f"normal-{puzzle_id}",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is True
    assert body["result"] == "pass"
    assert body["review_context"] == "focus_practice"
    assert body["affects_scheduling"] is True
    stats = db_session.get(PuzzleStats, puzzle_id)
    assert stats.attempts >= 1
    assert stats.last_result == "pass"
    assert stats.next_due_at is not None


def test_focus_practice_snapshot_policy_survives_due_boundary_on_resume_and_review(
    client, db_session
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_focus_candidate(
        db_session, "future", "game-future", due_at=now + timedelta(days=1)
    )
    _seed_focus_candidate(
        db_session, "future-two", "game-future-two", due_at=now + timedelta(days=2)
    )
    _seed_focus_candidate(
        db_session, "future-three", "game-future-three", due_at=now + timedelta(days=3)
    )
    _seed_focus_candidate(
        db_session, "future-four", "game-future-four", due_at=now + timedelta(days=4)
    )
    db_session.commit()
    started = _start(client)
    future_stats = db_session.get(PuzzleStats, "future")
    future_stats.next_due_at = now - timedelta(seconds=1)
    db_session.commit()

    resumed = client.get(f"/sessions/{started['session_id']}")
    assert resumed.status_code == 200
    assert resumed.json()["puzzles"][0]["review_policy"] == "practice_only"
    response = client.post(
        "/puzzles/future/review",
        json={"username": USER, "result": "pass", "session_id": started["session_id"]},
    )
    assert response.status_code == 200
    assert response.json()["affects_scheduling"] is False


@pytest.mark.parametrize("gate_on", [True, False])
def test_focus_practice_start_and_resume_obey_solution_gate(
    client, db_session, monkeypatch, gate_on
):
    if gate_on:
        monkeypatch.setenv("KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS", "true")
    else:
        monkeypatch.delenv("KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS", raising=False)
    _seed_focus_candidate(db_session, "one", "game-one")
    _seed_focus_candidate(db_session, "two", "game-two")
    _seed_focus_candidate(db_session, "three", "game-three")
    _seed_focus_candidate(db_session, "four", "game-four")
    db_session.commit()
    started = _start(client)
    resumed = client.get(f"/sessions/{started['session_id']}")
    assert resumed.status_code == 200
    for payload in (started["puzzles"][0], resumed.json()["puzzles"][0]):
        assert "primary_cause" not in payload
        if gate_on:
            for field in (
                "best_move_uci",
                "accept_moves_uci",
                "played_move_uci",
                "solution_pv",
            ):
                assert field not in payload
        else:
            assert payload["best_move_uci"] == "d2d4"
            assert payload["played_move_uci"] == "e2e4"


def test_focus_practice_uses_valid_confirmed_cause_but_never_invalid_override(
    client, db_session
):
    _seed_focus_candidate(
        db_session,
        "confirmed",
        "game-confirmed",
        primary_cause="king_safety_blindness",
        confirmed_cause=CAUSE,
    )
    _seed_focus_candidate(db_session, "primary", "game-primary")
    _seed_focus_candidate(db_session, "primary-two", "game-primary-two")
    _seed_focus_candidate(db_session, "primary-three", "game-primary-three")
    _seed_focus_candidate(
        db_session,
        "invalid",
        "game-invalid",
        primary_cause=CAUSE,
        confirmed_cause="obsolete_cause",
    )
    db_session.commit()

    started = _start(client)
    selected = {puzzle["id"] for puzzle in started["puzzles"]}
    assert {"confirmed", "primary"} <= selected
    assert "invalid" not in selected
    stale_probe = client.post(
        "/sessions/focus-practice/start",
        json={"username": USER, "focus_cause": "king_safety_blindness", "n": 2},
    )
    assert stale_probe.status_code == 409
