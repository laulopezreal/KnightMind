"""Tests for database URL resolution (fail-fast behavior)."""

import pytest

from services.api.db import DEFAULT_SQLITE_URL, _resolve_database_url


def test_database_url_wins(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host:5432/km")
    monkeypatch.setenv("KNIGHTMIND_DEV_SQLITE", "1")
    assert _resolve_database_url() == "postgresql+psycopg://u:p@host:5432/km"


def test_database_url_is_stripped(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "  postgresql+psycopg://u:p@host:5432/km \n")
    assert _resolve_database_url() == "postgresql+psycopg://u:p@host:5432/km"


def test_dev_sqlite_opt_in(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("KNIGHTMIND_DEV_SQLITE", "1")
    assert _resolve_database_url() == DEFAULT_SQLITE_URL


@pytest.mark.parametrize("dev_flag", [None, "", "0", "false", "no"])
def test_missing_url_fails_fast(monkeypatch, dev_flag):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if dev_flag is None:
        monkeypatch.delenv("KNIGHTMIND_DEV_SQLITE", raising=False)
    else:
        monkeypatch.setenv("KNIGHTMIND_DEV_SQLITE", dev_flag)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        _resolve_database_url()


@pytest.mark.parametrize("empty", ["", "   "])
def test_empty_url_fails_fast(monkeypatch, empty):
    monkeypatch.setenv("DATABASE_URL", empty)
    monkeypatch.delenv("KNIGHTMIND_DEV_SQLITE", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        _resolve_database_url()
