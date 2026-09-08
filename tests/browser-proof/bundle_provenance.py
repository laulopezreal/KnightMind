"""Commit-bound provenance for the browser-proof production bundle."""

import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
from types import MappingProxyType

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
MANIFEST_NAME = ".knightmind-bproof-provenance.json"
VITE_ENV_FILES = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.production.local",
)
BUILD_INPUT_FILES = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.production.local",
    ".npmrc",
    ".postcssrc",
    ".postcssrc.cjs",
    ".postcssrc.cts",
    ".postcssrc.js",
    ".postcssrc.json",
    ".postcssrc.mjs",
    ".postcssrc.mts",
    ".postcssrc.ts",
    ".postcssrc.yaml",
    ".postcssrc.yml",
    "index.html",
    "package-lock.json",
    "package.json",
    "postcss.config.cjs",
    "postcss.config.cts",
    "postcss.config.js",
    "postcss.config.mjs",
    "postcss.config.mts",
    "postcss.config.ts",
    "tailwind.config.cjs",
    "tailwind.config.cts",
    "tailwind.config.js",
    "tailwind.config.mjs",
    "tailwind.config.mts",
    "tailwind.config.ts",
    "tsconfig.app.json",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.cjs",
    "vite.config.cts",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.mts",
    "vite.config.ts",
    "vitest.config.cjs",
    "vitest.config.cts",
    "vitest.config.js",
    "vitest.config.mjs",
    "vitest.config.mts",
    "vitest.config.ts",
)
STAT_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
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


def _safe_inventory_path(path):
    if type(path) is not str or not path or path == MANIFEST_NAME or "\\" in path:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        return False
    pure_path = pathlib.PurePosixPath(path)
    return (
        not pure_path.is_absolute()
        and pure_path.parts
        and all(part not in ("", ".", "..") for part in pure_path.parts)
        and str(pure_path) == path
    )


def _hash_regular_file(path, initial_stat):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ValueError("bundle entry is not regular")
        if any(
            getattr(initial_stat, field) != getattr(opened_stat, field)
            for field in STAT_IDENTITY_FIELDS
        ):
            raise ValueError("bundle entry changed during inventory")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        final_stat = os.fstat(descriptor)
        if any(
            getattr(opened_stat, field) != getattr(final_stat, field)
            for field in STAT_IDENTITY_FIELDS
        ):
            raise ValueError("bundle entry changed during inventory")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _stat_identity(value):
    return tuple(getattr(value, field) for field in STAT_IDENTITY_FIELDS)


