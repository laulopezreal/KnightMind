"""Commit-bound provenance for the browser-proof production bundle."""

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
MANIFEST_NAME = ".knightmind-bproof-provenance.json"
VITE_ENV_FILES = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.production.local",
)


def _valid_commit(value):
    return type(value) is str and SHA_RE.fullmatch(value) is not None


def manifest_path(dist):
    return pathlib.Path(dist) / MANIFEST_NAME


def _expected_artifact_path(dist):
    """Return the one existing canonical directory accepted by validation."""
    if not isinstance(dist, (str, os.PathLike)):
        raise TypeError("artifact path must be path-like")
    return pathlib.Path(dist).resolve(strict=True)


def write_manifest(dist, commit):
    """Atomically record the checkout that produced a successfully built dist."""
    dist = pathlib.Path(dist).resolve()
    if not _valid_commit(commit):
        raise ValueError(f"invalid commit SHA: {commit!r}")
    if not (dist / "index.html").is_file():
        raise ValueError(f"built bundle missing index.html at {dist}")
    payload = {"version": 1, "commit": commit, "dist": str(dist)}
    target = manifest_path(dist)
    fd, temporary = tempfile.mkstemp(prefix=f".{MANIFEST_NAME}.", dir=dist)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def certify_build(repo_root, dist, commit):
    """Recheck build inputs immediately before certifying a completed bundle."""
    assert_build_inputs_clean(repo_root)
    write_manifest(dist, commit)


def assert_build_inputs_clean(repo_root):
    """Reject build inputs that could make a bundle differ from clean HEAD."""
    repo_root = pathlib.Path(repo_root).resolve()
    frontend = repo_root / "apps/web"
    vite_overrides = sorted(name for name in os.environ if name.startswith("VITE_"))
    if vite_overrides:
        raise RuntimeError(
            "Vite environment overrides are not allowed for commit-bound proof: "
            + ", ".join(vite_overrides)
        )
    vite_files = [str(frontend / name) for name in VITE_ENV_FILES if (frontend / name).is_file()]
    if vite_files:
        raise RuntimeError(
            "Vite environment files are not allowed for commit-bound proof: "
            + ", ".join(vite_files)
        )
    tracked = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files", "apps/web"], text=True
    ).splitlines()
    relevant = [
        path
        for path in tracked
        if path.startswith(("apps/web/src/", "apps/web/public/"))
        or pathlib.PurePosixPath(path).name
        in {
            "index.html",
            "package.json",
            "package-lock.json",
            "vite.config.ts",
            "vitest.config.ts",
            "tsconfig.json",
            "tsconfig.app.json",
            "tsconfig.node.json",
        }
    ]
    changed = subprocess.check_output(
        ["git", "-C", str(repo_root), "diff", "--name-only", "HEAD", "--", *sorted(relevant)],
        text=True,
    ).splitlines()
    if changed:
        raise RuntimeError("tracked build input is dirty against HEAD: " + ", ".join(changed))


def validate_manifest(dist, expected_commit):
    """Fail closed unless the manifest exactly identifies this checkout and dist."""
    try:
        expected_dist = _expected_artifact_path(dist)
        if type(dist) is not str or dist != str(expected_dist):
            raise ValueError("artifact path is not canonical")
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise RuntimeError("bundle provenance malformed") from exc
    try:
        target = manifest_path(expected_dist)
        if target.is_symlink():
            raise ValueError("manifest target is a symlink")
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise RuntimeError("bundle provenance malformed") from exc
    try:
        with target.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (TypeError, ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        raise RuntimeError("bundle provenance missing or malformed") from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload.get("version") != 1
        or set(payload) != {"version", "commit", "dist"}
        or not _valid_commit(payload.get("commit"))
        or not isinstance(payload.get("dist"), str)
    ):
        raise RuntimeError("bundle provenance malformed")
    if payload["dist"] != str(expected_dist):
        raise RuntimeError("bundle provenance malformed")
    try:
        manifest_dist = pathlib.Path(payload["dist"])
        if manifest_dist.is_symlink() or manifest_dist.resolve(strict=True) != expected_dist:
            raise RuntimeError("manifest artifact path is not canonical")
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise RuntimeError("bundle provenance malformed") from exc
    if not _valid_commit(expected_commit):
        raise RuntimeError("bundle provenance malformed")
    if payload["commit"] != expected_commit:
        raise RuntimeError("bundle provenance mismatch")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "check":
        assert_build_inputs_clean(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "write":
        write_manifest(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == "certify":
        certify_build(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit(
            f"usage: {sys.argv[0]} check REPO_ROOT | write DIST COMMIT | "
            "certify REPO_ROOT DIST COMMIT"
        )
