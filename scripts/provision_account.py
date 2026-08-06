"""Operator CLI: provision a KnightMind account and claim Chess.com handles.

Registration is operator-provisioned (there is no public signup endpoint), so
this is how the first account(s) are created and bound to the Chess.com
usernames whose data already lives in the database.

Idempotent:
    - Re-running with the same email updates nothing unless --update-password is
      given (in which case the password hash is rotated).
    - Claims are additive and race-safe via UNIQUE(username): a handle already
      owned by THIS account is skipped; a handle owned by ANOTHER account aborts
      with a clear error (first-importer-wins is a hard invariant).

Usage:
    # Provision the operator account that owns all three existing handles.
    # Never hardcode credentials in the repo — pass them at run time.
    python -m scripts.provision_account \
        --email you@example.com \
        --password 'REDACTED' \
        --claim lauureal,alfi3sr,hikaru

    # Read the password from an env var instead of the argv (avoids shell history):
    KNIGHTMIND_PROVISION_PASSWORD='REDACTED' python -m scripts.provision_account \
        --email you@example.com --claim lauureal,alfi3sr,hikaru

Requires:
    - DATABASE_URL set (Postgres; `make docker-up` starts one locally).
    - The accounts / account_chess_usernames tables to exist (alembic upgrade head).
"""

import argparse
import os
import sys

from sqlalchemy import select

from services.api.db import SessionLocal
from services.api.models import Account, AccountChessUsername
from services.api.security import hash_password
from services.api.usernames import canonical_username, validate_username


def _parse_handles(raw: str | None) -> list[str]:
    """Split ``--claim`` into canonical handles, preserving order, deduplicated.

    This is the one entry point that writes ``account_chess_usernames`` without
    passing through the ``Username`` annotation, so it has to do the annotation's
    whole job itself, not half of it.

    ``validate_username``, not ``canonical_username``: the fold alone stops
    ``--claim Ｂｏｂ`` writing ``ｂｏｂ``, but it accepts handles the HTTP boundary
    rejects outright — a Cyrillic ``аlice`` or anything over 64 characters folds
    to itself and persists a row that every request 422s before an ownership
    check can ever see it. Unreachable by a different route is still unreachable.

    Raises ``ValueError`` on an invalid handle so ``main`` can fail the run
    before anything is written. Empty segments are skipped rather than rejected,
    so a trailing or doubled comma stays a typo instead of an error.
    """
    if not raw:
        return []
    seen: list[str] = []
    for part in raw.split(","):
        if not canonical_username(part):
            continue
        handle = validate_username(part)
        if handle not in seen:
            seen.append(handle)
    return seen


def provision(
    email: str,
    password: str | None,
    handles: list[str],
    *,
    update_password: bool = False,
) -> int:
    email = email.strip().lower()
    if not email:
        print("error: --email is required", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.email == email))

        if account is None:
            if not password:
                print(
                    "error: creating a new account requires a password "
                    "(--password or KNIGHTMIND_PROVISION_PASSWORD)",
                    file=sys.stderr,
                )
                return 2
            account = Account(email=email, password_hash=hash_password(password))
            db.add(account)
            db.flush()
            print(f"created account {email} (id={account.id})")
        else:
            print(f"account {email} already exists (id={account.id})")
            if update_password:
                if not password:
                    print(
                        "error: --update-password given but no password provided",
                        file=sys.stderr,
                    )
                    return 2
                account.password_hash = hash_password(password)
                print("  rotated password hash")

        for handle in handles:
            existing = db.scalar(
                select(AccountChessUsername).where(
                    AccountChessUsername.username == handle
                )
            )
            if existing is not None:
                if existing.account_id == account.id:
                    print(f"  claim '{handle}': already owned by this account (skip)")
                else:
                    print(
                        f"error: claim '{handle}' is owned by another account "
                        f"(id={existing.account_id}). Aborting; no changes committed.",
                        file=sys.stderr,
                    )
                    db.rollback()
                    return 1
            else:
                db.add(AccountChessUsername(account_id=account.id, username=handle))
                db.flush()
                print(f"  claimed '{handle}'")

        db.commit()
        print("done.")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision a KnightMind account and claim Chess.com handles."
    )
    parser.add_argument("--email", required=True, help="Account email (login).")
    parser.add_argument(
        "--password",
        default=os.environ.get("KNIGHTMIND_PROVISION_PASSWORD"),
        help="Account password (or set KNIGHTMIND_PROVISION_PASSWORD).",
    )
    parser.add_argument(
        "--claim",
        default="",
        help="Comma-separated Chess.com usernames to claim (e.g. lauureal,alfi3sr,hikaru).",
    )
    parser.add_argument(
        "--update-password",
        action="store_true",
        help="Rotate the password hash if the account already exists.",
    )
    args = parser.parse_args()

    try:
        handles = _parse_handles(args.claim)
    except ValueError as exc:
        # Same rejection the Username annotation applies to HTTP callers, so the
        # CLI cannot create ownership rows a request could never address.
        print(f"error: invalid --claim handle: {exc}", file=sys.stderr)
        sys.exit(2)

    code = provision(
        args.email,
        args.password,
        handles,
        update_password=args.update_password,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
