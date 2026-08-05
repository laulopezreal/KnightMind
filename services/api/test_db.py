"""Tests for database URL resolution (fail-fast behavior)."""

import pytest

from services.api.db import _resolve_database_url


def test_database_url_is_used(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host:5432/km")
    assert _resolve_database_url() == "postgresql+psycopg://u:p@host:5432/km"


def test_database_url_is_stripped(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "  postgresql+psycopg://u:p@host:5432/km \n")
    assert _resolve_database_url() == "postgresql+psycopg://u:p@host:5432/km"


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_missing_url_fails_fast(monkeypatch, empty):
    if empty is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", empty)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        _resolve_database_url()


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///./knightmind.db",
        "sqlite:///:memory:",
        "sqlite+pysqlite:///./x.db",
    ],
)
def test_sqlite_is_rejected(monkeypatch, url):
    """SQLite is refused rather than quietly accepted.

    It was a supported fallback until the suite was moved onto Postgres. Running
    on it proves nothing about production -- most sharply, SQLite does not
    enforce foreign keys unless asked, which is how 38 tests came to assert
    against rows the production schema forbids. Failing loudly here is what
    stops the second backend growing back.
    """
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(RuntimeError, match="Postgres-only"):
        _resolve_database_url()
