"""Canonical username handling shared across every API entry point.

A username can arrive as a path segment, a query parameter, or a field in a
request body. Historically some handlers lowercased it, some did not, and none
rejected whitespace-only input — so ``Bob``, ``bob`` and ``" bob "`` addressed
three *different* logical users, and ``"   "`` silently returned an empty-data
200 instead of an error. That violates the canonical-username invariant: one
person must map to exactly one storage key at every boundary.

This module is that single boundary. ``canonical_username`` folds any inbound
handle to one key; ``validate_username`` additionally rejects invalid handles
with a ``ValueError`` (surfaced by FastAPI as HTTP 422). The ``Username``
annotated type wires the validator into path params, query params, and Pydantic
body fields so canonicalization is uniform and hard to forget.

Safety note: production data is already stored under lowercase ASCII handles
(``lauureal``, ``alfi3sr``, ``hikaru``), which are fixed points of this fold —
canonicalizing existing rows changes nothing. This only makes *entry points*
canonical and rejects invalid input.
"""

import unicodedata
from typing import Annotated

from pydantic import BeforeValidator

# Chess.com handles are short ASCII; the API already capped query params at 64.
MAX_USERNAME_LENGTH = 64


def canonical_username(raw: str | None) -> str:
    """Fold a raw username to its canonical storage/lookup key.

    Order matters: NFKC-normalize first, so compatibility forms (fullwidth
    ``Ｂｏｂ``, ligatures, exotic whitespace) collapse to their plain ASCII
    equivalents, then strip surrounding whitespace, then lowercase. The result
    is what MUST be used for BOTH the query and any write, so case/whitespace/
    compatibility variants can never fork a user's data.

    This is a pure fold with no validation — use ``validate_username`` (or the
    ``Username`` annotated type) at request boundaries to also reject invalid
    handles.
    """
    text = unicodedata.normalize("NFKC", "" if raw is None else str(raw))
    return text.strip().lower()


def validate_username(raw: str | None) -> str:
    """Canonicalize and validate an inbound username.

    Raises ``ValueError`` (returned by FastAPI as HTTP 422 with the message)
    when the handle is empty/whitespace-only, longer than
    ``MAX_USERNAME_LENGTH``, or contains non-ASCII characters that survive NFKC
    folding. The ASCII check rejects cross-script homoglyphs (e.g. the Cyrillic
    ``аlice``) that NFKC does not fold and that would otherwise masquerade as a
    distinct Latin user. Real Chess.com handles are ASCII by construction, so
    this never rejects a genuine account.
    """
    username = canonical_username(raw)
    if not username:
        raise ValueError("Username must not be empty or whitespace-only")
    if len(username) > MAX_USERNAME_LENGTH:
        raise ValueError(
            f"Username must be at most {MAX_USERNAME_LENGTH} characters long"
        )
    if not username.isascii():
        raise ValueError("Username must contain only ASCII characters")
    return username


# Reusable annotation. Use directly for path params, plain query params, and
# Pydantic body fields. For a query param that also needs ``Query()`` metadata
# (description / limits), wrap it: ``Annotated[Username, Query(...)]``.
Username = Annotated[str, BeforeValidator(validate_username)]
