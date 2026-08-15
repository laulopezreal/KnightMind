"""Every environment variable the code reads must appear in .env.example.

This exists because the file had drifted several ways at once: seven variables
were undocumented -- including the two that decide whether auth is enforced --
a comment described a deleted SQLite variable and told operators to use a
backend the API now refuses outright, and STOCKFISH_MOVETIME_MS was documented
while nothing read it.

Documentation drift is invisible until an operator needs the variable, which is
exactly when they can least afford to go reading source. So the check is
mechanical: find the reads, assert each is mentioned, and assert nothing is
mentioned that no longer exists.

Known limits, stated rather than papered over:

- Names built at runtime are invisible to a static scan. ``ratelimit.py`` does
  exactly this -- ``env_int(f"RATE_LIMIT_{upper}", ...)`` -- so those six
  variables are listed by hand and checked by
  ``test_dynamic_rate_limit_names_are_documented``, which also fails if the set
  of limiters in the code changes.
- The scan resolves ``os.environ``/``os.getenv`` plus the wrappers in
  ``ENV_WRAPPERS`` by name. A new wrapper needs adding there.
"""

import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Functions taking an environment variable NAME as their first argument.
# os.environ[...] subscripts are handled separately.
ENV_WRAPPERS = {"getenv", "get", "env_int", "_env_seconds", "_get_env_positive"}

# Read by tooling, never set by an operator deploying this service.
NOT_OPERATOR_FACING = {
    # Test harness, set by CI and conftest.
    "KNIGHTMIND_TEST_DATABASE_URL",
    "KNIGHTMIND_TEST_POSTGRES_URL",
    # A one-shot argument to `python -m scripts.provision_account`, not service
    # configuration. .env.example is copied to .env.docker for the running API,
    # so listing it here would imply the service reads it. Documented in that
    # script's own docstring instead.
    "KNIGHTMIND_PROVISION_PASSWORD",
}

# Built by f-string at ratelimit.py:165-166, so no literal exists to find.
RATE_LIMITERS = (
    "DIAGNOSE",
    "ENGINE_EVAL",
    "IMPORT_CHESSCOM",
    "OPENINGS_BASELINE",
    "PUZZLES_GENERATE",
    "RATINGS_SNAPSHOT",
)

# Looks like an environment variable: SCREAMING_SNAKE, long enough not to be an
# incidental constant. Deliberately NOT restricted to KNIGHTMIND_* -- the first
# version of this guard was, which left 14 variables unguarded while its
# docstring claimed to cover "every environment flag the code reads".
_ENVISH = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")


def _env_names(source: str) -> set[str]:
    """Variable names passed to an environment read, via the AST.

    Two earlier attempts got this wrong in opposite directions, both silently.

    A bare name regex matched prose: ``db.py``'s module docstring explains the
    SQLite fallback it no longer has, so ``KNIGHTMIND_DEV_SQLITE`` looked like a
    live variable needing documentation.

    Pinning it to ``os.environ.get(...)`` then matched too little, because most
    variables here are read through wrappers --
    ``env_int("KNIGHTMIND_DB_POOL_SIZE", 10)`` -- so six live ones looked stale.

    Matching the CALL rather than the name resolves both: comments never reach
    the AST, a docstring is not a call argument, and a name reaching os.environ
    through any wrapper is still a literal at that wrapper's call site.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            fn = node.func
            fname = (
                fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            )
            first = node.args[0]
            if (
                fname in ENV_WRAPPERS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _ENVISH.match(first.value)
            ):
                names.add(first.value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            names.add(node.slice.value)
        # `_FLAG = "KNIGHTMIND_AI_DIAGNOSIS"` then `os.environ.get(_FLAG)`:
        # there is no literal at the call site, so match the binding too. A
        # constant that merely looks env-ish costs nothing here -- it can only
        # make the stale-check slightly more permissive, never miss a read.
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str) and _ENVISH.match(node.value.value):
                names.add(node.value.value)
    return names


def _compose_interpolations() -> set[str]:
    """`${VAR}` / `${VAR:-default}` in compose files.

    POSTGRES_*, API_PORT and friends are never read by Python -- Docker Compose
    substitutes them before the container starts. Without this they look like
    documented-but-dead entries, which is how the first version of the
    stale-check reported six false positives.
    """
    names: set[str] = set()
    for name in ("docker-compose.yml", "docker-compose.yaml"):
        path = REPO_ROOT / name
        if path.exists():
            names |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]{3,})[:}-]", path.read_text()))
    return names


def _source_files() -> list[Path]:
    """Tracked, non-test Python that runs in production or deployment."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # An exported tree (sdist, Docker image) has no .git. Skipping says so;
        # failing with git's raw exit code 128 explains nothing.
        pytest.skip(f"needs a git checkout to enumerate source files: {exc}")

    files = [
        REPO_ROOT / rel
        for rel in listed
        if "alembic/versions" not in rel
        and not Path(rel).name.startswith("test_")
        and Path(rel).name != "conftest.py"
    ]
    # A sparse or relocated checkout returns few or no files, and every guard
    # below would then pass over an empty set. Fail loudly, not vacuously.
    assert (
        len(files) > 50
    ), f"only {len(files)} source files found; checkout looks wrong"
    return files


