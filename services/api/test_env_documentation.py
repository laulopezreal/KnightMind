"""Every environment flag the code reads must appear in .env.example.

This exists because the file had drifted twice over: seven flags were
undocumented -- including the three that decide whether auth is enforced -- and
a comment describing a deleted SQLite variable was still telling operators to
use a backend the API now refuses outright.

Documentation drift is invisible until an operator needs the flag, which is
exactly when they can least afford to go reading source. So the check is
mechanical: find the env reads, assert each is mentioned.
"""

import ast
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Test-harness plumbing, set by CI and conftest, never by an operator.
NOT_OPERATOR_FACING = {
    "KNIGHTMIND_TEST_DATABASE_URL",
    "KNIGHTMIND_TEST_POSTGRES_URL",
    # A one-shot argument to `python -m scripts.provision_account`, not service
    # configuration. .env.example is copied to .env.docker for the running API,
    # so listing it here would imply the service reads it. Documented in that
    # script's own docstring instead.
    "KNIGHTMIND_PROVISION_PASSWORD",
}

_FLAG = re.compile(r"^KNIGHTMIND_[A-Z0-9_]+$")


def _flag_literals(source: str) -> set[str]:
    """Flag names used as string literals in *code*, via the AST.

    Two earlier attempts got this wrong in opposite directions, which is worth
    recording because both failures were silent:

    A bare name regex matched prose. ``db.py``'s module docstring explains the
    SQLite fallback it no longer has, so ``KNIGHTMIND_DEV_SQLITE`` looked like
    a live flag needing documentation.

    Pinning the regex to ``os.environ.get(...)`` then matched too little: most
    flags here are read through wrappers -- ``env_int("KNIGHTMIND_DB_POOL_SIZE",
    10)`` and the AI-diagnosis helpers -- so six documented, actively-read flags
    looked stale.

    The AST cuts the knot. Comments never appear in it, docstrings are the one
    statement shape that can be skipped precisely, and a name reaching os.environ
    through any depth of helper is still a string literal at its call site.
    """
    tree = ast.parse(source)
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node not in docstrings
        and _FLAG.match(node.value)
    }


def _flags_read_by_the_code() -> set[str]:
    """Flags read by non-test source, via git's file list.

    Uses ``git ls-files`` rather than a directory walk so untracked scratch
    files and virtualenvs cannot influence the result.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "services/*.py", "scripts/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    found: set[str] = set()
    for rel in tracked:
        name = Path(rel).name
        if name.startswith("test_") or name == "conftest.py":
            continue
        found |= _flag_literals((REPO_ROOT / rel).read_text())
    return found - NOT_OPERATOR_FACING


def test_every_env_flag_is_documented():
    documented = ENV_EXAMPLE.read_text()
    undocumented = sorted(
        flag for flag in _flags_read_by_the_code() if flag not in documented
    )
    assert not undocumented, (
        "These flags are read by the code but absent from .env.example:\n  "
        + "\n  ".join(undocumented)
        + "\nAdd them with their default and what turning them on does."
    )


def test_env_example_documents_no_flag_the_code_dropped():
    """The other direction: a documented flag nothing reads is a stale promise.

    This is the failure the SQLite comment actually was -- the variable went
    away with the fallback, the prose stayed and kept advertising it.
    """
    read = _flags_read_by_the_code()
    documented = set(re.findall(r"\bKNIGHTMIND_[A-Z0-9_]+\b", ENV_EXAMPLE.read_text()))
    stale = sorted(documented - read - NOT_OPERATOR_FACING)
    assert not stale, (
        ".env.example documents flags nothing reads any more:\n  "
        + "\n  ".join(stale)
        + "\nRemove them, or the file promises behaviour that does not exist."
    )


def test_no_sqlite_is_offered_anywhere_in_the_example():
    """SQLite is not a supported backend; the API rejects such a URL outright.

    A worked SQLite example in this file sends an operator down a path that
    fails at startup, which is how the deleted variable's comment survived.
    """
    text = ENV_EXAMPLE.read_text().lower()
    assert "sqlite:///" not in text, (
        ".env.example shows a sqlite:/// URL. services/api/db.py refuses "
        "SQLite URLs, so this documents a configuration that cannot work."
    )
