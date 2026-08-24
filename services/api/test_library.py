"""Tests for the GET /puzzles/list (Library) endpoint."""

import os
from typing import Any

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.api.main import app, get_db
from services.api.models import (
    DiagnosisStatus,
    Game,
    PuzzleDiagnosis,
    PuzzleStats,
)
from services.api.models import Puzzle as PuzzleModel


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create_game(db, game_id: str, username: str = "testuser"):
    """Helper: create a Game row."""
    existing = db.get(Game, (game_id, username))
    if existing:
        return
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
            end_time=int(datetime.now(timezone.utc).timestamp()),
            rated=True,
        )
    )
    db.flush()


def _create_puzzle(
    db,
    puzzle_id: str,
    username: str = "testuser",
    swing: float = 3.0,
    fen: str = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    best_move_uci: str = "d2d4",
    created_at: datetime | None = None,
):
    """Helper: create a Puzzle row (and parent Game if needed)."""
    game_id = f"game-{puzzle_id}"
    _create_game(db, game_id, username)
    db.add(
        PuzzleModel(
            id=puzzle_id,
            username=username,
            source_game_id=game_id,
            ply=10,
            fen=fen,
            side_to_move="white",
            played_move_uci="e2e4",
            best_move_uci=best_move_uci,
            eval_before=0.2,
            eval_after=-0.5,
            swing=swing,
            created_at=created_at or datetime.now(timezone.utc),
        )
    )
    db.flush()


# Sentinel: "no title was asked for", which is not the same as title=None (a
# genuinely untitled row, which several tests want).
# Sentinel distinguishing "caller said nothing" from an explicit
# title=None. Typed Any so it can sit in a `str | None` parameter
# default without mypy rejecting the assignment.
_FROM_PUZZLE_ID: Any = object()


def _create_stats(
    db,
    puzzle_id: str,
    username: str = "testuser",
    # Derived from the puzzle id, not a constant: titles are unique per user
    # (uq_puzzle_stats_username_title), so a shared default would make any test
    # that seeds two rows fail on the constraint rather than on its subject.
    title: str | None = _FROM_PUZZLE_ID,
    primary_motif: str | None = None,
    attempts: int = 0,
    pass_count: int = 0,
    fail_count: int = 0,
    last_reviewed_at: datetime | None = None,
    last_result: str | None = None,
    next_due_at: datetime | None = None,
    resolved: bool = True,
):
    """Helper: create a PuzzleStats row.

    Defaults to a RESOLVED row -- attempted, not yet due again -- so a test
    asserting a revealing field passes with the resolution gate both on and
    off. Pass ``resolved=False`` (or set attempts/next_due_at directly) for a
    test whose subject IS the gate.

    Without this the suite could only ever be run with the gate off, which is
    exactly how three ungated routes and two dead overrides shipped green.
    Callers that set `attempts` or `next_due_at` explicitly keep their values.
    """
    if resolved and attempts == 0 and next_due_at is None:
        attempts = 1
        next_due_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            days=3
        )
        last_reviewed_at = last_reviewed_at or datetime.now(timezone.utc).replace(
            tzinfo=None
        )
    if title is _FROM_PUZZLE_ID:
        title = f"Test Puzzle {puzzle_id}"
    db.add(
        PuzzleStats(
            puzzle_id=puzzle_id,
            username=username,
            title=title,
            primary_motif=primary_motif,
            attempts=attempts,
            pass_count=pass_count,
            fail_count=fail_count,
            last_reviewed_at=last_reviewed_at,
            last_result=last_result,
            next_due_at=next_due_at,
            interval_days=1 if next_due_at else None,
            ease_factor=2.0,
        )
    )
    db.flush()


def _create_diagnosis(
    db,
    puzzle_id: str,
    username: str = "testuser",
    primary_cause: str | None = "loose_piece_awareness",
    user_confirmed_cause: str | None = None,
    insufficient_evidence: bool = False,
    status: str = DiagnosisStatus.OK,
    error: str | None = None,
    opening_name: str | None = None,
):
    db.add(
        PuzzleDiagnosis(
            puzzle_id=puzzle_id,
            username=username,
            status=status,
            error=error,
            primary_motif="fork",
            primary_cause=primary_cause,
            user_confirmed_cause=user_confirmed_cause,
            insufficient_evidence=insufficient_evidence,
            source="rules",
            evidence_json=[
                {
                    "id": "best.move",
                    "label": "Best move",
                    "value": "Best move Qxd5 from d1 to d5, PV d1d5",
                }
            ],
            opening_name=opening_name,
            explanation="The solution is Qxd5.",
            training_recommendation="Look for the queen move Qxd5.",
            updated_at=datetime(2026, 1, 2, 12, 0),
        )
    )
    db.flush()


def _seed_puzzles(db, count: int = 5, username: str = "testuser"):
    """Seed `count` puzzles with varying properties."""
    now = datetime.now(timezone.utc)
    motifs = ["Fork", "Pin", "Skewer", None, "Discovery"]
    swings = [1.0, 3.0, 6.0, 2.5, 4.0]

    for i in range(count):
        pid = f"p-{i}"
        _create_puzzle(
            db,
            pid,
            username,
            swing=swings[i % len(swings)],
            created_at=now - timedelta(days=count - i),
        )
        _create_stats(
            db,
            pid,
            username,
            title=f"Puzzle {i}",
            primary_motif=motifs[i % len(motifs)],
            attempts=i + 1,
            pass_count=i,
            fail_count=1,
            last_reviewed_at=now - timedelta(hours=i + 1),
            last_result="pass" if i > 0 else "fail",
            next_due_at=now - timedelta(hours=i) if i < 2 else now + timedelta(days=i),
        )
    db.commit()


# ---- Basic endpoint tests ----


