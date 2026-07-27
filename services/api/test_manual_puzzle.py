"""Tests for POST /puzzles/manual — create puzzle from arbitrary position."""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.main import app, get_db
from services.api.models import Base, PuzzleStats
from services.api.models import Puzzle as PuzzleModel

VALID_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
TERMINAL_FEN = "8/8/8/8/8/8/8/k6K w - - 0 1"  # stalemate / no legal moves


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
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _payload(**overrides):
    base = {
        "username": "testuser",
        "fen": VALID_FEN,
        "title": "Sicilian Fork",
        "motif": "fork",
        "source": "Lau game",
        "solution_pv": "c6d4",
    }
    base.update(overrides)
    return base


def test_create_manual_puzzle_happy_path(client, db_session):
    resp = client.post("/puzzles/manual", json=_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_new"] is True
    puzzle_id = data["puzzle_id"]

    puzzle = db_session.get(PuzzleModel, puzzle_id)
    assert puzzle is not None
    assert puzzle.source_game_id == "__manual__"
    assert puzzle.side_to_move == "black"  # derived from FEN
    assert puzzle.best_move_uci == "c6d4"
    assert puzzle.solution_pv == "c6d4"
    assert puzzle.source_path == "Lau game"
    assert puzzle.ply == 0

    stats = (
        db_session.query(PuzzleStats)
        .filter_by(puzzle_id=puzzle_id, username="testuser")
        .first()
    )
    assert stats is not None
    assert stats.title == "Sicilian Fork"
    assert stats.primary_motif == "fork"
    assert (
        db_session.query(PuzzleStats)
        .filter_by(puzzle_id=puzzle_id, username="testuser")
        .count()
        == 1
    )


def test_create_manual_puzzle_idempotent(client):
    resp1 = client.post("/puzzles/manual", json=_payload())
    assert resp1.status_code == 200
    id1 = resp1.json()["puzzle_id"]
    assert resp1.json()["is_new"] is True

    resp2 = client.post("/puzzles/manual", json=_payload())
    assert resp2.status_code == 200
    id2 = resp2.json()["puzzle_id"]
    assert resp2.json()["is_new"] is False

    assert id1 == id2


def test_create_manual_puzzle_different_fen_gets_sequential_ply(client, db_session):
    fen2 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    resp1 = client.post("/puzzles/manual", json=_payload())
    resp2 = client.post("/puzzles/manual", json=_payload(fen=fen2, solution_pv="f3e5"))
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    id1 = resp1.json()["puzzle_id"]
    id2 = resp2.json()["puzzle_id"]
    assert id1 != id2

    p1 = db_session.get(PuzzleModel, id1)
    p2 = db_session.get(PuzzleModel, id2)
    assert p2.ply == p1.ply + 1


def test_create_manual_puzzle_invalid_fen(client):
    resp = client.post("/puzzles/manual", json=_payload(fen="not-a-fen"))
    assert resp.status_code == 400
    assert "Invalid FEN" in resp.json()["detail"]


def test_create_manual_puzzle_terminal_position(client):
    # King + king stalemate — game is over
    resp = client.post(
        "/puzzles/manual", json=_payload(fen=TERMINAL_FEN, solution_pv="h1g1")
    )
    assert resp.status_code == 400


def test_create_manual_puzzle_invalid_motif(client):
    resp = client.post("/puzzles/manual", json=_payload(motif="invalid_motif"))
    assert resp.status_code == 400
    assert "motif" in resp.json()["detail"].lower()


def test_create_manual_puzzle_missing_title(client):
    resp = client.post("/puzzles/manual", json=_payload(title=""))
    assert resp.status_code == 400


def test_create_manual_puzzle_missing_solution(client):
    resp = client.post("/puzzles/manual", json=_payload(solution_pv=""))
    assert resp.status_code == 400


def test_create_manual_puzzle_illegal_move_in_solution(client):
    resp = client.post("/puzzles/manual", json=_payload(solution_pv="e2e4"))
    assert resp.status_code == 400
    assert "Illegal move" in resp.json()["detail"]


def test_create_manual_puzzle_multi_move_solution_validated(client, db_session):
    # Two-move PV: knight fork c6d4, then white bishop recaptures c4f7
    resp = client.post("/puzzles/manual", json=_payload(solution_pv="c6d4 c4f7"))
    assert resp.status_code == 200
    puzzle_id = resp.json()["puzzle_id"]
    puzzle = db_session.get(PuzzleModel, puzzle_id)
    assert puzzle.solution_pv == "c6d4 c4f7"
    assert puzzle.best_move_uci == "c6d4"


def test_create_manual_puzzle_stats_not_overwritten_by_backfill(client, db_session):
    """Backfill only fills NULL title; user-set title must survive."""
    from services.api.puzzles.identity import backfill_puzzle_identity

    resp = client.post("/puzzles/manual", json=_payload(title="My Title", motif="pin"))
    assert resp.status_code == 200
    puzzle_id = resp.json()["puzzle_id"]

    backfill_puzzle_identity(db_session)

    stats = (
        db_session.query(PuzzleStats)
        .filter_by(puzzle_id=puzzle_id, username="testuser")
        .first()
    )
    assert stats.title == "My Title"
    assert stats.primary_motif == "pin"


def test_create_manual_puzzle_motifs_normalized(client, db_session):
    """Motif is normalized (strip + lowercase) before storage and validation."""
    resp = client.post("/puzzles/manual", json=_payload(motif="  fork  "))
    assert resp.status_code == 200
    stats = (
        db_session.query(PuzzleStats)
        .filter_by(puzzle_id=resp.json()["puzzle_id"], username="testuser")
        .first()
    )
    assert stats.primary_motif == "fork"


def test_create_manual_puzzle_appears_in_library_motif_filter(client):
    create_resp = client.post("/puzzles/manual", json=_payload(motif="fork"))
    assert create_resp.status_code == 200
    puzzle_id = create_resp.json()["puzzle_id"]

    list_resp = client.get("/puzzles/list?username=testuser&motif=fork")
    assert list_resp.status_code == 200, list_resp.text
    data = list_resp.json()

    assert data["total"] == 1
    assert data["available_motifs"] == ["fork"]
    assert data["puzzles"][0]["id"] == puzzle_id
    assert data["puzzles"][0]["title"] == "Sicilian Fork"
    assert data["puzzles"][0]["primary_motif"] == "fork"