def _vars_read_by_the_code() -> set[str]:
    found: set[str] = set()
    for path in _source_files():
        found |= _env_names(path.read_text())
    return found - NOT_OPERATOR_FACING


def test_every_env_var_is_documented():
    documented = ENV_EXAMPLE.read_text()
    undocumented = sorted(
        name for name in _vars_read_by_the_code() if name not in documented
    )
    assert not undocumented, (
        "These variables are read by the code but absent from .env.example:\n  "
        + "\n  ".join(undocumented)
        + "\nAdd them with their default and what changing them does."
    )


def test_dynamic_rate_limit_names_are_documented():
    """ratelimit.py builds its names by f-string, so the AST scan cannot see them.

    Without this, a new limiter would silently escape the guard above. The
    second assertion is the important one: it fails when the code's set of
    limiters drifts from the list here, so this cannot quietly go stale.
    """
    documented = ENV_EXAMPLE.read_text()
    missing = [n for n in RATE_LIMITERS if f"RATE_LIMIT_{n}" not in documented]
    assert not missing, f"undocumented rate limiters: {missing}"

    sources = "\n".join(p.read_text() for p in _source_files())
    in_code = {
        m.group(1).upper()
        for m in re.finditer(r"""rate_limit\(\s*["']([a-z_]+)["']""", sources)
    }
    assert in_code == set(RATE_LIMITERS), (
        f"RATE_LIMITERS is stale: code has {sorted(in_code)}, this list has "
        f"{sorted(RATE_LIMITERS)}. Document any new one in .env.example too."
    )


def test_known_limiters_matches_the_registered_routes():
    """`ratelimit.KNOWN_LIMITERS` must not drift from the code.

    It is a THIRD list, beside this file's RATE_LIMITERS and the actual
    `rate_limit(...)` call sites, and until now nothing checked it — a comment
    beside it claimed this test did, which was simply false.

    It is not decorative: `_longest_configured_window()` uses it to decide how
    long the hourly purge must keep rate-limit rows. A limiter missing from it
    is invisible to that calculation, so a window longer than the floor would
    have its live hits deleted every hour and its limit silently reset.
    """
    from services.api.ratelimit import KNOWN_LIMITERS

    sources = "\n".join(p.read_text() for p in _source_files())
    in_code = {
        m.group(1)
        for m in re.finditer(r"""rate_limit\(\s*["']([a-z_]+)["']""", sources)
    }
    assert set(KNOWN_LIMITERS) == in_code, (
        f"KNOWN_LIMITERS is stale: code registers {sorted(in_code)}, "
        f"the list has {sorted(KNOWN_LIMITERS)}"
    )


def test_env_example_documents_nothing_the_code_dropped():
    """A documented variable nothing reads is a stale promise.

    STOCKFISH_MOVETIME_MS was exactly this: documented here and in the README,
    read nowhere, having outlived whatever once used it.
    """
    read = _vars_read_by_the_code() | _compose_interpolations()
    read |= {f"RATE_LIMIT_{n}" for n in RATE_LIMITERS}
    read |= {f"RATE_LIMIT_{n}_WINDOW" for n in RATE_LIMITERS}
    documented = {
        m.group(1)
        for m in re.finditer(
            r"^#?\s*([A-Z][A-Z0-9_]{3,})=", ENV_EXAMPLE.read_text(), re.M
        )
    }
    stale = sorted(documented - read - NOT_OPERATOR_FACING)
    assert not stale, (
        ".env.example documents variables nothing reads any more:\n  "
        + "\n  ".join(stale)
        + "\nRemove them, or the file promises behaviour that does not exist."
    )


def test_no_sqlite_is_offered_as_a_backend():
    """SQLite is not supported; db.py rejects any URL starting with "sqlite".

    Checked against the README as well as .env.example, because the README is
    the likelier first stop when setting this up -- and it went on claiming
    SQLite support for local development long after db.py stopped allowing it.
    The pattern is `sqlite:` rather than `sqlite:///` so `sqlite://`, a valid
    in-memory URL, cannot slip through the way it could before.
    """
    for doc in (ENV_EXAMPLE, REPO_ROOT / "README.md"):
        text = doc.read_text().lower()
        assert "sqlite:" not in text, (
            f"{doc.name} shows a sqlite: URL. services/api/db.py rejects any URL "
            "starting with 'sqlite', so this documents a setup that cannot work."
        )
        assert "supports sqlite" not in text, (
            f"{doc.name} claims SQLite support. It was removed; db.py fails fast "
            "on a SQLite URL."
        )