def _snapshot_directory(directory):
    """Capture entries and prove the directory stayed stable during enumeration."""
    before = os.stat(directory, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError("bundle entry is not a directory")
    with os.scandir(directory) as iterator:
        entries = sorted(
            (
                entry.name,
                entry.path,
                entry.stat(follow_symlinks=False),
            )
            for entry in iterator
        )
    after = os.stat(directory, follow_symlinks=False)
    if _stat_identity(before) != _stat_identity(after):
        raise ValueError("bundle directory changed during inventory")
    return entries, _stat_identity(after)


def _bundle_inventory(dist):
    """Return a stable content inventory, rejecting every nonregular entry."""
    dist = pathlib.Path(dist)
    inventory = []

    def visit(directory):
        entries, directory_identity = _snapshot_directory(directory)
        initial_entries = [
            (name, _stat_identity(entry_stat))
            for name, _entry_path, entry_stat in entries
        ]
        for _name, entry_path, entry_stat in entries:
            relative = pathlib.Path(entry_path).relative_to(dist).as_posix()
            if relative == MANIFEST_NAME:
                continue
            if not _safe_inventory_path(relative):
                raise ValueError("unsafe bundle entry")
            if stat.S_ISDIR(entry_stat.st_mode):
                visit(pathlib.Path(entry_path))
            elif stat.S_ISREG(entry_stat.st_mode):
                inventory.append(
                    {
                        "path": relative,
                        "sha256": _hash_regular_file(entry_path, entry_stat),
                    }
                )
            else:
                raise ValueError("bundle entry is not regular")
        final_entries, final_directory_identity = _snapshot_directory(directory)
        if directory_identity != final_directory_identity or initial_entries != [
            (name, _stat_identity(entry_stat))
            for name, _entry_path, entry_stat in final_entries
        ]:
            raise ValueError("bundle directory changed during inventory")

    visit(dist)
    inventory.sort(key=lambda item: item["path"])
    return inventory


def _valid_inventory(value):
    if type(value) is not list:
        return False
    paths = []
    for item in value:
        if (
            type(item) is not dict
            or set(item) != {"path", "sha256"}
            or not _safe_inventory_path(item.get("path"))
            or type(item.get("sha256")) is not str
            or DIGEST_RE.fullmatch(item["sha256"]) is None
        ):
            return False
        paths.append(item["path"])
    return (
        paths == sorted(paths)
        and len(paths) == len(set(paths))
        and "index.html" in paths
    )


def write_manifest(dist, commit):
    """Atomically record the checkout that produced a successfully built dist."""
    dist = pathlib.Path(dist).resolve(strict=True)
    if not _valid_commit(commit):
        raise ValueError(f"invalid commit SHA: {commit!r}")
    target = manifest_path(dist)
    if target.is_symlink():
        raise ValueError("manifest target must not be a symlink")
    inventory = _bundle_inventory(dist)
    if not any(item["path"] == "index.html" for item in inventory):
        raise ValueError(f"built bundle missing index.html at {dist}")
    payload = {
        "version": 2,
        "commit": commit,
        "dist": str(dist),
        "inventory": inventory,
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{MANIFEST_NAME}.", dir=dist)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        target_stat = target.lstat()
        if target.is_symlink() or not stat.S_ISREG(target_stat.st_mode):
            raise ValueError("manifest target must be a regular file")
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


def _is_build_input(path):
    pure_path = pathlib.PurePosixPath(path)
    return (
        len(pure_path.parts) >= 4
        and pure_path.parts[:3] in (("apps", "web", "src"), ("apps", "web", "public"))
    ) or (
        len(pure_path.parts) == 3
        and pure_path.parts[:2] == ("apps", "web")
        and pure_path.name in BUILD_INPUT_FILES
    )


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
    vite_files = [
        str(frontend / name) for name in VITE_ENV_FILES if (frontend / name).is_file()
    ]
    if vite_files:
        raise RuntimeError(
            "Vite environment files are not allowed for commit-bound proof: "
            + ", ".join(vite_files)
        )
    changed = [
        path
        for path in subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_root),
                "diff",
                "--name-only",
                "-z",
                "HEAD",
                "--",
                "apps/web",
            ],
            text=True,
        ).split("\0")
        if path and _is_build_input(path)
    ]
    if changed:
        raise RuntimeError(
            "tracked build input is dirty against HEAD: " + ", ".join(sorted(changed))
        )
    untracked = [
        path
        for path in subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--others",
                "-z",
                "--",
                "apps/web",
            ],
            text=True,
        ).split("\0")
        if path and _is_build_input(path)
    ]
    if untracked:
        raise RuntimeError(
            "untracked build input is not commit-bound: " + ", ".join(sorted(untracked))
        )


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
        target_stat = target.lstat()
        if not stat.S_ISREG(target_stat.st_mode):
            raise ValueError("manifest target is not regular")
        descriptor = os.open(
            target,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_dev != target_stat.st_dev
                or opened_stat.st_ino != target_stat.st_ino
            ):
                raise ValueError("manifest target changed during open")
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                descriptor = None
                payload = json.load(handle)
        finally:
            if descriptor is not None:
                os.close(descriptor)
    except (TypeError, ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        raise RuntimeError("bundle provenance missing or malformed") from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload.get("version") != 2
        or set(payload) != {"version", "commit", "dist", "inventory"}
        or not _valid_commit(payload.get("commit"))
        or not isinstance(payload.get("dist"), str)
        or not _valid_inventory(payload.get("inventory"))
    ):
        raise RuntimeError("bundle provenance malformed")
    if payload["dist"] != str(expected_dist):
        raise RuntimeError("bundle provenance malformed")
    try:
        manifest_dist = pathlib.Path(payload["dist"])
        if (
            manifest_dist.is_symlink()
            or manifest_dist.resolve(strict=True) != expected_dist
        ):
            raise RuntimeError("manifest artifact path is not canonical")
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise RuntimeError("bundle provenance malformed") from exc
    if not _valid_commit(expected_commit):
        raise RuntimeError("bundle provenance malformed")
    if payload["commit"] != expected_commit:
        raise RuntimeError("bundle provenance mismatch")
    try:
        actual_inventory = _bundle_inventory(expected_dist)
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise RuntimeError("bundle provenance mismatch") from exc
    if payload["inventory"] != actual_inventory:
        raise RuntimeError("bundle provenance mismatch")
    return payload


def _load_inventory_file(path, expected_digest):
    initial_stat = os.stat(path, follow_symlinks=False)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(initial_stat.st_mode)
            or not stat.S_ISREG(opened_stat.st_mode)
            or _stat_identity(initial_stat) != _stat_identity(opened_stat)
        ):
            raise ValueError("bundle entry changed before immutable load")
        chunks = []
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            digest.update(chunk)
        if (
            _stat_identity(opened_stat) != _stat_identity(os.fstat(descriptor))
            or digest.hexdigest() != expected_digest
        ):
            raise ValueError("bundle entry changed during immutable load")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def load_certified_bundle(dist, expected_commit):
    """Load certified bytes into an immutable artifact detached from source paths."""
    payload = validate_manifest(dist, expected_commit)
    expected_dist = pathlib.Path(dist)
    try:
        bundle = {
            item["path"]: _load_inventory_file(
                expected_dist / pathlib.PurePosixPath(item["path"]), item["sha256"]
            )
            for item in payload["inventory"]
        }
        validate_manifest(dist, expected_commit)
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise RuntimeError("bundle provenance mismatch") from exc
    return MappingProxyType(bundle)


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
