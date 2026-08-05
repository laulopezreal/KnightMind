"""Tests for weakness clusters and the /puzzles/{id}/similar surface."""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from services.api.diagnosis.clusters import (  # noqa: E402
    ClusterKey,
    MatchTier,
    describe,
    key_for,
    tiers_for,
)
from services.api.main import app, get_db  # noqa: E402
from services.api.models import (  # noqa: E402
    Base,
    DiagnosisStatus,
    PuzzleDiagnosis,
    PuzzleStats,
)
from services.api.models import Puzzle as PuzzleModel  # noqa: E402
from services.api.storage.diagnosis_repository import DiagnosisRepository  # noqa: E402

USER = "clusteruser"
OTHER = "someoneelse"
FEN = "6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1"


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


_PLY = iter(range(1, 10_000))


def _puzzle(db, puzzle_id, username=USER, created_days_ago=0, swing=3.0):
    # (username, source_game_id, ply) is uniquely indexed, so every fixture
    # puzzle needs its own ply or the second insert collides.
    db.add(
        PuzzleModel(
            id=puzzle_id,
            username=username,
            source_game_id="__manual__",
            ply=next(_PLY),
            fen=FEN,
            side_to_move="white",
            played_move_uci="d1d2",
            best_move_uci="d1d5",
            eval_before=0.5,
            eval_after=-swing,
            swing=swing,
            created_at=datetime.now(timezone.utc) - timedelta(days=created_days_ago),
        )
    )
    db.add(
        PuzzleStats(
            puzzle_id=puzzle_id,
            username=username,
            title=f"Title {puzzle_id}",
            primary_motif="Fork",
            attempts=1,
            pass_count=0,
            fail_count=1,
        )
    )
    db.commit()


