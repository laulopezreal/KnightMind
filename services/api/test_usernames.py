"""Tests for canonical username handling.

Covers the pure helper (``canonical_username`` / ``validate_username``) and the
end-to-end guarantee that every entry point folds ``Bob`` / ``bob`` / ``" bob "``
to one user and rejects empty / whitespace-only / homoglyph handles with a 4xx
instead of silently serving an empty-data 200.
"""

import os

os.environ["KNIGHTMIND_WORKER_DISABLED"] = "true"

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.api.main import app, get_db
from services.api.models import Game
from services.api.usernames import (
    MAX_USERNAME_LENGTH,
    canonical_username,
    validate_username,
)

# --- Unit: canonical_username (pure fold) ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Bob", "bob"),
        ("bob", "bob"),
        ("  bob  ", "bob"),
        ("\tBoB\n", "bob"),
        ("HIKARU", "hikaru"),
        # NFKC folds fullwidth/compat forms to plain ASCII.
        ("Ｂｏｂ", "bob"),  # fullwidth "Bob"
        (" bob ", "bob"),  # non-breaking spaces stripped after NFKC
        (None, ""),
        ("   ", ""),
    ],
)
def test_canonical_username_folds(raw, expected):
    assert canonical_username(raw) == expected


def test_canonical_username_is_idempotent_on_existing_handles():
    # Production handles are already lowercase ASCII: fixed points of the fold.
    for handle in ("lauureal", "alfi3sr", "hikaru"):
        assert canonical_username(handle) == handle


# --- Unit: validate_username (fold + reject) ---


def test_validate_username_returns_canonical():
    assert validate_username(" Bob ") == "bob"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", None])
def test_validate_username_rejects_empty(raw):
    with pytest.raises(ValueError, match="empty"):
        validate_username(raw)


def test_validate_username_rejects_non_ascii_homoglyph():
    # Cyrillic "а" (U+0430) is a homoglyph NFKC does not fold; it must be
    # rejected so it cannot masquerade as the Latin "alice".
    with pytest.raises(ValueError, match="ASCII"):
        validate_username("аlice")


def test_validate_username_rejects_overlong():
    with pytest.raises(ValueError, match="characters"):
        validate_username("a" * (MAX_USERNAME_LENGTH + 1))


# --- End-to-end fixtures ---


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _add_game(db, username="bob", game_id="g1"):
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
            end_time=1,
            rated=True,
            pgn_blob='[Event "Test"]\n\n1. e4 e5 1/2-1/2',
        )
    )
    db.commit()


# --- End-to-end: case/whitespace variants resolve to one user ---


@pytest.mark.parametrize("variant", ["bob", "Bob", "BOB", "%20bob%20"])
def test_status_variants_resolve_to_same_user(client, db_session, variant):
    _add_game(db_session, username="bob")
    resp = client.get(f"/users/{variant}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "bob"
    assert body["games_count"] == 1


@pytest.mark.parametrize("bad", ["%20%20%20", "%09"])
def test_status_whitespace_username_is_rejected(client, bad):
    # A whitespace-only handle must 4xx, NOT return an empty-data 200.
    resp = client.get(f"/users/{bad}/status")
    assert resp.status_code == 422


def test_status_homoglyph_username_is_rejected(client):
    # Cyrillic "аlice" must not silently create a distinct empty user.
    resp = client.get("/users/аlice/status")
    assert resp.status_code == 422


def test_query_param_username_is_canonicalized(client):
    # import/status is a plain query param entry point.
    for variant in (" Bob ", "bob", "BOB"):
        resp = client.get("/import/status", params={"username": variant})
        assert resp.status_code == 200
    assert client.get("/import/status", params={"username": "   "}).status_code == 422


# --- End-to-end: body-param sessions can't fork on case/whitespace ---


def test_session_case_variants_do_not_fork(client):
    with patch(
        "services.api.ratings_auto.get_player_stats", new_callable=AsyncMock
    ) as stats:
        stats.return_value = {}
        start = client.post("/sessions/start", json={"username": "Bob", "n": 2})
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        # Completing with a different-case/whitespace handle must resolve to the
        # SAME session (canonical match), not 403 "belongs to different user".
        done = client.post(
            f"/sessions/{session_id}/complete", json={"username": " bob "}
        )
        assert done.status_code == 200

    # The recent-sessions list keys on the canonical username too.
    recent = client.get("/sessions/recent", params={"username": "BOB"})
    assert recent.status_code == 200
    assert len(recent.json()) == 1


def test_session_start_rejects_blank_username(client):
    resp = client.post("/sessions/start", json={"username": "   ", "n": 2})
    assert resp.status_code == 422
