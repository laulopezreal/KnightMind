import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "KnightMind API"
    assert "version" in data


def test_import_chesscom():
    response = client.post("/import/chesscom?username=testuser")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "games_count" in data
    assert "testuser" in data["message"]


def test_import_chesscom_missing_username():
    response = client.post("/import/chesscom")
    assert response.status_code == 422  # Validation error


def test_get_openings():
    response = client.get("/openings")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "moves" in data
    assert "count" in data
    assert "children" in data
