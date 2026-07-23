"""Tests for the shared env-int reader.

The helper replaced three subtly different per-module copies; these tests pin
the semantics each call site depends on: silent fallback on unset/blank, the
``min_value`` range contract (0 must stay valid for rate limits but be
rejected for positive-only thresholds), and opt-in warning logs.
"""

import pytest

from services.api.envutil import env_int


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_unset_or_blank_falls_back_silently(monkeypatch, caplog, raw):
    if raw is None:
        monkeypatch.delenv("KM_TEST_INT", raising=False)
    else:
        monkeypatch.setenv("KM_TEST_INT", raw)
    assert env_int("KM_TEST_INT", 7, min_value=1, log_invalid=True) == 7
    assert caplog.records == []


def test_valid_value_wins(monkeypatch):
    monkeypatch.setenv("KM_TEST_INT", "42")
    assert env_int("KM_TEST_INT", 7) == 42


def test_non_integer_falls_back(monkeypatch):
    monkeypatch.setenv("KM_TEST_INT", "not-a-number")
    assert env_int("KM_TEST_INT", 7) == 7


def test_zero_is_valid_without_min_value(monkeypatch):
    # Rate limits rely on this: RATE_LIMIT_<NAME>=0 disables a limiter.
    monkeypatch.setenv("KM_TEST_INT", "0")
    assert env_int("KM_TEST_INT", 7) == 0
    assert env_int("KM_TEST_INT", 7, min_value=0) == 0


@pytest.mark.parametrize("raw", ["0", "-3"])
def test_below_min_value_falls_back(monkeypatch, raw):
    monkeypatch.setenv("KM_TEST_INT", raw)
    assert env_int("KM_TEST_INT", 7, min_value=1) == 7


def test_log_invalid_warns_on_bad_and_out_of_range(monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="services.api.envutil"):
        monkeypatch.setenv("KM_TEST_INT", "junk")
        assert env_int("KM_TEST_INT", 7, log_invalid=True) == 7
        monkeypatch.setenv("KM_TEST_INT", "-1")
        assert env_int("KM_TEST_INT", 7, min_value=1, log_invalid=True) == 7
    assert len(caplog.records) == 2


def test_invalid_is_silent_by_default(monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="services.api.envutil"):
        monkeypatch.setenv("KM_TEST_INT", "junk")
        assert env_int("KM_TEST_INT", 7) == 7
    assert caplog.records == []
