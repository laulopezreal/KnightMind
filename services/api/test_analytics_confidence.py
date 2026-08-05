"""
Tests for the analytics min-sample thresholds (SCORECARD dim 18).

Two guarantees:
- the thresholds are env-overridable and fall back to documented defaults;
- each threshold gates its endpoint exactly at the boundary — at N-1 the metric
  is ``insufficient_data``, at N it is emitted. The N-1 (gated) side is already
  characterized in test_analytics_truthfulness.py; here we lock the env contract,
  the defaults, and the N (emitted) side.
"""

import importlib
import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from services.api import analytics_confidence
from services.api.db import get_db
from services.api.main import app
from services.api.models import (
    Game,
    Puzzle,
    PuzzleReview,
    PuzzleStats,
    RatingSnapshot,
)

_ENV_NAMES = (
    "ANALYTICS_MIN_REVIEWS_FORM_TREND",
    "ANALYTICS_MIN_REVIEWS_MOTIF_TREND",
    "ANALYTICS_MIN_ATTEMPTS_MOTIF_RANK",
    "ANALYTICS_MIN_GAMES_RATING_DRIVERS",
)


# ---------------------------------------------------------------------------
# Env contract: overrides are read, and defaults hold when unset.
# ---------------------------------------------------------------------------
def test_defaults_unchanged_when_env_unset(monkeypatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    try:
        mod = importlib.reload(analytics_confidence)
        assert mod.MIN_REVIEWS_FOR_FORM_TREND == 8
        assert mod.MIN_REVIEWS_FOR_MOTIF_TREND == 8
        assert mod.MIN_ATTEMPTS_FOR_MOTIF_RANK == 5
        assert mod.MIN_GAMES_FOR_RATING_DRIVERS == 5
    finally:
        monkeypatch.undo()
        importlib.reload(analytics_confidence)


def test_thresholds_read_env_override(monkeypatch):
    monkeypatch.setenv("ANALYTICS_MIN_REVIEWS_FORM_TREND", "3")
    monkeypatch.setenv("ANALYTICS_MIN_REVIEWS_MOTIF_TREND", "4")
    monkeypatch.setenv("ANALYTICS_MIN_ATTEMPTS_MOTIF_RANK", "2")
    monkeypatch.setenv("ANALYTICS_MIN_GAMES_RATING_DRIVERS", "9")
    try:
        mod = importlib.reload(analytics_confidence)
        assert mod.MIN_REVIEWS_FOR_FORM_TREND == 3
        assert mod.MIN_REVIEWS_FOR_MOTIF_TREND == 4
        assert mod.MIN_ATTEMPTS_FOR_MOTIF_RANK == 2
        assert mod.MIN_GAMES_FOR_RATING_DRIVERS == 9
    finally:
        monkeypatch.undo()
        importlib.reload(analytics_confidence)


@pytest.mark.parametrize("bad", ["0", "-1", "notanint", "  "])
def test_invalid_env_falls_back_to_default(monkeypatch, bad):
    # A typo or non-positive value must never silently disable the gate.
    monkeypatch.setenv("ANALYTICS_MIN_GAMES_RATING_DRIVERS", bad)
    try:
        mod = importlib.reload(analytics_confidence)
        assert mod.MIN_GAMES_FOR_RATING_DRIVERS == 5
    finally:
        monkeypatch.undo()
        importlib.reload(analytics_confidence)


# ---------------------------------------------------------------------------
# Boundary behavior at the default thresholds (N emitted, N-1 gated).
# ---------------------------------------------------------------------------
@pytest.fixture
def client_with_db(db_session, monkeypatch):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        "services.api.main.SessionLocal", sessionmaker(bind=db_session.get_bind())
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


_PZ_PLY = iter(range(1, 100_000))


def _ensure_puzzle(db, puzzle_id, username):
    """Create the puzzle (and its parent game) a review or stats row points at.

    puzzle_reviews.puzzle_id and puzzle_stats.puzzle_id are real foreign keys,
    as is puzzles(source_game_id, username). See conftest for why these can no
    longer be skipped.
    """
    if db.get(Puzzle, puzzle_id) is not None:
        return
    game_id = f"g-{puzzle_id}"
    if db.get(Game, (game_id, username)) is None:
        db.add(
            Game(
                game_id=game_id,
                url="",
                username=username,
                white_username=username,
                black_username="",
                white_result="",
                black_result="",
                time_control="",
                end_time=0,
            )
        )
        db.flush()
    db.add(
        Puzzle(
            id=puzzle_id,
            username=username,
            source_game_id=game_id,
            ply=next(_PZ_PLY),
            fen="6k1/pp3ppp/8/3q4/8/8/PP3PPP/3Q2K1 w - - 0 1",
            side_to_move="white",
            played_move_uci="d1d2",
            best_move_uci="d1d5",
            eval_before=0.5,
            eval_after=-3.0,
            swing=3.0,
        )
    )
    db.flush()


def _seed_reviews(db_session, username, results, base=None):
    base = base or (datetime.now(timezone.utc) - timedelta(hours=5))
    for i, r in enumerate(results):
        _ensure_puzzle(db_session, f"{username}-pz-{i}", username)
        db_session.add(
            PuzzleReview(
                id=f"{username}-rev-{i}",
                puzzle_id=f"{username}-pz-{i}",
                username=username,
                reviewed_at=base + timedelta(minutes=i),
                result=r,
            )
        )


@pytest.mark.parametrize(
    "n,expected_insufficient",
    [
        (analytics_confidence.MIN_REVIEWS_FOR_FORM_TREND - 1, True),
        (analytics_confidence.MIN_REVIEWS_FOR_FORM_TREND, False),
    ],
)
def test_recent_form_boundary(client_with_db, db_session, n, expected_insufficient):
    # First half fail, second half pass -> a clear "up" once the gate opens.
    half = n // 2
    results = ["fail"] * half + ["pass"] * (n - half)
    _seed_reviews(db_session, "formuser", results)
    db_session.commit()

    resp = client_with_db.get("/users/formuser/dashboard")
    assert resp.status_code == 200
    form = resp.json()["recent_form"]
    assert form["sample_size"] == n
    assert form["insufficient_data"] is expected_insufficient
    if expected_insufficient:
        assert form["trend"] == "steady"
    else:
        assert form["trend"] == "up"


def _seed_motif_two_days(db_session, username, motif, day1_results, day2_results):
    now = datetime.now(timezone.utc)
    day1 = now - timedelta(days=3)
    day2 = now - timedelta(days=1)
    idx = 0
    for day, results in ((day1, day1_results), (day2, day2_results)):
        for r in results:
            pid = f"{motif}-{idx}"
            _ensure_puzzle(db_session, pid, username)
            db_session.add(
                PuzzleStats(
                    puzzle_id=pid,
                    username=username,
                    primary_motif=motif,
                    attempts=1,
                    pass_count=1 if r == "pass" else 0,
                )
            )
            db_session.add(
                PuzzleReview(
                    id=f"mr-{pid}",
                    puzzle_id=pid,
                    username=username,
                    reviewed_at=day,
                    result=r,
                )
            )
            idx += 1


@pytest.mark.parametrize(
    "total,expected_insufficient",
    [
        (analytics_confidence.MIN_REVIEWS_FOR_MOTIF_TREND - 1, True),
        (analytics_confidence.MIN_REVIEWS_FOR_MOTIF_TREND, False),
    ],
)
def test_motif_trend_boundary(client_with_db, db_session, total, expected_insufficient):
    # Day 1 all pass, day 2 all fail -> a clear "down" once the gate opens.
    half = total // 2
    _seed_motif_two_days(
        db_session,
        "motifuser",
        "fork",
        ["pass"] * half,
        ["fail"] * (total - half),
    )
    db_session.commit()

    resp = client_with_db.get("/users/motifuser/trends", params={"window": 30})
    assert resp.status_code == 200
    trends = resp.json()["motif_trends"]
    assert len(trends) == 1
    fork = trends[0]
    assert fork["total_reviews"] == total
    assert fork["insufficient_data"] is expected_insufficient
    if expected_insufficient:
        assert fork["trend"] == "steady"
    else:
        assert fork["trend"] == "down"


@pytest.mark.parametrize(
    "attempts,expected_insufficient",
    [
        (analytics_confidence.MIN_ATTEMPTS_FOR_MOTIF_RANK - 1, True),
        (analytics_confidence.MIN_ATTEMPTS_FOR_MOTIF_RANK, False),
    ],
)
def test_motif_rank_boundary(
    client_with_db, db_session, attempts, expected_insufficient
):
    _ensure_puzzle(db_session, "rank-p1", "rankuser")
    db_session.add(
        PuzzleStats(
            puzzle_id="rank-p1",
            username="rankuser",
            primary_motif="pin",
            attempts=attempts,
            pass_count=attempts,  # all passes so it can't be a "weakness"
        )
    )
    db_session.commit()

    resp = client_with_db.get("/users/rankuser/motifs/performance")
    assert resp.status_code == 200
    pin = next(m for m in resp.json()["motifs"] if m["name"] == "pin")
    assert pin["attempts"] == attempts
    assert pin["insufficient_data"] is expected_insufficient


def _seed_rated_games(db_session, username, since_time, n):
    db_session.add(
        RatingSnapshot(
            username=username,
            source="chesscom",
            time_control="rapid",
            rating=1400,
            recorded_at=since_time + timedelta(minutes=1),
        )
    )
    pgn_win = (
        f'[Event "T"]\n[White "{username}"]\n[Black "opp"]\n[Result "1-0"]\n'
        '[WhiteElo "1400"]\n[BlackElo "1650"]\n\n1. e4 e5 1-0'
    )
    for i in range(n):
        db_session.add(
            Game(
                game_id=f"{username}-{i}",
                url=f"https://chess.com/game/{username}-{i}",
                username=username,
                white_username=username,
                black_username="opp",
                white_result="win",
                black_result="loss",
                time_control="600",
                end_time=int((since_time + timedelta(hours=2 + i)).timestamp()),
                rated=True,
                pgn_blob=pgn_win,
            )
        )


@pytest.mark.parametrize(
    "n,expected_insufficient",
    [
        (analytics_confidence.MIN_GAMES_FOR_RATING_DRIVERS - 1, True),
        (analytics_confidence.MIN_GAMES_FOR_RATING_DRIVERS, False),
    ],
)
def test_rating_drivers_boundary(client_with_db, db_session, n, expected_insufficient):
    since_time = datetime.now(timezone.utc) - timedelta(days=1)
    username = f"rateuser{n}"
    _seed_rated_games(db_session, username, since_time, n)
    db_session.commit()

    resp = client_with_db.get(
        "/ratings/explain",
        params={
            "username": username,
            "time_control": "rapid",
            "since": since_time.isoformat(),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["insufficient_data"] is expected_insufficient
