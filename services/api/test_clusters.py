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
    """A missing leg must not produce two identical queries."""
    full = tiers_for(ClusterKey("c", "Fork", "middlegame"))
    assert full == [
        MatchTier.EXACT,
        MatchTier.CAUSE_AND_MOTIF,
        MatchTier.CAUSE_AND_PHASE,
        MatchTier.CAUSE_ONLY,
    ]

    # Phase is recorded on every diagnosed puzzle while a usable motif is on
    # roughly a quarter, so this is the common shape — it must still offer a
    # tighter grouping than "same cause, somewhere".
    no_motif = tiers_for(ClusterKey("c", None, "middlegame"))
    assert no_motif == [MatchTier.CAUSE_AND_PHASE, MatchTier.CAUSE_ONLY]

    no_phase = tiers_for(ClusterKey("c", "Fork", None))
    assert no_phase == [MatchTier.CAUSE_AND_MOTIF, MatchTier.CAUSE_ONLY]


def test_unclassified_is_not_a_weakness():
    """The classifier declining to name a cause is not a shared weakness.

    Every other cause surface excludes it; grouping by it would collect every
    unexplained mistake into one bucket and call it a pattern.
    """
    assert key_for("unclassified", "Fork", "middlegame") is None


def test_blunder_is_treated_as_no_motif_recorded():
    """ "blunder" is assign_primary_motif's fallback, not a tactic.

    It is 45% of diagnoses, so honouring it would report an `exact` tactical
    match for the plurality of the corpus while carrying no tactical
    information at all.
    """
    key = key_for("calculation_stopped_early", "blunder", "middlegame")
    assert key is not None
    assert key.motif is None
    assert tiers_for(key) == [MatchTier.CAUSE_AND_PHASE, MatchTier.CAUSE_ONLY]

    # Case-insensitive on the check, but a real motif keeps its stored spelling
    # because it is used verbatim as a SQL equality predicate.
    assert key_for("c", "BLUNDER", "middlegame").motif is None
    assert key_for("c", "Fork", "middlegame").motif == "Fork"


def test_describe_uses_the_house_cause_label_not_its_own():
    """The diagnosis card renders directly above this one on the same page.

    Owning a second translation here put "Loose piece awareness" and "loose
    piece awareness" on one screen.

    The motif is deliberately a real snake_case key from the corpus. An earlier
    version of this test used "Fork", which has no underscore, so the
    no-slug assertion passed while `hanging_piece` was shipping raw.
    """
    key = ClusterKey("calculation_stopped_early", "hanging_piece", "middlegame")
    exact = describe(key, MatchTier.EXACT)
    assert "Calculation stopped early" in exact
    assert "_" not in exact
    assert "hanging piece" in exact and "middlegame" in exact


def test_describe_never_claims_more_than_the_tier_matched():
    key = ClusterKey("calculation_stopped_early", "hanging_piece", "middlegame")

    # Motif matched, phase did not.
    assert "middlegame" not in describe(key, MatchTier.CAUSE_AND_MOTIF)
    assert "_" not in describe(key, MatchTier.CAUSE_AND_MOTIF)
    # Phase matched, motif did not.
    phase_only = describe(key, MatchTier.CAUSE_AND_PHASE)
    assert "hanging" not in phase_only and "middlegame" in phase_only
    # The widest tier is reached whenever nothing tighter matched, which
    # includes puzzles whose motif was never recorded — so it must not assert
    # the positions differ, which nothing checked.
    widest = describe(key, MatchTier.CAUSE_ONLY)
    assert "hanging" not in widest
    assert "different kind of position" not in widest


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
    assert body["cause_label"] == "Calculation stopped early"
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


def test_cause_and_phase_tier_groups_puzzles_with_no_usable_motif(db_session):
    """The common shape: phase is always recorded, a real motif rarely is.

    Before this tier existed these fell straight to CAUSE_ONLY and were told
    their siblings came from "a different kind of position" — when they in fact
    shared the phase, and nothing had checked the position at all.
    """
    _puzzle(db_session, "p1")
    _diagnosis(db_session, "p1", motif="blunder", phase="middlegame")
    _puzzle(db_session, "p2")
    _diagnosis(db_session, "p2", motif=None, phase="middlegame")
    _puzzle(db_session, "p3")
    _diagnosis(db_session, "p3", motif="blunder", phase="endgame")

    repo = DiagnosisRepository(db_session)
    key = repo.cluster_key_for(USER, "p1")
    assert key.motif is None  # "blunder" is not a motif

    ids, tier = repo.similar_puzzle_ids(USER, "p1", key, 5)
    assert tier is MatchTier.CAUSE_AND_PHASE
    assert ids == ["p2"]  # p3 shares the cause but not the phase


def test_siblings_are_ordered_by_puzzle_recency_not_diagnosis_time(db_session):
    """A backfill stamps every diagnosis within seconds, in scan order.

    Ordering by the diagnosis timestamp is therefore arbitrary, and the card
    displays the puzzle's date — so it would be labelled with a date it was not
    sorted by.
    """
    from datetime import timedelta

    _puzzle(db_session, "old", created_days_ago=30)
    _puzzle(db_session, "recent", created_days_ago=1)
    _puzzle(db_session, "anchor", created_days_ago=10)
    # Diagnose in the opposite order to puzzle recency, as a backfill would.
    now = datetime.now(timezone.utc)
    for i, pid in enumerate(("anchor", "recent", "old")):
        db_session.add(
            PuzzleDiagnosis(
                puzzle_id=pid,
                username=USER,
                status=DiagnosisStatus.OK,
                primary_cause="calculation_stopped_early",
                primary_motif="Fork",
                phase="middlegame",
                created_at=now + timedelta(seconds=i),
            )
        )
    db_session.commit()

    repo = DiagnosisRepository(db_session)
    key = repo.cluster_key_for(USER, "anchor")
    ids, _ = repo.similar_puzzle_ids(USER, "anchor", key, 5)

    assert ids == ["recent", "old"]


def test_similar_enforces_ownership(client, db_session):
    """Regression guard for the house tenant-isolation pattern.

    A foreign puzzle must be indistinguishable from a nonexistent one, so the
    endpoint cannot be used to probe whether another account owns a given id.
    """
    _puzzle(db_session, "mine")
    _diagnosis(db_session, "mine")
    _puzzle(db_session, "theirs", username=OTHER)
    _diagnosis(db_session, "theirs", username=OTHER)

    res = client.get(f"/puzzles/theirs/similar?username={USER}")

    assert res.status_code == 404
    assert res.json()["detail"] == "Puzzle not found"
