"""Tests for POST /puzzles/manual — create puzzle from arbitrary position."""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from services.api.main import app, get_db
from services.api.models import Puzzle as PuzzleModel
from services.api.models import PuzzleStats
from services.api.storage.game_repository import MANUAL_GAME_ID
from services.api.storage.puzzle_repository import PuzzleRepository

VALID_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
TERMINAL_FEN = "8/8/8/8/8/8/8/k6K w - - 0 1"  # stalemate / no legal moves


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


def test_create_manual_puzzle_does_not_count_as_imported_game(client):
    create_resp = client.post("/puzzles/manual", json=_payload())
    assert create_resp.status_code == 200, create_resp.text

    status_resp = client.get("/users/testuser/status")
    assert status_resp.status_code == 200, status_resp.text
    status = status_resp.json()
    assert status["games_count"] == 0
    assert status["puzzles_count"] == 1


def test_create_manual_puzzle_keeps_openings_in_no_games_state(client):
    create_resp = client.post("/puzzles/manual", json=_payload())
    assert create_resp.status_code == 200, create_resp.text

    openings_resp = client.get("/openings?username=testuser")
    assert openings_resp.status_code == 404
    assert "no games" in openings_resp.json()["detail"].lower()


def test_manual_puzzles_count_as_due_for_fresh_user(client):
    """A fresh user who saves N analysis positions sees them all as due/New.

    Reconciliation guard (#268 x #228): each manual save eagerly creates a
    PuzzleStats row with next_due_at = NULL. #228's due-count fix counts NULL
    next_due_at as due, so /status must report due_count == N (not 0) even
    though the user has never reviewed and has no imported games.
    """
    positions = [
        # (fen, first legal solution move)
        ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "e2e4"),
        ("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1", "e7e5"),
        ("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3", "c6d4"),
    ]
    for fen, move in positions:
        resp = client.post(
            "/puzzles/manual",
            json=_payload(fen=fen, motif="blunder", solution_pv=move),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_new"] is True

    status = client.get("/users/testuser/status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["puzzles_count"] == len(positions)
    assert body["games_count"] == 0
    # The whole point: never-reviewed manual saves are trainable, not due_count 0.
    assert body["due_count"] == len(positions)

    # They must also SURFACE in /puzzles/due, not merely be counted: the
    # MANUAL_GAME_ID exclusion is scoped to Game-corpus queries only, so puzzle
    # selection (get_all_puzzles / get_adaptive_puzzles) still returns them.
    due = client.get("/puzzles/due", params={"username": "testuser", "n": 20})
    assert due.status_code == 200, due.text
    due_body = due.json()
    assert due_body["due_count"] == len(positions)
    returned_fens = {p["fen"] for p in due_body["puzzles"]}
    for fen, _move in positions:
        assert fen in returned_fens


def test_create_manual_puzzle_transposition_dedup(client, db_session):
    """Finding 1: the SAME board reached via a different move order (a
    transposition — routine in an analysis tool) has a DIFFERENT raw FEN because
    the halfmove/fullmove counters differ, but it is the SAME position. It must
    dedup to ONE puzzle, not insert a second.

    Regression for the duplicate-puzzle defect: the idempotency key was the raw
    FEN string, so a transposed counter inserted a permanent duplicate (there is
    no delete path) whose extra PuzzleStats row inflated due_count forever.
    """
    # Same first four FEN fields (piece placement + side + castling + en passant)
    # as VALID_FEN, only the halfmove/fullmove counters differ ("- 3 3" -> "- 1 2").
    transposed = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 1 2"
    assert transposed != VALID_FEN
    assert transposed.split()[:4] == VALID_FEN.split()[:4]

    resp1 = client.post("/puzzles/manual", json=_payload())  # VALID_FEN
    assert resp1.status_code == 200, resp1.text
    assert resp1.json()["is_new"] is True
    id1 = resp1.json()["puzzle_id"]

    resp2 = client.post("/puzzles/manual", json=_payload(fen=transposed))
    assert resp2.status_code == 200, resp2.text
    # Same position => same puzzle, returned as existing (NOT a second insert).
    assert resp2.json()["is_new"] is False
    assert resp2.json()["puzzle_id"] == id1

    manual = db_session.scalars(
        select(PuzzleModel).where(PuzzleModel.source_game_id == MANUAL_GAME_ID)
    ).all()
    assert len(manual) == 1, "transposition inserted a duplicate puzzle"

    stats_count = db_session.scalar(select(func.count()).select_from(PuzzleStats))
    assert stats_count == 1, "duplicate PuzzleStats row would inflate due_count"

    # The endpoint's "same position => same puzzle" contract also means the
    # never-reviewed manual save counts as exactly ONE due item, not two.
    status = client.get("/users/testuser/status")
    assert status.status_code == 200, status.text
    assert status.json()["due_count"] == 1


def test_concurrent_manual_save_of_same_position_absorbed(
    client, db_session, monkeypatch
):
    """Finding 2: a concurrent duplicate of the SAME position that slips past the
    app-level precheck (a TOCTOU window) is absorbed by the partial unique index.

    The endpoint's precheck can miss if a concurrent request commits the same
    position between our read and our insert. We pin that interleave: the first
    delegated save_puzzle commits the SAME position at a DIFFERENT ply (exactly
    what a racing request would do), then delegates. The delegated insert then
    violates the partial unique index on the normalized position AT COMMIT. The
    endpoint must catch that IntegrityError and return the existing puzzle
    idempotently — no phantom id, no 500, and exactly one row.
    """
    real_save = PuzzleRepository.save_puzzle
    state = {"stole": False}

    def steal_then_save(self, **kwargs):
        if not state["stole"]:
            state["stole"] = True
            # A "concurrent request" commits our exact position at a DIFFERENT ply
            # slot, so our own insert's ply precheck stays clear and the collision
            # lands on the normalized-position unique index instead.
            thief_kwargs = dict(kwargs)
            thief_kwargs["ply"] = kwargs["ply"] + 5
            real_save(self, **thief_kwargs)
        return real_save(self, **kwargs)

    monkeypatch.setattr(PuzzleRepository, "save_puzzle", steal_then_save)

    resp = client.post("/puzzles/manual", json=_payload())  # VALID_FEN
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_new"] is False
    winner_id = resp.json()["puzzle_id"]

    manual = db_session.scalars(
        select(PuzzleModel).where(PuzzleModel.source_game_id == MANUAL_GAME_ID)
    ).all()
    assert len(manual) == 1, "concurrent same-position save was not absorbed"
    assert manual[0].id == winner_id
    assert db_session.get(PuzzleModel, winner_id) is not None  # not a phantom id

    stats_count = db_session.scalar(select(func.count()).select_from(PuzzleStats))
    assert stats_count == 1


def test_concurrent_manual_saves_of_different_positions_both_persist(
    client, db_session, monkeypatch
):
    """Regression (#268 race): a concurrent save of a DIFFERENT position must not
    be dropped.

    All manual puzzles share source_game_id == MANUAL_GAME_ID, and
    (username, source_game_id, ply) is unique. The endpoint computes
    ply = max(ply) + 1 OUTSIDE the insert's transaction, so two concurrent saves
    of different positions can compute the same ply. #268 let the loser hit
    IntegrityError, resolved it to the WINNER's id via _existing_puzzle_id, and
    returned that (a phantom for the loser) with is_new=False -- silently
    dropping the loser's saved position. The fix reallocates a fresh ply and
    retries when a DIFFERENT position took the slot, while still returning the
    existing puzzle idempotently for the same FEN.

    We pin the interleaving deterministically instead of racing threads: the
    first save_puzzle call commits a *different* position into the ply slot the
    endpoint just computed (exactly what a concurrent request would do), then
    delegates. This reproduces the collision every run with zero flakiness.
    """
    thief_fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    real_save = PuzzleRepository.save_puzzle
    real_existing = PuzzleRepository._existing_puzzle_id
    state = {"stole": False, "skip_precheck_once": False}

    def existing_maybe_skip(self, username, source_game_id, ply):
        # Force ONLY the delegated save's opening precheck to miss, so it walks
        # the real INSERT -> unique-index violation at commit -> IntegrityError
        # -> rollback -> recovery path (the exact path the #268 bug lived on),
        # not just save_puzzle's early-return duplicate precheck. Every other
        # lookup (incl. the except-handler's) runs for real.
        if state["skip_precheck_once"]:
            state["skip_precheck_once"] = False
            return None
        return real_existing(self, username, source_game_id, ply)

    def steal_then_save(self, **kwargs):
        # On the first insert only, a "concurrent request" grabs this exact ply
        # slot with a DIFFERENT position and commits, then we delegate. The
        # delegated insert then collides on the unique (username, game, ply) key
        # AT COMMIT, exercising save_puzzle's IntegrityError/rollback branch.
        if not state["stole"]:
            state["stole"] = True
            thief_kwargs = dict(kwargs)
            thief_kwargs.update(
                fen=thief_fen,
                played_move_uci="e2e4",
                best_move_uci="e2e4",
                accept_moves_uci="e2e4",
                solution_pv="e2e4",
                title="Concurrent Save",
                primary_motif="blunder",
            )
            real_save(self, **thief_kwargs)
            state["skip_precheck_once"] = True
        return real_save(self, **kwargs)

    monkeypatch.setattr(PuzzleRepository, "_existing_puzzle_id", existing_maybe_skip)
    monkeypatch.setattr(PuzzleRepository, "save_puzzle", steal_then_save)

    resp = client.post("/puzzles/manual", json=_payload())  # VALID_FEN
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_new"] is True
    saved_id = resp.json()["puzzle_id"]

    manual = db_session.scalars(
        select(PuzzleModel).where(PuzzleModel.source_game_id == MANUAL_GAME_ID)
    ).all()
    by_fen = {p.fen: p for p in manual}

    # BOTH concurrent positions persisted with distinct ids/plies: none dropped.
    assert thief_fen in by_fen, "the concurrent position was dropped"
    assert VALID_FEN in by_fen, "our saved position was dropped"
    thief_row = by_fen[thief_fen]
    our_row = by_fen[VALID_FEN]
    assert our_row.id == saved_id  # not a phantom uuid
    assert db_session.get(PuzzleModel, saved_id) is not None
    assert our_row.id != thief_row.id
    assert our_row.ply != thief_row.ply

    # Exactly ONE PuzzleStats for our saved puzzle (the #228 x #268 join guard:
    # both PRs eagerly create stats; the reconciled save_puzzle inserts one).
    stats_count = db_session.scalar(
        select(func.count())
        .select_from(PuzzleStats)
        .where(PuzzleStats.puzzle_id == saved_id)
    )
    assert stats_count == 1