class TestListPuzzlesBasic:
    def test_missing_username(self, client):
        """Username is required."""
        response = client.get("/puzzles/list")
        assert response.status_code == 422

    def test_empty_result(self, client):
        """Returns empty list when user has no puzzles."""
        response = client.get("/puzzles/list?username=nobody")
        assert response.status_code == 200
        data = response.json()
        assert data["puzzles"] == []
        assert data["total"] == 0
        assert data["available_motifs"] == []
        assert data["stats"] == {
            "total": 0,
            "due": 0,
            "new": 0,
            "learning": 0,
            "mastered": 0,
        }

    def test_returns_all_puzzles(self, client, db_session):
        """Returns all puzzles for a user with correct structure."""
        _seed_puzzles(db_session, count=3)
        response = client.get("/puzzles/list?username=testuser")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["puzzles"]) == 3

    def test_response_shape(self, client, db_session):
        """Each puzzle in response has required fields."""
        _create_puzzle(db_session, "p-shape", swing=2.5)
        _create_stats(
            db_session,
            "p-shape",
            title="Shape Test",
            primary_motif="Fork",
            attempts=3,
            pass_count=2,
            fail_count=1,
        )
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser")
        puzzle = response.json()["puzzles"][0]

        required_fields = [
            "id",
            "title",
            "primary_motif",
            "difficulty",
            "swing",
            "fen",
            "side_to_move",
            "best_move_uci",
            "status",
            "attempts",
            "pass_count",
            "fail_count",
            "last_reviewed_at",
            "last_result",
            "next_due_at",
            "created_at",
            "diagnosis_summary",
        ]
        for field in required_fields:
            assert field in puzzle, f"Missing field: {field}"

    def test_includes_safe_diagnosis_summary_without_solution_evidence(
        self, client, db_session, monkeypatch
    ):
        _create_puzzle(db_session, "p-diagnosed", best_move_uci="d1d5")
        _create_stats(db_session, "p-diagnosed")
        _create_diagnosis(db_session, "p-diagnosed")
        db_session.commit()
        monkeypatch.setenv("KNIGHTMIND_STRIP_PUZZLE_SOLUTIONS", "true")

        response = client.get("/puzzles/list?username=testuser")
        assert response.status_code == 200
        puzzle = response.json()["puzzles"][0]

        assert puzzle["best_move_uci"] is None
        assert puzzle["accept_moves_uci"] == []
        assert puzzle["diagnosis_summary"] == {
            "state": "ready",
            "primary_cause": "loose_piece_awareness",
            "primary_cause_label": "Loose piece awareness",
            "source": "rules",
            "diagnosed_at": "2026-01-02T12:00:00",
        }
        assert set(puzzle["diagnosis_summary"]) == {
            "state",
            "primary_cause",
            "primary_cause_label",
            "source",
            "diagnosed_at",
        }
        wire = response.text
        assert "Qxd5" not in wire
        assert "d1d5" not in wire
        assert "from d1 to d5" not in wire
        assert "training_recommendation" not in wire
        assert "explanation" not in wire
        assert "evidence_json" not in wire

    def test_missing_diagnosis_returns_null_summary(self, client, db_session):
        _create_puzzle(db_session, "p-undx")
        db_session.commit()

        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]

        assert puzzle["diagnosis_summary"] is None

    def test_user_confirmed_cause_wins_in_list_summary(self, client, db_session):
        _create_puzzle(db_session, "p-confirmed")
        # Resolved: this test is about the summary's CONTENT, not the
        # gate, and a puzzle with no stats row is unresolved by
        # definition -- so the summary would be withheld.
        _create_stats(db_session, "p-confirmed")
        _create_diagnosis(
            db_session,
            "p-confirmed",
            primary_cause="loose_piece_awareness",
            user_confirmed_cause="king_safety_blindness",
        )
        db_session.commit()

        summary = client.get("/puzzles/list?username=testuser").json()["puzzles"][0][
            "diagnosis_summary"
        ]

        assert summary["primary_cause"] == "king_safety_blindness"
        assert summary["primary_cause_label"] == "King safety blindness"

    def test_unclear_state_with_cause_reports_unclear(self, client, db_session):
        """insufficient_evidence=True forces state='unclear' even when primary_cause is set."""
        _create_puzzle(db_session, "p-unclear")
        # Resolved: this test is about the summary's CONTENT, not the
        # gate, and a puzzle with no stats row is unresolved by
        # definition -- so the summary would be withheld.
        _create_stats(db_session, "p-unclear")
        _create_diagnosis(
            db_session,
            "p-unclear",
            primary_cause="loose_piece_awareness",
            insufficient_evidence=True,
        )
        db_session.commit()

        summary = client.get("/puzzles/list?username=testuser").json()["puzzles"][0][
            "diagnosis_summary"
        ]

        assert summary["state"] == "unclear"
        assert summary["primary_cause"] == "loose_piece_awareness"
        assert summary["primary_cause_label"] == "Loose piece awareness"

    def test_unavailable_diagnosis_omits_error_field(self, client, db_session):
        """UNAVAILABLE rows carry an error reason that must never reach the client."""
        _create_puzzle(db_session, "p-unavail")
        # Resolved: this test is about the summary's CONTENT, not the
        # gate, and a puzzle with no stats row is unresolved by
        # definition -- so the summary would be withheld.
        _create_stats(db_session, "p-unavail")
        _create_diagnosis(
            db_session,
            "p-unavail",
            primary_cause=None,
            status=DiagnosisStatus.UNAVAILABLE,
            error="illegal_move_SENTINEL",
        )
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser")
        summary = response.json()["puzzles"][0]["diagnosis_summary"]

        assert summary["state"] == "unavailable"
        assert summary["primary_cause"] is None
        assert "illegal_move_SENTINEL" not in response.text
        assert "error" not in summary

    def test_diagnosis_not_leaked_across_users(self, client, db_session):
        """A diagnosis row for user B must not appear in user A's list."""
        _create_puzzle(db_session, "p-shared", username="alice")
        _create_diagnosis(db_session, "p-shared", username="bob")
        db_session.commit()

        data = client.get("/puzzles/list?username=alice").json()
        assert data["puzzles"][0]["diagnosis_summary"] is None

    def test_puzzles_without_stats_are_new(self, client, db_session):
        """Puzzles with no stats row have status 'new' and zeroed counts."""
        _create_puzzle(db_session, "p-nostats", swing=1.5)
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser")
        puzzle = response.json()["puzzles"][0]
        assert puzzle["status"] == "new"
        assert puzzle["attempts"] == 0
        assert puzzle["pass_count"] == 0
        assert puzzle["fail_count"] == 0
        assert puzzle["title"] is None

    def test_user_scoping(self, client, db_session):
        """Puzzles for one user don't leak into another's results."""
        _create_puzzle(db_session, "p-alice", username="alice")
        _create_puzzle(db_session, "p-bob", username="bob")
        db_session.commit()

        response = client.get("/puzzles/list?username=alice")
        ids = [p["id"] for p in response.json()["puzzles"]]
        assert ids == ["p-alice"]


# ---- Status computation tests ----


