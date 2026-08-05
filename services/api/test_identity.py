"""Unit tests for the identity foundation: password hashing, JWT, flag-aware
dependencies, ownership helpers, and the /auth/login + /auth/me routes.

These test the primitives in isolation. Cross-tenant HTTP enforcement lives in
test_tenant_isolation.py.
"""

import time

import jwt
import pytest
from fastapi import HTTPException

from services.api import security
from services.api.identity import (
    assert_owns_username,
    auth_required,
    claim_username_if_unowned,
    normalize_username,
    require_account,
    require_authenticated_account,
)
from services.api.models import Account, AccountChessUsername
from services.api.security import (
    JWTSecretMissingError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

SECRET = "test-secret-please-ignore-0123456789-abcdef"


@pytest.fixture
def db(db_session):
    return db_session


def _make_account(db, email="alice@example.com", password="hunter2", disabled=False):
    account = Account(
        email=email, password_hash=hash_password(password), disabled=disabled
    )
    db.add(account)
    db.flush()
    return account


def _claim(db, account, username):
    db.add(AccountChessUsername(account_id=account.id, username=username))
    db.flush()


# --- Password hashing --------------------------------------------------------


def test_password_roundtrip():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h) is True
    assert verify_password("wrong horse", h) is False


def test_verify_password_handles_garbage_hash():
    assert verify_password("anything", "not-a-hash") is False


# --- JWT ---------------------------------------------------------------------


def test_jwt_roundtrip(monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_JWT_SECRET", SECRET)
    token = create_access_token("acct-1", "alice@example.com")
    claims = decode_access_token(token)
    assert claims["sub"] == "acct-1"
    assert claims["email"] == "alice@example.com"


def test_jwt_expired_rejected(monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_JWT_SECRET", SECRET)
    now = int(time.time())
    token = jwt.encode(
        {"sub": "acct-1", "iat": now - 120, "exp": now - 60},
        SECRET,
        algorithm=security.JWT_ALGORITHM,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_jwt_tampered_signature_rejected(monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_JWT_SECRET", SECRET)
    token = create_access_token("acct-1", "alice@example.com")
    tampered = token[:-3] + ("abc" if token[-3:] != "abc" else "xyz")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(tampered)


def test_jwt_wrong_secret_rejected(monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_JWT_SECRET", SECRET)
    token = create_access_token("acct-1", "alice@example.com")
    monkeypatch.setenv(
        "KNIGHTMIND_JWT_SECRET", "a-different-secret-0123456789-abcdefghij"
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token)


def test_jwt_missing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("KNIGHTMIND_JWT_SECRET", raising=False)
    with pytest.raises(JWTSecretMissingError):
        create_access_token("acct-1", "alice@example.com")


# --- Flag + normalization ----------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_auth_required_truthy(monkeypatch, value):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", value)
    assert auth_required() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "banana"])
def test_auth_required_falsy(monkeypatch, value):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", value)
    assert auth_required() is False


def test_normalize_username():
    assert normalize_username("  Alice ") == "alice"
    assert normalize_username("HIKARU") == "hikaru"
    # Cyrillic 'а' is NOT folded into Latin 'a'.
    assert normalize_username("аlice") != "alice"


# --- require_account (flag-aware) --------------------------------------------


def test_require_account_off_returns_none(monkeypatch, db):
    monkeypatch.delenv("KNIGHTMIND_REQUIRE_AUTH", raising=False)
    assert require_account(authorization=None, db=db) is None
    # Even a bogus token is ignored when the flag is off.
    assert require_account(authorization="Bearer nonsense", db=db) is None


def test_require_account_on_requires_token(monkeypatch, db):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    monkeypatch.setenv("KNIGHTMIND_JWT_SECRET", SECRET)
    with pytest.raises(HTTPException) as exc:
        require_account(authorization=None, db=db)
    assert exc.value.status_code == 401


def test_require_account_on_accepts_valid_token(monkeypatch, db):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    monkeypatch.setenv("KNIGHTMIND_JWT_SECRET", SECRET)
    account = _make_account(db)
    token = create_access_token(account.id, account.email)
    resolved = require_account(authorization=f"Bearer {token}", db=db)
    assert resolved is not None
    assert resolved.id == account.id


def test_require_account_on_rejects_disabled(monkeypatch, db):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    monkeypatch.setenv("KNIGHTMIND_JWT_SECRET", SECRET)
    account = _make_account(db, disabled=True)
    token = create_access_token(account.id, account.email)
    with pytest.raises(HTTPException) as exc:
        require_account(authorization=f"Bearer {token}", db=db)
    assert exc.value.status_code == 401


def test_require_authenticated_account_always_requires_token(monkeypatch, db):
    # Even with the flag OFF, /auth/me-style routes require a token.
    monkeypatch.delenv("KNIGHTMIND_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("KNIGHTMIND_JWT_SECRET", SECRET)
    with pytest.raises(HTTPException) as exc:
        require_authenticated_account(authorization=None, db=db)
    assert exc.value.status_code == 401


# --- assert_owns_username ----------------------------------------------------


def test_assert_owns_username_noop_when_off(monkeypatch, db):
    monkeypatch.delenv("KNIGHTMIND_REQUIRE_AUTH", raising=False)
    # No account, no ownership rows — must not raise when auth is off.
    assert_owns_username(None, "anybody", db)


def test_assert_owns_username_owner_passes(monkeypatch, db):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    account = _make_account(db)
    _claim(db, account, "alice")
    assert_owns_username(account, "Alice", db)  # case-insensitive


def test_assert_owns_username_non_owner_403(monkeypatch, db):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    account = _make_account(db)
    _claim(db, account, "alice")
    with pytest.raises(HTTPException) as exc:
        assert_owns_username(account, "bob", db)
    assert exc.value.status_code == 403


def test_assert_owns_username_404_variant(monkeypatch, db):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    account = _make_account(db)
    _claim(db, account, "alice")
    with pytest.raises(HTTPException) as exc:
        assert_owns_username(account, "bob", db, status_code=404)
    assert exc.value.status_code == 404


# --- claim_username_if_unowned ----------------------------------------------


def test_claim_unowned_creates_link(monkeypatch, db):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    account = _make_account(db)
    claim_username_if_unowned(account, "Alice", db)
    row = (
        db.query(AccountChessUsername)
        .filter(AccountChessUsername.username == "alice")
        .one()
    )
    assert row.account_id == account.id


def test_claim_already_owned_by_self_noop(monkeypatch, db):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    account = _make_account(db)
    _claim(db, account, "alice")
    claim_username_if_unowned(account, "alice", db)  # no raise
    count = (
        db.query(AccountChessUsername)
        .filter(AccountChessUsername.username == "alice")
        .count()
    )
    assert count == 1


def test_claim_owned_by_other_403(monkeypatch, db):
    monkeypatch.setenv("KNIGHTMIND_REQUIRE_AUTH", "true")
    a = _make_account(db, email="a@x.com")
    b = _make_account(db, email="b@x.com")
    _claim(db, a, "alice")
    with pytest.raises(HTTPException) as exc:
        claim_username_if_unowned(b, "alice", db)
    assert exc.value.status_code == 403


def test_claim_noop_when_off(monkeypatch, db):
    monkeypatch.delenv("KNIGHTMIND_REQUIRE_AUTH", raising=False)
    account = _make_account(db)
    claim_username_if_unowned(account, "alice", db)
    assert (
        db.query(AccountChessUsername)
        .filter(AccountChessUsername.username == "alice")
        .count()
        == 0
    )
