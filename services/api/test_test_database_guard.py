"""The guard that stops the suite dropping a database it should not.

``conftest._resolve_test_database_url`` is the only thing between a stale
``KNIGHTMIND_TEST_DATABASE_URL`` and a real database, because the fixtures DROP
and TRUNCATE every table before the first test runs. It is worth testing for the
same reason it exists: the failure is silent, immediate and unrecoverable.

The first version matched ``"test" in name``, which also accepted ``latest``,
``contest`` and ``attestation`` -- and ``knightmind_latest`` is a name someone
could plausibly have. The refusals below are the point of this file.
"""

import pytest


def _resolve(url, monkeypatch):
    """Call the guard with KNIGHTMIND_TEST_DATABASE_URL set to `url`."""
    import conftest as root_conftest

    monkeypatch.setenv(root_conftest.TEST_DB_URL_ENV, url)
    return root_conftest._resolve_test_database_url()


BASE = "postgresql+psycopg://u:p@localhost:5432/"


@pytest.mark.parametrize(
    "database",
    ["knightmind_test", "test", "tests", "test_db", "knightmind-test", "api_test_2"],
)
def test_disposable_names_are_accepted(database, monkeypatch):
    assert _resolve(BASE + database, monkeypatch) == BASE + database


@pytest.mark.parametrize(
    "database",
    [
        "knightmind",  # the obvious one
        "knightmind_prod",
        "latest",  # contains "test", is not a test database
        "knightmind_latest",  # entirely plausible, and would be dropped
        "contest",
        "attestation",
    ],
)
def test_non_disposable_names_are_refused(database, monkeypatch):
    with pytest.raises(RuntimeError, match="Refusing to run"):
        _resolve(BASE + database, monkeypatch)


def test_query_string_does_not_smuggle_a_name_past_the_guard(monkeypatch):
    """?options=... must not be read as part of the database name."""
    with pytest.raises(RuntimeError, match="Refusing to run"):
        _resolve(BASE + "knightmind?application_name=test", monkeypatch)


def test_missing_url_is_refused(monkeypatch):
    import conftest as root_conftest

    monkeypatch.delenv(root_conftest.TEST_DB_URL_ENV, raising=False)
    with pytest.raises(RuntimeError, match="is not set"):
        root_conftest._resolve_test_database_url()
