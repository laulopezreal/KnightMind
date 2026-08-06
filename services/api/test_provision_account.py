"""Tests for the operator CLI's handle parsing (``scripts/provision_account.py``).

Lives under ``services/api`` because that is what ``testpaths`` collects, and it
is the API's ownership invariant being defended: ``provision_account`` is the one
writer of ``account_chess_usernames`` that does not pass through the ``Username``
annotation, so whatever the annotation rejects, this has to reject too.
"""

import pytest

from scripts.provision_account import _parse_handles


def test_parse_handles_folds_to_canonical_keys():
    """Non-canonical spellings become the one key the storage layer uses."""
    assert _parse_handles("  Bob  ") == ["bob"]
    assert _parse_handles("Ｂｏｂ") == ["bob"]
    assert _parse_handles("BOB\xa0") == ["bob"]


def test_parse_handles_deduplicates_after_folding():
    """Two spellings of one handle are one claim, in first-seen order."""
    assert _parse_handles("Ｂｏｂ,bob,alice") == ["bob", "alice"]


def test_parse_handles_skips_empty_segments():
    """A trailing or doubled comma is a typo, not an error."""
    assert _parse_handles("lauureal,,hikaru,") == ["lauureal", "hikaru"]
    assert _parse_handles("") == []
    assert _parse_handles(None) == []


@pytest.mark.parametrize(
    "raw, reason",
    [
        ("аlice", "non-ASCII"),  # Cyrillic а, a cross-script homoglyph
        ("x" * 65, "too long"),
    ],
)
def test_parse_handles_rejects_what_the_http_boundary_rejects(raw, reason):
    """A handle no request can address must not become an ownership row.

    Folding alone is not enough, and this is the case that proves it: both of
    these are fixed points of ``canonical_username``, so a fold-only parse
    accepts them and writes the row. Every HTTP path then 422s the same handle
    at the ``Username`` annotation, before ``assert_owns_username`` is reached —
    so the row exists, is owned, and is unreachable by the person who owns it.

    That is the same shape as the fullwidth bug this file's fold was changed to
    fix, arriving through validation rather than through canonicalization.
    """
    with pytest.raises(ValueError):
        _parse_handles(raw)


def test_parse_handles_rejects_a_whole_claim_list_for_one_bad_handle():
    """Fail the run rather than claim the good handles and drop the bad one.

    ``main`` turns this into exit code 2 before ``provision`` opens a session, so
    a mistyped handle cannot leave a half-provisioned account behind.
    """
    with_bad = "lauureal,аlice,hikaru"
    with pytest.raises(ValueError):
        _parse_handles(with_bad)
