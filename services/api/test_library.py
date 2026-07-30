"""Tests for the GET /puzzles/list (Library) endpoint."""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.main import app, get_db
from services.api.models import (
    Base,
    DiagnosisStatus,
    Game,
    PuzzleDiagnosis,
    PuzzleStats,
)
from services.api.models import Puzzle as PuzzleModel


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


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


def _create_stats(
    db,
    puzzle_id: str,
    username: str = "testuser",
    title: str | None = "Test Puzzle",
    primary_motif: str | None = None,
    attempts: int = 0,
    pass_count: int = 0,
    fail_count: int = 0,
    last_reviewed_at: datetime | None = None,
    last_result: str | None = None,
    next_due_at: datetime | None = None,
):
    """Helper: create a PuzzleStats row."""
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
):
    db.add(
        PuzzleDiagnosis(
            puzzle_id=puzzle_id,
            username=username,
            status=status,
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
        payload = str(puzzle)
        assert "Qxd5" not in payload
        assert "d1d5" not in payload
        assert "from d1 to d5" not in payload
        assert "training_recommendation" not in payload
        assert "explanation" not in payload
        assert "evidence" not in payload

    def test_missing_diagnosis_returns_null_summary(self, client, db_session):
        _create_puzzle(db_session, "p-undx")
        db_session.commit()

        puzzle = client.get("/puzzles/list?username=testuser").json()["puzzles"][0]

        assert puzzle["diagnosis_summary"] is None

    def test_user_confirmed_cause_wins_in_list_summary(self, client, db_session):
        _create_puzzle(db_session, "p-confirmed")
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
