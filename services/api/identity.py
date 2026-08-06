"""Multi-user authorization policy: flag, dependencies, ownership checks.

This is the enforcement layer that sits *above* the per-username data model.
Authentication (who you are) comes from a JWT bearer token verified in
``security.py``; authorization (what you may touch) is decided here by matching
the authenticated account against the Chess.com usernames it has claimed in
``account_chess_usernames``.

Rollout flag — ``KNIGHTMIND_REQUIRE_AUTH`` (default OFF):
  OFF  → ``require_account`` returns ``None`` and ``assert_owns_username`` /
         ``claim_username_if_unowned`` are NO-OPs. The app behaves EXACTLY as it
         does today, so merging this code changes nothing at runtime and the
         live frontend (which sends no token yet) is unaffected.
  ON   → every guarded route requires a valid bearer token and enforces
         object-level ownership.

The existing tailnet ``require_operator`` gate (``auth.py``) is intentionally
left untouched: operator endpoints stay gated to the tailnet, not to accounts.

Username folding — ``account_chess_usernames.username`` is a storage key, so it
obeys the storage-boundary rule in ``usernames.py``: ``canonical_username`` is
the only fold, used for BOTH the lookup and the write. This module used to
carry its own ``normalize_username`` (``.strip().lower()``), which is not a
weaker canonicalization but a *different* one — it does not NFKC-fold, so
``Ｂｏｂ`` claimed a row at ``ｂｏｂ`` that no canonical lookup could ever reach.
Cross-script homoglyphs are still NOT folded (``аlice`` with a Cyrillic а stays
distinct from ``alice``); NFKC folds compatibility forms, not scripts, so that
deliberate property survives unchanged.

No backfill ships with this change, deliberately: ``account_chess_usernames`` is
empty in production (auth has never been switched on), so there is nothing to
canonicalize. Should a non-canonical row ever exist, it is stranded rather than
corrupting anything — ``/auth/me`` reports it verbatim and ``assert_owns_username``
declines to match it. If this table is ever populated before this code ships, a
one-off ``UPDATE`` folding the existing rows has to come first.
"""

import os

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.api.db import get_db
from services.api.models import Account, AccountChessUsername
from services.api.security import JWTSecretMissingError, decode_access_token
from services.api.usernames import canonical_username

_TRUTHY = {"1", "true", "yes", "on"}


def auth_required() -> bool:
    """Whether enforcement is turned on (``KNIGHTMIND_REQUIRE_AUTH``)."""
    return os.environ.get("KNIGHTMIND_REQUIRE_AUTH", "").strip().lower() in _TRUTHY


def _parse_bearer(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _resolve_account(token: str, db: Session) -> Account | None:
    """Decode a token and load the (enabled) account it points at, or None."""
    try:
        claims = decode_access_token(token)
    except (jwt.InvalidTokenError, JWTSecretMissingError):
        return None
    sub = claims.get("sub")
    if not sub:
        return None
    account = db.get(Account, sub)
    if account is None or account.disabled:
        return None
    return account


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Account | None:
    """FastAPI dependency: the authenticated account, or ``None`` when auth is off.

    Flag OFF: returns ``None`` unconditionally (no token is required or inspected)
    so behavior is identical to today.
    Flag ON: requires a valid, unexpired bearer token for an enabled account;
    raises 401 otherwise.
    """
    if not auth_required():
        return None
    token = _parse_bearer(authorization)
    if token is None:
        raise _unauthorized()
    account = _resolve_account(token, db)
    if account is None:
        raise _unauthorized()
    return account


def require_authenticated_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Account:
    """Dependency for routes that are meaningless without an identity (``/auth/me``).

    Always requires a valid token regardless of the rollout flag.
    """
    token = _parse_bearer(authorization)
    if token is None:
        raise _unauthorized()
    account = _resolve_account(token, db)
    if account is None:
        raise _unauthorized()
    return account


def get_owned_usernames(account: Account, db: Session) -> list[str]:
    """All Chess.com usernames claimed by an account, exactly as stored.

    Canonical by construction, not by anything this function does: every write
    to ``account_chess_usernames`` folds with ``canonical_username`` first. The
    rows are returned verbatim — ``/auth/me`` hands them straight to the caller,
    so re-folding here would report a handle the ownership check then refuses.
    """
    rows = db.scalars(
        select(AccountChessUsername.username).where(
            AccountChessUsername.account_id == account.id
        )
    ).all()
    return list(rows)


def assert_owns_username(
    account: Account | None,
    username: str,
    db: Session,
    *,
    status_code: int = 403,
) -> None:
    """Assert the account has claimed ``username``; NO-OP when auth is off.

    Username-addressed routes use the default 403. ID-addressed routes (jobs,
    sessions) pass ``status_code=404`` so a resource owned by another tenant is
    indistinguishable from one that does not exist.
    """
    if not auth_required():
        return
    if account is None:
        # Enforcement on but no identity resolved — should not happen when the
        # route also depends on require_account, but fail closed regardless.
        raise _unauthorized()
    owned = db.scalar(
        select(AccountChessUsername).where(
            AccountChessUsername.account_id == account.id,
            AccountChessUsername.username == canonical_username(username),
        )
    )
    if owned is None:
        raise HTTPException(
            status_code=status_code,
            detail=(
                "Not Found"
                if status_code == 404
                else "You do not have access to this data"
            ),
        )


def claim_username_if_unowned(
    account: Account | None,
    username: str,
    db: Session,
) -> None:
    """First-importer-wins claim; NO-OP when auth is off.

    Inserts an ownership row when the handle is unclaimed. If it is already owned
    by this account, no-op. If owned by a *different* account, 403. Relies on
    ``UNIQUE(username)`` to resolve concurrent first-import races.

    Does not commit — the caller's transaction owns the boundary.
    """
    if not auth_required():
        return
    if account is None:
        raise _unauthorized()

    normalized = canonical_username(username)
    existing = db.scalar(
        select(AccountChessUsername).where(AccountChessUsername.username == normalized)
    )
    if existing is not None:
        if existing.account_id != account.id:
            raise HTTPException(
                status_code=403, detail="This username is claimed by another account"
            )
        return

    link = AccountChessUsername(account_id=account.id, username=normalized)
    db.add(link)
    try:
        db.flush()
    except IntegrityError:
        # Lost the race: another request claimed it first. Re-resolve owner.
        db.rollback()
        winner = db.scalar(
            select(AccountChessUsername).where(
                AccountChessUsername.username == normalized
            )
        )
        if winner is None or winner.account_id != account.id:
            raise HTTPException(
                status_code=403, detail="This username is claimed by another account"
            ) from None
