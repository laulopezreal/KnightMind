"""
AUDIT GATE 8 — analytics truthfulness.

Characterizing tests for scorecard dims 17 (day boundary) and 18 (uncertainty):
- a small rating-explain window must not be presented as a confident trend;
- day-based metrics resolve on one documented UTC boundary;
- a tiny-sample motif "trend" must carry an uncertainty flag, not a direction.
"""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from services.api.day_boundary import day_key, utc_today
from services.api.db import get_db
from services.api.main import app
from services.api.models import (
    Game,
    Puzzle,
    PuzzleReview,
    PuzzleStats,
    RatingSnapshot,
    TrainingSession,
)


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


# ---------------------------------------------------------------------------
# (a) A tiny rating-explain window must not read as a confident trend.
# ---------------------------------------------------------------------------
def test_explain_small_sample_is_flagged_not_confident(client_with_db, db_session):
    since_time = datetime.now(timezone.utc) - timedelta(days=1)

    db_session.add(
        RatingSnapshot(
            username="tinyuser",
            source="chesscom",
            time_control="rapid",
            rating=1400,
            recorded_at=since_time + timedelta(minutes=1),
        )
    )

    # Only 2 rated games — far below the driver threshold.
    pgn_win = (
        '[Event "T"]\n[White "tinyuser"]\n[Black "opp"]\n[Result "1-0"]\n'
        '[WhiteElo "1400"]\n[BlackElo "1650"]\n\n1. e4 e5 1-0'
    )
    for i in range(2):
        db_session.add(
            Game(
                game_id=f"tiny-{i}",
                url=f"https://chess.com/game/tiny-{i}",
                username="tinyuser",
                white_username="tinyuser",
                black_username="opp",
                white_result="win",
                black_result="loss",
                time_control="600",
                end_time=int((since_time + timedelta(hours=2 + i)).timestamp()),
                rated=True,
                pgn_blob=pgn_win,
            )
        )
    db_session.commit()

    resp = client_with_db.get(
        "/ratings/explain",
        params={
            "username": "tinyuser",
            "time_control": "rapid",
            "since": since_time.isoformat(),
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    # Server exposes a canonical confidence signal.
    assert data["confidence"] == "low"
    assert data["insufficient_data"] is True

    # A 2-game delta must NOT be dressed up as a confident directional driver.
    directional = [d for d in data["drivers"] if d["direction"] in ("up", "down")]
    assert directional == [], f"unexpected confident drivers: {directional}"


# ---------------------------------------------------------------------------
# (b) One documented day boundary: UTC calendar days.
# ---------------------------------------------------------------------------
def test_day_boundary_is_utc_and_normalized():
    # Postgres-style date object and SQLite-style string normalize identically.
    assert day_key(datetime(2026, 1, 2, 23, 30).date()) == "2026-01-02"
    assert day_key("2026-01-02") == "2026-01-02"
    assert utc_today() == datetime.now(timezone.utc).date()


def test_streak_counts_cross_midnight_utc_days(client_with_db, db_session):
    """A session at 23:30 UTC and one at 00:30 UTC next day are two streak days.

    Locks the boundary to UTC: the late-night (UTC) session belongs to its UTC
    calendar day, and the pair forms a 2-day streak ending today.
    """
    today = utc_today()
    # Session completed today at 00:30 UTC, and yesterday at 23:30 UTC.
    today_0030 = datetime.combine(
        today, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(minutes=30)
    yday_2330 = today_0030 - timedelta(hours=1)  # yesterday 23:30 UTC

    for idx, ts in enumerate((today_0030, yday_2330)):
        db_session.add(
            TrainingSession(
                id=f"streak-{idx}",
                username="streakuser",
                created_at=ts,
                completed_at=ts,
                requested_n=1,
            )
        )
    db_session.commit()

    resp = client_with_db.get("/users/streakuser/dashboard")
    assert resp.status_code == 200
    assert resp.json()["training_streak_days"] == 2


_PZ_PLY = iter(range(1, 100_000))


def _ensure_puzzle(db, puzzle_id, username):
    """Create the puzzle (and parent game) a review or stats row points at.

    puzzle_reviews.puzzle_id and puzzle_stats.puzzle_id are real foreign keys.
    See conftest for why they can no longer be left dangling.
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


def test_recent_form_small_sample_flagged(client_with_db, db_session):
    """<8 reviews: report insufficient_data and a neutral (steady) trend."""
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    # 4 reviews, alternating — old code would call this a directional trend.
    results = ["fail", "fail", "pass", "pass"]
    for i, r in enumerate(results):
        _ensure_puzzle(db_session, f"pz-{i}", "formuser")
        db_session.add(
            PuzzleReview(
                id=f"rev-{i}",
                puzzle_id=f"pz-{i}",
                username="formuser",
                reviewed_at=base + timedelta(minutes=i),
                result=r,
            )
        )
    db_session.commit()

    resp = client_with_db.get("/users/formuser/dashboard")
    assert resp.status_code == 200
    form = resp.json()["recent_form"]
    assert form["sample_size"] == 4
    assert form["insufficient_data"] is True
    assert form["trend"] == "steady"


# ---------------------------------------------------------------------------
# (c) A tiny-sample motif "trend" must carry an uncertainty flag.
# ---------------------------------------------------------------------------
def _seed_motif_reviews(db_session, username, motif, day_result_pairs):
    """Seed stats+reviews so /trends has motif data."""
    for idx, (dt, result) in enumerate(day_result_pairs):
        pid = f"{motif}-{idx}"
        _ensure_puzzle(db_session, pid, username)
        db_session.add(
            PuzzleStats(
                puzzle_id=pid,
                username=username,
                primary_motif=motif,
                attempts=1,
                pass_count=1 if result == "pass" else 0,
            )
        )
        db_session.add(
            PuzzleReview(
                id=f"mr-{pid}",
                puzzle_id=pid,
                username=username,
                reviewed_at=dt,
                result=result,
            )
        )


def test_motif_trend_tiny_sample_flagged(client_with_db, db_session):
    now = datetime.now(timezone.utc)
    # 2 reviews on 2 different days: pass then fail -> old code emits "down".
    _seed_motif_reviews(
        db_session,
        "motifuser",
        "fork",
        [(now - timedelta(days=3), "pass"), (now - timedelta(days=1), "fail")],
    )
    db_session.commit()

    resp = client_with_db.get("/users/motifuser/trends", params={"window": 30})
    assert resp.status_code == 200
    trends = resp.json()["motif_trends"]
    assert len(trends) == 1
    fork = trends[0]
    assert fork["total_reviews"] == 2
    assert fork["insufficient_data"] is True
    # A 2-review delta must not be presented as a direction.
    assert fork["trend"] == "steady"


def test_motif_performance_low_attempts_not_called_weakness(client_with_db, db_session):
    """A 1-attempt failed motif must not be surfaced as a weakness."""
    _ensure_puzzle(db_session, "wp1", "perfuser")
    db_session.add(
        PuzzleStats(
            puzzle_id="wp1",
            username="perfuser",
            primary_motif="pin",
            attempts=1,
            pass_count=0,
        )
    )
    db_session.commit()

    resp = client_with_db.get("/users/perfuser/motifs/performance")
    assert resp.status_code == 200
    data = resp.json()
    pin = next(m for m in data["motifs"] if m["name"] == "pin")
    assert pin["attempts"] == 1
    assert pin["insufficient_data"] is True
    assert "pin" not in data["weakest_motifs"]


def test_motif_performance_excludes_blunder_placeholder(client_with_db, db_session):
    """`blunder` is the no-motif placeholder, not a trainable pattern.

    usable_motif() strips it from every puzzle payload (identity design step
    7, #409); the analytics aggregation must apply the same contract or the
    placeholder resurfaces as the user's #1 "Weak Area" on the training page.
    """
    _ensure_puzzle(db_session, "bl1", "perfuser2")
    _ensure_puzzle(db_session, "bl2", "perfuser2")
    db_session.add(
        PuzzleStats(
            puzzle_id="bl1",
            username="perfuser2",
            primary_motif="blunder",
            attempts=20,
            pass_count=5,
        )
    )
    db_session.add(
        PuzzleStats(
            puzzle_id="bl2",
            username="perfuser2",
            primary_motif="fork",
            attempts=10,
            pass_count=6,
        )
    )
    db_session.commit()

    resp = client_with_db.get("/users/perfuser2/motifs/performance")
    assert resp.status_code == 200
    data = resp.json()
    names = [m["name"] for m in data["motifs"]]
    assert "blunder" not in names
    assert "fork" in names
    assert "blunder" not in data["weakest_motifs"]
    # The placeholder must not inflate the practiced-motif count either.
    assert data["total_motifs_practiced"] == len(names)
