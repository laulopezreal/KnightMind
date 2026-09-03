#!/usr/bin/env python3
"""Certify an isolated KnightMind frontend worktree before worker dispatch."""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

MARKER_VERSION = 1
REQUIRED_DEPENDENCIES = ("vitest", "@vitejs/plugin-react", "eslint")


class PreflightError(RuntimeError):
    pass


def _run(command, *, cwd, timeout=None):
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise PreflightError(f"focused test readiness probe timed out after {timeout}s") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise PreflightError(f"command failed ({' '.join(command)}){suffix}") from error


def _git(worktree, *args):
    return _run(["git", *args], cwd=worktree).stdout.strip()


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _worktree_state(raw_worktree, requested_ref, *, require_clean=True):
    worktree = pathlib.Path(raw_worktree).expanduser().resolve(strict=True)
    if not worktree.is_dir():
        raise PreflightError("worktree path is not a directory")

    top_level = pathlib.Path(_git(worktree, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top_level != worktree:
        raise PreflightError("path must name the exact Git worktree root")

    common_git_raw = pathlib.Path(_git(worktree, "rev-parse", "--git-common-dir"))
    if not common_git_raw.is_absolute():
        common_git_raw = worktree / common_git_raw
    canonical_checkout = common_git_raw.resolve(strict=True).parent
    if worktree == canonical_checkout:
        raise PreflightError("canonical checkout is out of scope")

    git_entry = worktree / ".git"
    if not git_entry.is_file():
        raise PreflightError("candidate must be an isolated linked Git worktree")

    status = _git(worktree, "status", "--porcelain=v1", "--untracked-files=all")
    if require_clean and status:
        raise PreflightError("candidate Git worktree is not clean")

    head = _git(worktree, "rev-parse", "HEAD")
    requested = _git(worktree, "rev-parse", f"{requested_ref}^{{commit}}")
    if requested != head:
        raise PreflightError(
            f"requested ref does not match HEAD (requested {requested}, HEAD {head})"
        )

    frontend = worktree / "apps" / "web"
    lockfile = frontend / "package-lock.json"
    if not lockfile.is_file() or lockfile.is_symlink():
        raise PreflightError("frontend package-lock.json is missing or unsafe")

    return {
        "worktree": worktree,
        "canonical_checkout": canonical_checkout,
        "frontend": frontend,
        "head": head,
        "lockfile": lockfile,
        "lockfile_sha256": _sha256(lockfile),
        "requested_ref": requested_ref,
        "status": status,
    }


def _check_dependencies(state):
    frontend = state["frontend"]
    modules = frontend / "node_modules"
    try:
        modules_stat = modules.lstat()
    except FileNotFoundError as error:
        raise PreflightError("frontend dependency directory is missing") from error
    if stat.S_ISLNK(modules_stat.st_mode) or not stat.S_ISDIR(modules_stat.st_mode):
        raise PreflightError("frontend dependency directory must be a local, real directory")

    node_program = r"""
const fs = require('fs');
const path = require('path');
const { createRequire } = require('module');
const frontend = fs.realpathSync(process.argv[1]);
const localModules = fs.realpathSync(path.join(frontend, 'node_modules'));
const requireFromFrontend = createRequire(path.join(frontend, 'package.json'));
const result = {};
for (const dependency of process.argv.slice(2)) {
  const resolved = fs.realpathSync(requireFromFrontend.resolve(dependency));
  if (resolved !== localModules && !resolved.startsWith(localModules + path.sep)) {
    throw new Error(`${dependency} resolved outside candidate node_modules: ${resolved}`);
  }
  result[dependency] = resolved;
}
process.stdout.write(JSON.stringify(result));
"""
    try:
        resolved = _run(
            ["node", "-e", node_program, str(frontend), *REQUIRED_DEPENDENCIES],
            cwd=frontend,
        )
    except PreflightError as error:
        raise PreflightError(f"frontend dependency resolution failed: {error}") from error

    vitest_bin = modules / ".bin" / "vitest"
    try:
        resolved_vitest_bin = vitest_bin.resolve(strict=True)
    except FileNotFoundError as error:
        raise PreflightError("frontend dependency executable is missing: vitest") from error
    if not _is_within(resolved_vitest_bin, modules.resolve(strict=True)):
        raise PreflightError("vitest executable resolves outside candidate node_modules")
    if not os.access(vitest_bin, os.X_OK):
        raise PreflightError("frontend vitest executable is not executable")

    return json.loads(resolved.stdout)


def _test_target(state, raw_target):
    target = pathlib.PurePosixPath(raw_target)
    if target.is_absolute() or not target.parts or any(part in ("", ".", "..") for part in target.parts):
        raise PreflightError("test target must be a safe path relative to apps/web")
    unresolved = state["frontend"] / pathlib.Path(*target.parts)
    if unresolved.is_symlink():
        raise PreflightError("test target must not be a symlink")
    candidate = unresolved.resolve(strict=True)
    if not _is_within(candidate, state["frontend"].resolve(strict=True)):
        raise PreflightError("test target escapes apps/web")
    if not candidate.is_file() or candidate.is_symlink():
        raise PreflightError("test target must be a regular file")
    return target.as_posix()


def _marker_payload(state, dependencies, test_target):
    return {
        "version": MARKER_VERSION,
        "status": "ready",
        "worktree": str(state["worktree"]),
        "head": state["head"],
        "requested_ref": state["requested_ref"],
        "frontend_lockfile": str(state["lockfile"].relative_to(state["worktree"])),
        "frontend_lockfile_sha256": state["lockfile_sha256"],
        "test_target": test_target,
        "dependencies": dependencies,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _safe_marker_dir(raw_marker_dir, state):
    marker_dir = pathlib.Path(raw_marker_dir).expanduser().resolve()
    if _is_within(marker_dir, state["worktree"]) or _is_within(
        marker_dir, state["canonical_checkout"]
    ):
        raise PreflightError("marker directory must be outside every repository checkout")
    marker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return marker_dir.resolve(strict=True)


def _write_marker(marker_dir, payload):
    key = hashlib.sha256(payload["worktree"].encode("utf-8")).hexdigest()[:24]
    destination = marker_dir / f"{key}.json"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{key}.", dir=marker_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return destination


def _load_and_verify_marker(raw_marker, state):
    marker = pathlib.Path(raw_marker).expanduser().resolve(strict=True)
    if _is_within(marker, state["worktree"]) or _is_within(
        marker, state["canonical_checkout"]
    ):
        raise PreflightError("marker must be outside every repository checkout")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PreflightError("marker is missing or malformed") from error

    expected = {
        "version": MARKER_VERSION,
        "status": "ready",
        "worktree": str(state["worktree"]),
        "head": state["head"],
        "frontend_lockfile": str(state["lockfile"].relative_to(state["worktree"])),
        "frontend_lockfile_sha256": state["lockfile_sha256"],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise PreflightError("stale marker: worktree HEAD or frontend lockfile changed")
    return marker, payload


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Certify an isolated KnightMind frontend worktree before dispatch."
    )
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--marker-dir", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--test-target")
    action.add_argument("--verify-marker")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 600:
        parser.error("--timeout-seconds must be between 1 and 600")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        state = _worktree_state(
            args.worktree, args.ref, require_clean=not bool(args.verify_marker)
        )
        dependencies = _check_dependencies(state)
        if args.verify_marker:
            marker, payload = _load_and_verify_marker(args.verify_marker, state)
            if state["status"]:
                raise PreflightError("candidate Git worktree is not clean")
            result = {
                "status": "verified",
                "marker": str(marker),
                "worktree": payload["worktree"],
                "head": payload["head"],
                "frontend_lockfile_sha256": payload["frontend_lockfile_sha256"],
            }
        else:
            target = _test_target(state, args.test_target)
            before = (state["head"], state["lockfile_sha256"])
            vitest_bin = state["frontend"] / "node_modules" / ".bin" / "vitest"
            _run(
                [str(vitest_bin), "run", target],
                cwd=state["frontend"],
                timeout=args.timeout_seconds,
            )
            after = _worktree_state(args.worktree, args.ref)
            if before != (after["head"], after["lockfile_sha256"]):
                raise PreflightError("worktree HEAD or lockfile changed during readiness probe")
            payload = _marker_payload(after, dependencies, target)
            marker_dir = _safe_marker_dir(args.marker_dir, after)
            marker = _write_marker(marker_dir, payload)
            result = {**payload, "marker": str(marker)}
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, PreflightError) as error:
        print(f"frontend worktree preflight failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