class TestStatusComputation:
    def test_status_new(self, client, db_session):
        """Puzzle with no stats → 'new'."""
        _create_puzzle(db_session, "p-new")
        db_session.commit()
        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]
        assert puzzle["status"] == "new"

    def test_status_due(self, client, db_session):
        """Puzzle with next_due_at in the past → 'due'."""
        _create_puzzle(db_session, "p-due")
        _create_stats(
            db_session,
            "p-due",
            attempts=1,
            pass_count=1,
            next_due_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db_session.commit()
        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]
        assert puzzle["status"] == "due"

    def test_status_learning(self, client, db_session):
        """Puzzle with recent activity but below mastery threshold → 'learning'."""
        _create_puzzle(db_session, "p-learning")
        _create_stats(
            db_session,
            "p-learning",
            attempts=2,
            pass_count=1,
            fail_count=1,
            next_due_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db_session.commit()
        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]
        assert puzzle["status"] == "learning"

    def test_status_mastered(self, client, db_session):
        """Puzzle with ≥80% success rate and ≥3 attempts → 'mastered'."""
        _create_puzzle(db_session, "p-mastered")
        _create_stats(
            db_session,
            "p-mastered",
            attempts=5,
            pass_count=5,
            fail_count=0,
            next_due_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db_session.commit()
        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]
        assert puzzle["status"] == "mastered"


# ---- Corpus stats tests ----


class TestCorpusStats:
    def test_stats_reflect_all_statuses(self, client, db_session):
        """Stats object counts each status category."""
        _create_puzzle(db_session, "p-new1")
        _create_puzzle(db_session, "p-due1")
        _create_stats(
            db_session,
            "p-due1",
            attempts=1,
            pass_count=1,
            next_due_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        _create_puzzle(db_session, "p-mastered1")
        _create_stats(
            db_session,
            "p-mastered1",
            attempts=5,
            pass_count=5,
            fail_count=0,
            next_due_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db_session.commit()

        data = client.get("/puzzles/list?username=testuser").json()
        stats = data["stats"]
        assert stats["total"] == 3
        assert stats["new"] == 1
        assert stats["due"] == 1
        assert stats["mastered"] == 1
        assert stats["learning"] == 0

    def test_stats_unaffected_by_filters(self, client, db_session):
        """Stats always reflect the full corpus, even when filters are applied."""
        _create_puzzle(db_session, "p-a", swing=1.0)
        _create_puzzle(db_session, "p-b", swing=6.0)
        db_session.commit()

        # Filter to only hard puzzles
        data = client.get("/puzzles/list?username=testuser&difficulty=hard").json()
        assert data["total"] == 1  # filtered total
        assert data["stats"]["total"] == 2  # corpus total (unfiltered)


# ---- Difficulty bucketing tests ----


class TestDifficultyBucketing:
    def test_easy(self, client, db_session):
        """swing < 2.0 → 'easy'."""
        _create_puzzle(db_session, "p-easy", swing=1.5)
        db_session.commit()
        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]
        assert puzzle["difficulty"] == "easy"

    def test_medium(self, client, db_session):
        """2.0 ≤ swing < 5.0 → 'medium'."""
        _create_puzzle(db_session, "p-med", swing=3.5)
        db_session.commit()
        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]
        assert puzzle["difficulty"] == "medium"

    def test_hard(self, client, db_session):
        """swing ≥ 5.0 → 'hard'."""
        _create_puzzle(db_session, "p-hard", swing=7.0)
        db_session.commit()
        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]
        assert puzzle["difficulty"] == "hard"

    def test_boundary_easy_medium(self, client, db_session):
        """swing == 2.0 exactly → 'medium'."""
        _create_puzzle(db_session, "p-boundary", swing=2.0)
        db_session.commit()
        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]
        assert puzzle["difficulty"] == "medium"

    def test_boundary_medium_hard(self, client, db_session):
        """swing == 5.0 exactly → 'hard'."""
        _create_puzzle(db_session, "p-boundary-h", swing=5.0)
        db_session.commit()
        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]
        assert puzzle["difficulty"] == "hard"


# ---- Filter tests ----


