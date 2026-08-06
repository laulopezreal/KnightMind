"""Authentication endpoints: email + password login → JWT bearer, plus /auth/me.

Registration is deliberately absent: accounts are operator-provisioned via
``scripts/provision_account.py`` (see the product decision), so there is no
public signup surface yet.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.db import get_db
from services.api.identity import (
    get_owned_usernames,
    require_authenticated_account,
)
from services.api.models import Account
from services.api.security import (
    JWTSecretMissingError,
    create_access_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: str
    email: str
    usernames: list[str]


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Exchange email + password for a signed JWT bearer token."""
    email = request.email.strip().lower()
    account = db.scalar(select(Account).where(Account.email == email))

    # Verify a hash even when the account is missing to avoid leaking, via timing,
    # whether an email exists. Uniform 401 for missing / wrong-password / disabled.
    stored_hash = (
        account.password_hash
        if account is not None
        # argon2 hash of a random-ish constant; never matches a real password.
        else "$argon2id$v=19$m=65536,t=3,p=4$"
        "AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    password_ok = verify_password(request.password, stored_hash)

    if account is None or account.disabled or not password_ok:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    try:
        token = create_access_token(account.id, account.email)
    except JWTSecretMissingError as e:
        # Auth is being used but the server is misconfigured — fail closed.
        raise HTTPException(
            status_code=503, detail="Authentication is not configured"
        ) from e

    return TokenResponse(access_token=token)


@router.get("/me", response_model=MeResponse)
def me(
    account: Account = Depends(require_authenticated_account),
    db: Session = Depends(get_db),
):
    """Return the authenticated account and the usernames it owns.

    The stored values are returned as-is, not re-folded on read. Every write to
    ``account_chess_usernames`` goes through ``canonical_username`` (see
    ``identity.claim_username_if_unowned``), so a row is canonical by
    construction and a second fold here could only change a value that the
    ownership check would then refuse to match — reporting a handle the caller
    does not actually own. Folding on read is the "false assurance" the
    storage-boundary rule in ``usernames.py`` warns about.
    """
    usernames = get_owned_usernames(account, db)
    return MeResponse(id=account.id, email=account.email, usernames=usernames)