def _diagnosis(
    db,
    puzzle_id,
    username=USER,
    cause="calculation_stopped_early",
    motif="Fork",
    phase="middlegame",
    confirmed=None,
    status=DiagnosisStatus.OK,
):
    db.add(
        PuzzleDiagnosis(
            puzzle_id=puzzle_id,
            username=username,
            status=status,
            primary_cause=cause,
            primary_motif=motif,
            phase=phase,
            user_confirmed_cause=confirmed,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


# -- key and tier logic -------------------------------------------------


def test_key_requires_a_cause():
    """No cause means no weakness to group by."""
    assert key_for(None, "Fork", "middlegame") is None
    assert key_for("", "Fork", "middlegame") is None


def test_tiers_skip_steps_that_would_restate_a_narrower_one():
    """A missing motif must not produce two identical queries."""
    full = tiers_for(ClusterKey("c", "Fork", "middlegame"))
    assert full == [MatchTier.EXACT, MatchTier.CAUSE_AND_MOTIF, MatchTier.CAUSE_ONLY]

    no_motif = tiers_for(ClusterKey("c", None, "middlegame"))
    assert no_motif == [MatchTier.CAUSE_ONLY]

    no_phase = tiers_for(ClusterKey("c", "Fork", None))
    assert no_phase == [MatchTier.CAUSE_AND_MOTIF, MatchTier.CAUSE_ONLY]


def test_describe_reads_as_a_sentence_without_taxonomy_slugs():
    key = ClusterKey("calculation_stopped_early", "Fork", "middlegame")
    exact = describe(key, MatchTier.EXACT)
    assert "calculation stopped early" in exact
    assert "_" not in exact
    # The widest tier must not imply a motif or phase match it did not make.
    widest = describe(key, MatchTier.CAUSE_ONLY)
    assert "Fork" not in widest


# -- repository ---------------------------------------------------------


def test_exact_match_preferred_over_looser_ones(db_session):
    _puzzle(db_session, "p1")
    _diagnosis(db_session, "p1")
    _puzzle(db_session, "p2")
    _diagnosis(db_session, "p2")  # same cause + motif + phase
    _puzzle(db_session, "p3")
    _diagnosis(db_session, "p3", phase="endgame")  # cause + motif only

    repo = DiagnosisRepository(db_session)
    key = repo.cluster_key_for(USER, "p1")
    ids, tier = repo.similar_puzzle_ids(USER, "p1", key, 5)

    assert tier is MatchTier.EXACT
    assert ids == ["p2"]


def test_widens_to_cause_only_when_nothing_tighter_exists(db_session):
    _puzzle(db_session, "p1")
    _diagnosis(db_session, "p1")
    _puzzle(db_session, "p2")
    _diagnosis(db_session, "p2", motif="Pin", phase="endgame")

    repo = DiagnosisRepository(db_session)
    key = repo.cluster_key_for(USER, "p1")
    ids, tier = repo.similar_puzzle_ids(USER, "p1", key, 5)

    assert tier is MatchTier.CAUSE_ONLY
    assert ids == ["p2"]


def test_a_user_correction_moves_the_puzzle_between_clusters(db_session):
    """The reason clusters are derived rather than stored.

    p2's computed cause differs from p1's, so they do not group — until the
    user relabels p2, at which point they must. A cluster id frozen at
    diagnosis time could not do this.
    """
    _puzzle(db_session, "p1")
    _diagnosis(db_session, "p1", cause="calculation_stopped_early")
    _puzzle(db_session, "p2")
    _diagnosis(db_session, "p2", cause="quiet_move_blindness")

    repo = DiagnosisRepository(db_session)
    key = repo.cluster_key_for(USER, "p1")
    ids, _ = repo.similar_puzzle_ids(USER, "p1", key, 5)
    assert ids == []

    repo.confirm_cause(USER, "p2", "calculation_stopped_early")
    db_session.commit()

    ids, tier = repo.similar_puzzle_ids(USER, "p1", key, 5)
    assert ids == ["p2"]
    assert tier is MatchTier.EXACT


def test_other_users_puzzles_are_never_siblings(db_session):
    _puzzle(db_session, "p1")
    _diagnosis(db_session, "p1")
    _puzzle(db_session, "x1", username=OTHER)
    _diagnosis(db_session, "x1", username=OTHER)

    repo = DiagnosisRepository(db_session)
    key = repo.cluster_key_for(USER, "p1")
    ids, _ = repo.similar_puzzle_ids(USER, "p1", key, 5)
    assert ids == []


def test_unanalysable_diagnoses_are_not_grouped(db_session):
    _puzzle(db_session, "p1")
    _diagnosis(db_session, "p1")
    _puzzle(db_session, "p2")
    _diagnosis(db_session, "p2", status=DiagnosisStatus.UNAVAILABLE)

    repo = DiagnosisRepository(db_session)
    key = repo.cluster_key_for(USER, "p1")
    ids, _ = repo.similar_puzzle_ids(USER, "p1", key, 5)
    assert ids == []


# -- endpoint -----------------------------------------------------------


def test_similar_never_ships_a_solution(client, db_session):
    """The whole point of the surface is browsing, so it carries no answers."""
    _puzzle(db_session, "p1")
    _diagnosis(db_session, "p1")
    _puzzle(db_session, "p2")
    _diagnosis(db_session, "p2")

    body = client.get(f"/puzzles/p1/similar?username={USER}").json()

    assert body["puzzles"]
    serialized = str(body)
    for leaked in ("best_move_uci", "accept_moves_uci", "solution_pv", "d1d5"):
        assert leaked not in serialized


def test_similar_reports_which_tier_answered(client, db_session):
    _puzzle(db_session, "p1")
    _diagnosis(db_session, "p1")
    _puzzle(db_session, "p2")
    _diagnosis(db_session, "p2", motif="Pin", phase="endgame")

    body = client.get(f"/puzzles/p1/similar?username={USER}").json()

    assert body["match"] == "cause_only"
    assert body["cause"] == "calculation_stopped_early"
    assert body["cause_label"] == "calculation stopped early"
    assert "Pin" not in body["reason"]


def test_undiagnosed_puzzle_returns_empty_not_an_error(client, db_session):
    _puzzle(db_session, "p1")

    res = client.get(f"/puzzles/p1/similar?username={USER}")

    assert res.status_code == 200
    assert res.json()["puzzles"] == []


def test_unknown_puzzle_is_404(client, db_session):
    assert client.get(f"/puzzles/nope/similar?username={USER}").status_code == 404


def test_limit_is_respected(client, db_session):
    _puzzle(db_session, "p1")
    _diagnosis(db_session, "p1")
    for i in range(2, 7):
        _puzzle(db_session, f"p{i}")
        _diagnosis(db_session, f"p{i}")

    body = client.get(f"/puzzles/p1/similar?username={USER}&n=2").json()

    assert len(body["puzzles"]) == 2
    assert "p1" not in [p["id"] for p in body["puzzles"]]
