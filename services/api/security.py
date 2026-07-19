"""Password hashing (argon2id) and JWT issue/verify primitives.

Kept deliberately small and dependency-only so it can be unit-tested without a
database or FastAPI. The multi-user auth *policy* (flag semantics, dependencies,
ownership checks) lives in ``services/api/identity.py``; this module only knows
how to hash a password and mint/verify a token.

Environment:
- ``KNIGHTMIND_JWT_SECRET`` — symmetric secret for HS256. There is no default:
  callers that require auth must fail closed when it is unset (see identity.py).
- ``KNIGHTMIND_JWT_TTL_MIN`` — access-token lifetime in minutes (default 60).
"""

import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

# argon2id is the current OWASP-recommended password KDF. passlib delegates to
# argon2-cffi; no reliance on the stdlib ``crypt`` module (removed in 3.13).
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

JWT_ALGORITHM = "HS256"
DEFAULT_TTL_MINUTES = 60


class JWTSecretMissingError(RuntimeError):
    """Raised when a token operation is attempted but no secret is configured."""


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2id."""
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored argon2 hash.

    Returns False (never raises) for malformed/unknown hashes so callers can
    treat every failure as an authentication failure uniformly.
    """
    try:
        return _pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def _get_secret() -> str:
    secret = (os.environ.get("KNIGHTMIND_JWT_SECRET") or "").strip()
    if not secret:
        raise JWTSecretMissingError(
            "KNIGHTMIND_JWT_SECRET is not set. It is required to issue or verify "
            "auth tokens when KNIGHTMIND_REQUIRE_AUTH is enabled."
        )
    return secret


def _get_ttl_minutes() -> int:
    raw = (os.environ.get("KNIGHTMIND_JWT_TTL_MIN") or "").strip()
    if not raw:
        return DEFAULT_TTL_MINUTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TTL_MINUTES
    return value if value > 0 else DEFAULT_TTL_MINUTES


def create_access_token(account_id: str, email: str) -> str:
    """Issue a signed, short-lived JWT for an account.

    Raises JWTSecretMissingError if no secret is configured.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": account_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=_get_ttl_minutes())).timestamp()),
    }
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT, returning its claims.

    Raises jwt.InvalidTokenError (or a subclass, e.g. ExpiredSignatureError) on
    any signature/expiry/format problem, and JWTSecretMissingError if unset.
    """
    return jwt.decode(token, _get_secret(), algorithms=[JWT_ALGORITHM])