class TestFilters:
    def test_search_by_title(self, client, db_session):
        """Search by title substring."""
        _create_puzzle(db_session, "p-a")
        _create_stats(db_session, "p-a", title="Poison Pawn Trap")
        _create_puzzle(db_session, "p-b")
        _create_stats(db_session, "p-b", title="Knight Fork")
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&q=poison")
        data = response.json()
        assert data["total"] == 1
        assert data["puzzles"][0]["title"] == "Poison Pawn Trap"

    def test_search_by_id(self, client, db_session):
        """Search by puzzle ID substring."""
        _create_puzzle(db_session, "abc-123")
        _create_puzzle(db_session, "xyz-456")
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&q=abc")
        assert response.json()["total"] == 1
        assert response.json()["puzzles"][0]["id"] == "abc-123"

    def test_search_case_insensitive(self, client, db_session):
        """Search is case-insensitive."""
        _create_puzzle(db_session, "p-ci")
        _create_stats(db_session, "p-ci", title="Back Rank Mate")
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&q=BACK")
        assert response.json()["total"] == 1

    def test_filter_status(self, client, db_session):
        """Filter by status."""
        _create_puzzle(db_session, "p-new")  # no stats → new
        _create_puzzle(db_session, "p-due")
        _create_stats(
            db_session,
            "p-due",
            attempts=1,
            pass_count=1,
            next_due_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&status=new")
        assert response.json()["total"] == 1
        assert response.json()["puzzles"][0]["id"] == "p-new"

        response = client.get("/puzzles/list?username=testuser&status=due")
        assert response.json()["total"] == 1
        assert response.json()["puzzles"][0]["id"] == "p-due"

    def test_filter_motif(self, client, db_session):
        """Filter by motif."""
        _create_puzzle(db_session, "p-fork")
        _create_stats(db_session, "p-fork", primary_motif="Fork")
        _create_puzzle(db_session, "p-pin")
        _create_stats(db_session, "p-pin", primary_motif="Pin")
        _create_puzzle(db_session, "p-none")
        _create_stats(db_session, "p-none", primary_motif=None)
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&motif=Fork")
        data = response.json()
        assert data["total"] == 1
        assert data["puzzles"][0]["id"] == "p-fork"

    def test_filter_motif_comma_separated(self, client, db_session):
        """Comma-separated motifs apply as OR filter."""
        _create_puzzle(db_session, "p-fork")
        _create_stats(db_session, "p-fork", primary_motif="Fork")
        _create_puzzle(db_session, "p-pin")
        _create_stats(db_session, "p-pin", primary_motif="Pin")
        _create_puzzle(db_session, "p-skewer")
        _create_stats(db_session, "p-skewer", primary_motif="Skewer")
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&motif=Fork,Pin")
        data = response.json()
        assert data["total"] == 2
        ids = {p["id"] for p in data["puzzles"]}
        assert ids == {"p-fork", "p-pin"}

    def test_filter_difficulty(self, client, db_session):
        """Filter by difficulty bucket."""
        _create_puzzle(db_session, "p-easy", swing=1.0)
        _create_puzzle(db_session, "p-hard", swing=7.0)
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&difficulty=easy")
        assert response.json()["total"] == 1
        assert response.json()["puzzles"][0]["id"] == "p-easy"

    def test_combined_filters(self, client, db_session):
        """Multiple filters combine (AND)."""
        _create_puzzle(db_session, "p-target", swing=6.0)
        _create_stats(db_session, "p-target", primary_motif="Fork")
        _create_puzzle(db_session, "p-wrong-motif", swing=6.0)
        _create_stats(db_session, "p-wrong-motif", primary_motif="Pin")
        _create_puzzle(db_session, "p-wrong-diff", swing=1.0)
        _create_stats(db_session, "p-wrong-diff", primary_motif="Fork")
        db_session.commit()

        response = client.get(
            "/puzzles/list?username=testuser&motif=Fork&difficulty=hard"
        )
        data = response.json()
        assert data["total"] == 1
        assert data["puzzles"][0]["id"] == "p-target"

    def test_no_results(self, client, db_session):
        """Filters that match nothing return empty list."""
        _create_puzzle(db_session, "p-solo")
        db_session.commit()
        response = client.get("/puzzles/list?username=testuser&q=nonexistent")
        assert response.json()["total"] == 0
        assert response.json()["puzzles"] == []


# ---- Sort tests ----


class TestSorting:
    def test_sort_due_soonest(self, client, db_session):
        """Default sort puts due puzzles first, then new, then future."""
        now = datetime.now(timezone.utc)
        _create_puzzle(db_session, "p-future")
        _create_stats(
            db_session,
            "p-future",
            attempts=1,
            pass_count=1,
            next_due_at=now + timedelta(days=10),
        )
        _create_puzzle(db_session, "p-due")
        _create_stats(
            db_session,
            "p-due",
            attempts=1,
            pass_count=1,
            next_due_at=now - timedelta(hours=2),
        )
        _create_puzzle(db_session, "p-new")  # no stats
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&sort=due_soonest")
        ids = [p["id"] for p in response.json()["puzzles"]]
        assert ids[0] == "p-due"
        # new before future
        assert ids[1] == "p-new"
        assert ids[2] == "p-future"

    def test_sort_most_failed(self, client, db_session):
        """Sort by fail_count descending."""
        _create_puzzle(db_session, "p-low")
        _create_stats(db_session, "p-low", fail_count=1)
        _create_puzzle(db_session, "p-high")
        _create_stats(db_session, "p-high", fail_count=10)
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&sort=most_failed")
        ids = [p["id"] for p in response.json()["puzzles"]]
        assert ids[0] == "p-high"
        assert ids[1] == "p-low"

    def test_sort_difficulty_asc(self, client, db_session):
        """Sort by swing ascending."""
        _create_puzzle(db_session, "p-hard", swing=8.0)
        _create_puzzle(db_session, "p-easy", swing=0.5)
        _create_puzzle(db_session, "p-med", swing=3.0)
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&sort=difficulty_asc")
        ids = [p["id"] for p in response.json()["puzzles"]]
        assert ids == ["p-easy", "p-med", "p-hard"]

    def test_sort_newest(self, client, db_session):
        """Sort by created_at descending."""
        now = datetime.now(timezone.utc)
        _create_puzzle(db_session, "p-old", created_at=now - timedelta(days=10))
        _create_puzzle(db_session, "p-new", created_at=now)
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser&sort=newest")
        ids = [p["id"] for p in response.json()["puzzles"]]
        assert ids[0] == "p-new"
        assert ids[1] == "p-old"


# ---- Pagination tests ----


class TestPagination:
    def test_default_pagination(self, client, db_session):
        """Default limit is 50, offset 0."""
        _seed_puzzles(db_session, count=3)
        response = client.get("/puzzles/list?username=testuser")
        data = response.json()
        assert data["limit"] == 50
        assert data["offset"] == 0
        assert len(data["puzzles"]) == 3
        assert data["total"] == 3

    def test_custom_limit(self, client, db_session):
        """Custom limit restricts results."""
        _seed_puzzles(db_session, count=5)
        response = client.get("/puzzles/list?username=testuser&limit=2")
        data = response.json()
        assert len(data["puzzles"]) == 2
        assert data["total"] == 5

    def test_offset(self, client, db_session):
        """Offset skips results."""
        _seed_puzzles(db_session, count=5)
        response = client.get("/puzzles/list?username=testuser&limit=2&offset=3")
        data = response.json()
        assert len(data["puzzles"]) == 2
        assert data["total"] == 5
        assert data["offset"] == 3

    def test_offset_beyond_total(self, client, db_session):
        """Offset past total returns empty list."""
        _seed_puzzles(db_session, count=3)
        response = client.get("/puzzles/list?username=testuser&limit=50&offset=100")
        data = response.json()
        assert len(data["puzzles"]) == 0
        assert data["total"] == 3


# ---- Available motifs tests ----


class TestAvailableMotifs:
    def test_returns_distinct_motifs(self, client, db_session):
        """available_motifs contains unique motifs across all user puzzles."""
        _create_puzzle(db_session, "p-1")
        _create_stats(db_session, "p-1", primary_motif="Fork")
        _create_puzzle(db_session, "p-2")
        _create_stats(db_session, "p-2", primary_motif="Pin")
        _create_puzzle(db_session, "p-3")
        _create_stats(db_session, "p-3", primary_motif="Fork")  # duplicate
        _create_puzzle(db_session, "p-4")
        _create_stats(db_session, "p-4", primary_motif=None)  # null excluded
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser")
        assert response.json()["available_motifs"] == ["Fork", "Pin"]

    def test_motifs_sorted(self, client, db_session):
        """Motifs are sorted alphabetically."""
        _create_puzzle(db_session, "p-z")
        _create_stats(db_session, "p-z", primary_motif="Zugzwang")
        _create_puzzle(db_session, "p-a")
        _create_stats(db_session, "p-a", primary_motif="Attack")
        db_session.commit()

        response = client.get("/puzzles/list?username=testuser")
        assert response.json()["available_motifs"] == ["Attack", "Zugzwang"]

    def test_motifs_unaffected_by_filters(self, client, db_session):
        """available_motifs reflects ALL puzzles, not just filtered ones."""
        _create_puzzle(db_session, "p-fork", swing=1.0)
        _create_stats(db_session, "p-fork", primary_motif="Fork")
        _create_puzzle(db_session, "p-pin", swing=7.0)
        _create_stats(db_session, "p-pin", primary_motif="Pin")
        db_session.commit()

        # Filter by difficulty=easy — only p-fork matches, but motifs should still show both
        response = client.get("/puzzles/list?username=testuser&difficulty=easy")
        assert response.json()["available_motifs"] == ["Fork", "Pin"]


class TestDisplayNameOnListRoutes:
    """Step 1's list half: every puzzle-returning route resolves one name.

    The property that matters here is not the string -- `test_provenance.py`
    covers composition -- it is that resolving it for N puzzles costs a join
    rather than a query per row.
    """

    def test_the_list_route_serves_display_name(self, client, db_session):
        _create_puzzle(db_session, "p-list")
        _create_stats(db_session, "p-list", title="Named", primary_motif="Pin")

        body = client.get("/puzzles/list?username=testuser").json()
        item = next(p for p in body["puzzles"] if p["id"] == "p-list")

        # Invisible by design while every row still has a title.
        assert item["display_name"] == item["title"] == "Named"

    def test_a_puzzle_with_no_title_falls_back_to_provenance(self, client, db_session):
        """The state the corpus reaches after rollout step 6. Without a
        fallback the Library would render a column of blanks."""
        _create_puzzle(db_session, "p-bare")
        _create_stats(db_session, "p-bare", title=None, primary_motif="Pin")

        body = client.get("/puzzles/list?username=testuser").json()
        item = next(p for p in body["puzzles"] if p["id"] == "p-bare")

        assert item["title"] is None
        assert item["display_name"]
        assert "move " in item["display_name"]

    def test_resolving_n_names_does_not_cost_n_queries(self, client, db_session):
        """The regression this PR exists to avoid.

        A `db.get` per row for the game would satisfy every other assertion
        here and quietly scale with the page. Asserting *invariance* rather
        than a magic budget: the same request over 3 puzzles and over 12 must
        issue the same number of statements. A budget would have to be
        re-tuned whenever an unrelated query is added; this would not.
        """
        from sqlalchemy import event

        statements: list[str] = []
        engine = db_session.get_bind()

        def record(conn, cursor, statement, *args):
            statements.append(statement.strip().split()[0].upper())

        def count_for(n: int) -> int:
            statements.clear()
            # Cold identity map, or this test cannot fail. `db.get` answers
            # from the session's identity map without emitting SQL, and the
            # fixture created these rows in the very session the route reuses
            # -- so a per-row `db.get` would issue zero extra statements here
            # while being a true N+1 in production, where every request gets a
            # fresh session. Verified: without this line the N+1 mutant passes.
            db_session.expire_all()
            event.listen(engine, "before_cursor_execute", record)
            try:
                body = client.get(f"/puzzles/list?username=testuser&limit={n}").json()
            finally:
                event.remove(engine, "before_cursor_execute", record)
            assert len(body["puzzles"]) == n
            assert all(p["display_name"] for p in body["puzzles"])
            return len(statements)

        for i in range(12):
            pid = f"p-many-{i:02d}"
            _create_puzzle(db_session, pid)
            _create_stats(db_session, pid, title=None, primary_motif="Pin")

        assert count_for(12) == count_for(3)


class TestLibrarySearchCoversProvenance:
    """Rollout step 2, §8: search must cover provenance, or say what it covers.

    Puzzle ids here are deliberately opaque (`pzl-a1`, not `p-sicilian`): the
    predicate also matches on id, so an id containing the search term makes
    these tests pass whether or not the opening term exists. Verified -- with
    descriptive ids, removing the opening term left all four green.

    It does both, partially by necessity. Provenance is derived and never
    stored, so the composed string is not matchable in SQL; the opening is the
    one component that IS stored, and the one a user would type. The
    placeholder was changed to name what is actually searched rather than
    promising more.
    """

    def test_search_matches_the_opening(self, client, db_session):
        _create_puzzle(db_session, "pzl-a1")
        _create_stats(db_session, "pzl-a1", title=None, primary_motif="Pin")
        _create_diagnosis(db_session, "pzl-a1", opening_name="Sicilian Defense B27")
        _create_puzzle(db_session, "pzl-a2")
        _create_stats(db_session, "pzl-a2", title=None, primary_motif="Pin")

        body = client.get("/puzzles/list?username=testuser&q=sicilian").json()

        assert [p["id"] for p in body["puzzles"]] == ["pzl-a1"]

    def test_search_still_matches_a_nickname(self, client, db_session):
        _create_puzzle(db_session, "pzl-b1")
        _create_stats(db_session, "pzl-b1", title="Bishop Had Bigger Plans")

        body = client.get("/puzzles/list?username=testuser&q=bishop").json()

        assert [p["id"] for p in body["puzzles"]] == ["pzl-b1"]

    def test_a_null_title_no_longer_makes_search_id_only(self, client, db_session):
        """The degradation §8 names. Before the opening term, a corpus with
        NULL titles could only be searched by hex id, while the box invited a
        name."""
        _create_puzzle(db_session, "pzl-c1")
        _create_stats(db_session, "pzl-c1", title=None, primary_motif="Pin")
        _create_diagnosis(db_session, "pzl-c1", opening_name="Najdorf Variation")

        body = client.get("/puzzles/list?username=testuser&q=najdorf").json()

        assert [p["id"] for p in body["puzzles"]] == ["pzl-c1"]

    def test_a_puzzle_with_no_diagnosis_is_simply_not_matched(self, client, db_session):
        """The diagnosis join is OUTER, so a missing row must not drop the
        puzzle from unfiltered listings -- only from opening searches."""
        _create_puzzle(db_session, "pzl-d1")
        _create_stats(db_session, "pzl-d1", title=None, primary_motif="Pin")

        assert (
            client.get("/puzzles/list?username=testuser&q=sicilian").json()["puzzles"]
            == []
        )
        listed = client.get("/puzzles/list?username=testuser").json()["puzzles"]
        assert "pzl-d1" in [p["id"] for p in listed]


class TestGetPuzzleDetail:
    """Tests for GET /puzzles/{puzzle_id}."""

    def test_returns_puzzle_by_id(self, client, db_session):
        _create_puzzle(db_session, "p-detail", swing=3.0)
        _create_stats(
            db_session,
            "p-detail",
            title="Detail Test",
            primary_motif="Fork",
            attempts=5,
            pass_count=4,
            fail_count=1,
        )
        db_session.commit()

        response = client.get("/puzzles/p-detail?username=testuser")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "p-detail"
        assert data["title"] == "Detail Test"
        assert data["primary_motif"] == "Fork"
        assert data["difficulty"] == "medium"
        assert data["attempts"] == 5
        assert data["pass_count"] == 4
        assert data["fail_count"] == 1

    def test_puzzle_without_stats(self, client, db_session):
        _create_puzzle(db_session, "p-nostats", swing=1.0)
        db_session.commit()

        response = client.get("/puzzles/p-nostats?username=testuser")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "new"
        assert data["attempts"] == 0
        assert data["title"] is None

    def test_puzzle_not_found(self, client, db_session):
        response = client.get("/puzzles/nonexistent?username=testuser")
        assert response.status_code == 404

    def test_puzzle_wrong_user(self, client, db_session):
        _create_puzzle(db_session, "p-alice", username="alice")
        db_session.commit()

        response = client.get("/puzzles/p-alice?username=bob")
        assert response.status_code == 404

    def test_missing_username(self, client):
        response = client.get("/puzzles/some-id")
        assert response.status_code == 422

    def test_response_shape(self, client, db_session):
        _create_puzzle(db_session, "p-shape", swing=6.0)
        _create_stats(db_session, "p-shape", title="Shape Test", primary_motif="Pin")
        db_session.commit()

        response = client.get("/puzzles/p-shape?username=testuser")
        data = response.json()
        expected_keys = {
            "id",
            "title",
            # What the client renders. Equal to `title` while every row has
            # one; diverges when titles become NULL by default (design §7).
            "display_name",
            "primary_motif",
            "difficulty",
            "swing",
            "fen",
            "side_to_move",
            "best_move_uci",
            "accept_moves_uci",
            "status",
            "attempts",
            "pass_count",
            "fail_count",
            "last_reviewed_at",
            "last_result",
            "next_due_at",
            "created_at",
            "diagnosis_summary",
        }
        assert set(data.keys()) == expected_keys
        # Step 1 is explicitly a no-op for what the user sees: a nickname wins
        # wherever one exists, and today one always does.
        assert data["display_name"] == data["title"] == "Shape Test"

    def test_detail_diagnosis_summary_is_null(self, client, db_session):
        """Detail endpoint always returns null for diagnosis_summary.

        The detail page uses GET /puzzles/{id}/diagnosis for full data; the
        summary field is list-only and intentionally absent from this response.
        """
        _create_puzzle(db_session, "p-dx-detail")
        _create_diagnosis(db_session, "p-dx-detail")
        db_session.commit()

        data = client.get("/puzzles/p-dx-detail?username=testuser").json()
        assert data["diagnosis_summary"] is None


class TestCauseFilter:
    """The destination of the Insights "practise this" links.

    Those cards link to ``/library?cause=…``. Asserting the link's href in the
    frontend proves nothing on its own — these tests cover the other half, that
    the destination actually narrows the list.
    """

    def test_narrows_the_list_to_one_cause(self, client, db_session):
        _seed_puzzles(db_session, count=3)
        _create_diagnosis(db_session, "p-0", primary_cause="loose_piece_awareness")
        _create_diagnosis(db_session, "p-1", primary_cause="king_safety_blindness")
        _create_diagnosis(db_session, "p-2", primary_cause="loose_piece_awareness")
        db_session.commit()

        res = client.get("/puzzles/list?username=testuser&cause=loose_piece_awareness")
        assert res.status_code == 200
        body = res.json()
        assert {p["id"] for p in body["puzzles"]} == {"p-0", "p-2"}
        assert body["total"] == 2

    def test_excludes_undiagnosed_puzzles(self, client, db_session):
        # An undiagnosed puzzle has no cause, so it cannot match one. Without
        # this the outer join would let every un-analysed puzzle through.
        _seed_puzzles(db_session, count=2)
        _create_diagnosis(db_session, "p-0", primary_cause="loose_piece_awareness")
        db_session.commit()

        body = client.get(
            "/puzzles/list?username=testuser&cause=loose_piece_awareness"
        ).json()
        assert [p["id"] for p in body["puzzles"]] == ["p-0"]

    def test_a_user_correction_beats_the_computed_cause(self, client, db_session):
        # Same precedence the aggregates use: if the user says the diagnosis was
        # wrong, filtering must follow their word, both ways.
        _seed_puzzles(db_session, count=1)
        _create_diagnosis(
            db_session,
            "p-0",
            primary_cause="loose_piece_awareness",
            user_confirmed_cause="king_safety_blindness",
        )
        db_session.commit()

        confirmed = client.get(
            "/puzzles/list?username=testuser&cause=king_safety_blindness"
        ).json()
        assert [p["id"] for p in confirmed["puzzles"]] == ["p-0"]

        computed = client.get(
            "/puzzles/list?username=testuser&cause=loose_piece_awareness"
        ).json()
        assert computed["puzzles"] == []

    def test_accepts_several_causes(self, client, db_session):
        _seed_puzzles(db_session, count=3)
        _create_diagnosis(db_session, "p-0", primary_cause="loose_piece_awareness")
        _create_diagnosis(db_session, "p-1", primary_cause="king_safety_blindness")
        _create_diagnosis(db_session, "p-2", primary_cause="endgame_technique_gap")
        db_session.commit()

        body = client.get(
            "/puzzles/list?username=testuser"
            "&cause=loose_piece_awareness,king_safety_blindness"
        ).json()
        assert {p["id"] for p in body["puzzles"]} == {"p-0", "p-1"}

    def test_combines_with_the_other_filters(self, client, db_session):
        # A cause filter that ignored the status filter would quietly widen the
        # result the moment a user arrived from Insights with both set.
        _seed_puzzles(db_session, count=3)
        for pid in ("p-0", "p-1", "p-2"):
            _create_diagnosis(db_session, pid, primary_cause="loose_piece_awareness")
        db_session.commit()

        unfiltered = client.get(
            "/puzzles/list?username=testuser&cause=loose_piece_awareness"
        ).json()
        combined = client.get(
            "/puzzles/list?username=testuser"
            "&cause=loose_piece_awareness&difficulty=hard"
        ).json()
        assert combined["total"] < unfiltered["total"]
        assert all(p["difficulty"] == "hard" for p in combined["puzzles"])

    def test_is_scoped_to_the_requesting_user(self, client, db_session):
        # The diagnosis table is keyed (puzzle_id, username); a join that
        # forgot the username would surface another player's classification.
        _seed_puzzles(db_session, count=1, username="testuser")
        _create_diagnosis(
            db_session, "p-0", username="other", primary_cause="king_safety_blindness"
        )
        db_session.commit()

        body = client.get(
            "/puzzles/list?username=testuser&cause=king_safety_blindness"
        ).json()
        assert body["puzzles"] == []


class TestAvailableCauses:
    def test_offers_only_causes_that_would_return_something(self, client, db_session):
        _seed_puzzles(db_session, count=2)
        _create_diagnosis(db_session, "p-0", primary_cause="loose_piece_awareness")
        db_session.commit()

        body = client.get("/puzzles/list?username=testuser").json()
        assert body["available_causes"] == [
            {"value": "loose_piece_awareness", "label": "Loose piece awareness"}
        ]

    def test_carries_the_label_so_the_ui_never_shows_a_slug(self, client, db_session):
        _seed_puzzles(db_session, count=1)
        _create_diagnosis(db_session, "p-0", primary_cause="king_safety_blindness")
        db_session.commit()

        body = client.get("/puzzles/list?username=testuser").json()
        assert body["available_causes"][0]["label"] != "king_safety_blindness"

    def test_reports_the_confirmed_cause_not_the_computed_one(self, client, db_session):
        _seed_puzzles(db_session, count=1)
        _create_diagnosis(
            db_session,
            "p-0",
            primary_cause="loose_piece_awareness",
            user_confirmed_cause="king_safety_blindness",
        )
        db_session.commit()

        body = client.get("/puzzles/list?username=testuser").json()
        values = [c["value"] for c in body["available_causes"]]
        assert values == ["king_safety_blindness"]

    def test_offers_exactly_what_the_insights_counts_are_built_from(
        self, client, db_session
    ):
        # The two surfaces must agree: what Insights counts under a cause is
        # what the library filter returns for it. Comparing against the
        # repository the Insights endpoint uses pins them to one predicate
        # rather than to two queries that happen to match today.
        from services.api.storage.diagnosis_repository import DiagnosisRepository

        _seed_puzzles(db_session, count=3)
        _create_diagnosis(db_session, "p-0", primary_cause="loose_piece_awareness")
        _create_diagnosis(
            db_session,
            "p-1",
            primary_cause="king_safety_blindness",
            user_confirmed_cause="loose_piece_awareness",
        )
        _create_diagnosis(
            db_session,
            "p-2",
            primary_cause=None,
            status=DiagnosisStatus.UNAVAILABLE,
            error="illegal move for fen",
        )
        db_session.commit()

        counts = dict(DiagnosisRepository(db_session).cause_counts("testuser"))
        body = client.get("/puzzles/list?username=testuser").json()

        assert {c["value"] for c in body["available_causes"]} == set(counts)
        for value, expected in counts.items():
            listed = client.get(f"/puzzles/list?username=testuser&cause={value}").json()
            assert listed["total"] == expected

    def test_is_scoped_to_the_requesting_user(self, client, db_session):
        _seed_puzzles(db_session, count=1, username="testuser")
        _create_diagnosis(
            db_session, "p-0", username="other", primary_cause="king_safety_blindness"
        )
        db_session.commit()

        body = client.get("/puzzles/list?username=testuser").json()
        assert body["available_causes"] == []


class TestPhaseAndOpeningFilters:
    """The remaining two filters the spec asked for.

    Both live on the diagnosis rather than the puzzle, so both narrow to
    analysable rows the same way the cause filter does, and all three compose.
    """

    def _diag(
        self,
        db,
        pid,
        *,
        phase="middlegame",
        opening=None,
        cause="loose_piece_awareness",
    ):
        db.add(
            PuzzleDiagnosis(
                puzzle_id=pid,
                username="testuser",
                status=DiagnosisStatus.OK,
                primary_motif="fork",
                primary_cause=cause,
                phase=phase,
                opening_family=opening,
                source="rules",
                evidence_json=[],
            )
        )
        db.flush()

    def test_narrows_by_phase(self, client, db_session):
        _seed_puzzles(db_session, count=3)
        self._diag(db_session, "p-0", phase="opening")
        self._diag(db_session, "p-1", phase="endgame")
        self._diag(db_session, "p-2", phase="opening")
        db_session.commit()

        body = client.get("/puzzles/list?username=testuser&phase=opening").json()
        assert {p["id"] for p in body["puzzles"]} == {"p-0", "p-2"}

    def test_phase_matching_ignores_case(self, client, db_session):
        _seed_puzzles(db_session, count=1)
        self._diag(db_session, "p-0", phase="endgame")
        db_session.commit()
        body = client.get("/puzzles/list?username=testuser&phase=ENDGAME").json()
        assert [p["id"] for p in body["puzzles"]] == ["p-0"]

    def test_narrows_by_opening_family(self, client, db_session):
        # The spec's "common in Sicilian / Italian structures" needs this.
        _seed_puzzles(db_session, count=2)
        self._diag(db_session, "p-0", opening="Sicilian Defense")
        self._diag(db_session, "p-1", opening="Italian Game")
        db_session.commit()

        body = client.get(
            "/puzzles/list?username=testuser&opening=Sicilian%20Defense"
        ).json()
        assert [p["id"] for p in body["puzzles"]] == ["p-0"]

    def test_an_unclassified_game_is_excluded_rather_than_grouped(
        self, client, db_session
    ):
        # A game that never left book unclassified must not be swept into some
        # other family — an absence is not a match.
        _seed_puzzles(db_session, count=2)
        self._diag(db_session, "p-0", opening="Sicilian Defense")
        self._diag(db_session, "p-1", opening=None)
        db_session.commit()

        body = client.get(
            "/puzzles/list?username=testuser&opening=Sicilian%20Defense"
        ).json()
        assert [p["id"] for p in body["puzzles"]] == ["p-0"]
        assert body["available_openings"] == ["Sicilian Defense"]

    def test_the_three_diagnosis_filters_compose(self, client, db_session):
        _seed_puzzles(db_session, count=3)
        self._diag(db_session, "p-0", phase="opening", opening="Sicilian Defense")
        self._diag(db_session, "p-1", phase="endgame", opening="Sicilian Defense")
        self._diag(
            db_session,
            "p-2",
            phase="opening",
            opening="Sicilian Defense",
            cause="king_safety_blindness",
        )
        db_session.commit()

        body = client.get(
            "/puzzles/list?username=testuser&opening=Sicilian%20Defense"
            "&phase=opening&cause=loose_piece_awareness"
        ).json()
        assert [p["id"] for p in body["puzzles"]] == ["p-0"]

    def test_available_openings_is_scoped_to_the_user(self, client, db_session):
        _seed_puzzles(db_session, count=1, username="testuser")
        db_session.add(
            PuzzleDiagnosis(
                puzzle_id="p-0",
                username="other",
                status=DiagnosisStatus.OK,
                primary_cause="loose_piece_awareness",
                opening_family="Sicilian Defense",
                source="rules",
                evidence_json=[],
            )
        )
        db_session.commit()
        body = client.get("/puzzles/list?username=testuser").json()
        assert body["available_openings"] == []


class TestOpeningPractice:
    """What an explorer line can actually offer, and at what granularity.

    The join key is the part of the name before the colon. That derivation
    lives in exactly one place; these tests exist partly to keep it there.
    """

    def _diag(self, db, pid, name):
        family = name.split(":", 1)[0].strip() if name else None
        db.add(
            PuzzleDiagnosis(
                puzzle_id=pid,
                username="testuser",
                status=DiagnosisStatus.OK,
                primary_cause="loose_piece_awareness",
                opening_name=name,
                opening_family=family,
                source="rules",
                evidence_json=[],
            )
        )
        db.flush()

    def test_offers_the_line_when_it_has_enough(self, client, db_session):
        _seed_puzzles(db_session, count=4)
        for i in range(3):
            self._diag(db_session, f"p-{i}", "Sicilian Defense: Najdorf Variation")
        self._diag(db_session, "p-3", "Sicilian Defense: Dragon Variation")
        db_session.commit()

        body = client.get(
            "/users/testuser/opening-practice"
            "?opening_name=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        assert body["scope"] == "line"
        assert body["line_count"] == 3
        assert body["family_count"] == 4
        assert body["opening_family"] == "Sicilian Defense"

    def test_falls_back_to_the_family_when_the_line_is_thin(self, client, db_session):
        # Two puzzles is not practice — a session that keeps repeating the same
        # position is worse than a broader one.
        _seed_puzzles(db_session, count=4)
        for i in range(2):
            self._diag(db_session, f"p-{i}", "Sicilian Defense: Najdorf Variation")
        for i in range(2, 4):
            self._diag(db_session, f"p-{i}", "Sicilian Defense: Dragon Variation")
        db_session.commit()

        body = client.get(
            "/users/testuser/opening-practice"
            "?opening_name=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        assert body["scope"] == "family"
        assert body["line_count"] == 2
        assert body["family_count"] == 4

    def test_reports_none_when_the_opening_is_unplayed(self, client, db_session):
        # A link that leads to an empty list is worse than no link, so the
        # caller is told there is nothing rather than left to discover it.
        _seed_puzzles(db_session, count=2)
        self._diag(db_session, "p-0", "French Defense: Advance Variation")
        db_session.commit()

        body = client.get(
            "/users/testuser/opening-practice"
            "?opening_name=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        assert body["scope"] == "none"
        assert body["line_count"] == 0
        assert body["family_count"] == 0

    def test_derives_the_family_server_side(self, client, db_session):
        # The frontend must never re-implement the split. If it did, this is
        # the contract that would have let the two drift.
        _seed_puzzles(db_session, count=1)
        db_session.commit()
        body = client.get(
            "/users/testuser/opening-practice"
            "?opening_name=Queen%27s%20Gambit%20Declined:%20Exchange%20Variation"
        ).json()
        assert body["opening_family"] == "Queen's Gambit Declined"

    def test_a_family_only_name_is_its_own_family(self, client, db_session):
        _seed_puzzles(db_session, count=1)
        self._diag(db_session, "p-0", "Bird Opening")
        db_session.commit()
        body = client.get(
            "/users/testuser/opening-practice?opening_name=Bird%20Opening"
        ).json()
        assert body["opening_family"] == "Bird Opening"
        assert body["line_count"] == 1

    def test_is_scoped_to_the_requesting_user(self, client, db_session):
        _seed_puzzles(db_session, count=1, username="testuser")
        db_session.add(
            PuzzleDiagnosis(
                puzzle_id="p-0",
                username="other",
                status=DiagnosisStatus.OK,
                primary_cause="loose_piece_awareness",
                opening_name="Sicilian Defense: Najdorf Variation",
                opening_family="Sicilian Defense",
                source="rules",
                evidence_json=[],
            )
        )
        db_session.commit()
        body = client.get(
            "/users/testuser/opening-practice"
            "?opening_name=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        assert body["scope"] == "none"

    def test_the_promised_counts_match_what_the_filters_return(
        self, client, db_session
    ):
        # The number in "Practise this line (N)" and the number of puzzles the
        # filter actually yields are computed by different queries. Comparing
        # them against each other, rather than against a literal, is what stops
        # the two drifting — a count that overstates is the same class of defect
        # as a link to an empty list.
        _seed_puzzles(db_session, count=5)
        for i in range(3):
            self._diag(db_session, f"p-{i}", "Sicilian Defense: Najdorf Variation")
        self._diag(db_session, "p-3", "Sicilian Defense: Dragon Variation")
        self._diag(db_session, "p-4", "French Defense: Advance Variation")
        db_session.commit()

        practice = client.get(
            "/users/testuser/opening-practice"
            "?opening_name=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        by_line = client.get(
            "/puzzles/list?username=testuser"
            "&opening_line=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        by_family = client.get(
            "/puzzles/list?username=testuser&opening=Sicilian%20Defense"
        ).json()

        assert practice["line_count"] == by_line["total"]
        assert practice["family_count"] == by_family["total"]


class TestOpeningLineFilter:
    def _diag(self, db, pid, name):
        db.add(
            PuzzleDiagnosis(
                puzzle_id=pid,
                username="testuser",
                status=DiagnosisStatus.OK,
                primary_cause="loose_piece_awareness",
                opening_name=name,
                opening_family=name.split(":", 1)[0].strip(),
                source="rules",
                evidence_json=[],
            )
        )
        db.flush()

    def test_narrows_to_one_line_within_a_family(self, client, db_session):
        _seed_puzzles(db_session, count=3)
        self._diag(db_session, "p-0", "Sicilian Defense: Najdorf Variation")
        self._diag(db_session, "p-1", "Sicilian Defense: Dragon Variation")
        self._diag(db_session, "p-2", "Sicilian Defense: Najdorf Variation")
        db_session.commit()

        body = client.get(
            "/puzzles/list?username=testuser"
            "&opening_line=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        assert {p["id"] for p in body["puzzles"]} == {"p-0", "p-2"}

    def test_is_narrower_than_the_family_filter(self, client, db_session):
        _seed_puzzles(db_session, count=3)
        self._diag(db_session, "p-0", "Sicilian Defense: Najdorf Variation")
        self._diag(db_session, "p-1", "Sicilian Defense: Dragon Variation")
        self._diag(db_session, "p-2", "Sicilian Defense: Dragon Variation")
        db_session.commit()

        line = client.get(
            "/puzzles/list?username=testuser"
            "&opening_line=Sicilian%20Defense:%20Najdorf%20Variation"
        ).json()
        family = client.get(
            "/puzzles/list?username=testuser&opening=Sicilian%20Defense"
        ).json()
        assert line["total"] == 1
        assert family["total"] == 3
