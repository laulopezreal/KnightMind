#!/usr/bin/env python3
"""Check practical frontend worktree readiness before worker dispatch."""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import pwd
import secrets
import stat
import subprocess
import sys
import tempfile

MARKER_VERSION = 2
MAX_JSON_BYTES = 1024 * 1024
REQUIRED_DEPENDENCIES = ("vitest", "@vitejs/plugin-react", "eslint")
MARKER_FIELDS = {
    "version",
    "status",
    "worktree",
    "head",
    "requested_ref",
    "frontend_lockfile",
    "frontend_lockfile_sha256",
    "installed_lockfile_sha256",
    "test_target",
    "test_target_sha256",
    "dependencies",
    "created_at",
}


class PreflightError(RuntimeError):
    pass


def _run(command, *, cwd, classification, timeout=None):
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
        raise PreflightError(f"{classification} timed out") from error
    except subprocess.CalledProcessError as error:
        raise PreflightError(f"{classification} failed") from error
    except OSError as error:
        raise PreflightError(f"{classification} could not start") from error


def _git(worktree, *args):
    return _run(
        ["git", *args], cwd=worktree, classification="Git worktree inspection"
    ).stdout.strip()


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256(path):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise PreflightError("required file could not be read") from error
    return digest.hexdigest()


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_json(path, classification, *, descriptor=None):
    try:
        if descriptor is None:
            data = path.read_bytes()
        else:
            chunks = []
            remaining = MAX_JSON_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        if not data or len(data) > MAX_JSON_BYTES:
            raise ValueError("invalid JSON size")
        return json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PreflightError(f"{classification} is malformed") from error


def _worktree_state(raw_worktree, requested_ref, *, require_clean=True):
    try:
        worktree = pathlib.Path(raw_worktree).expanduser().resolve(strict=True)
    except OSError as error:
        raise PreflightError("worktree path is unavailable") from error
    if not worktree.is_dir():
        raise PreflightError("worktree path is not a directory")

    try:
        top_level = pathlib.Path(
            _git(worktree, "rev-parse", "--show-toplevel")
        ).resolve(strict=True)
        common_git_raw = pathlib.Path(_git(worktree, "rev-parse", "--git-common-dir"))
        if not common_git_raw.is_absolute():
            common_git_raw = worktree / common_git_raw
        canonical_checkout = common_git_raw.resolve(strict=True).parent
    except OSError as error:
        raise PreflightError("Git worktree metadata is unsafe") from error
    if top_level != worktree:
        raise PreflightError("path must name the exact Git worktree root")
    if worktree == canonical_checkout:
        raise PreflightError("canonical checkout is out of scope")
    if not (worktree / ".git").is_file():
        raise PreflightError("candidate must be an isolated linked Git worktree")

    status = _git(worktree, "status", "--porcelain=v1", "--untracked-files=all")
    if require_clean and status:
        raise PreflightError("candidate Git worktree is not clean")
    head = _git(worktree, "rev-parse", "HEAD")
    requested = _git(worktree, "rev-parse", f"{requested_ref}^{{commit}}")
    if requested != head:
        raise PreflightError("requested ref does not match HEAD")

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


def _regular_local_file(path, parent, classification):
    try:
        metadata = path.lstat()
        canonical = path.resolve(strict=True)
        parent_canonical = parent.resolve(strict=True)
    except OSError as error:
        raise PreflightError(f"{classification} is missing or unsafe") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or not _is_within(canonical, parent_canonical)
    ):
        raise PreflightError(f"{classification} is missing or unsafe")
    return canonical


def _check_dependencies(state):
    frontend = state["frontend"]
    modules = frontend / "node_modules"
    try:
        modules_stat = modules.lstat()
    except OSError as error:
        raise PreflightError("frontend dependency directory is missing") from error
    if stat.S_ISLNK(modules_stat.st_mode) or not stat.S_ISDIR(modules_stat.st_mode):
        raise PreflightError(
            "frontend dependency directory must be a local, real directory"
        )

    project_lock = _read_json(state["lockfile"], "frontend lockfile")
    installed_lock_path = modules / ".package-lock.json"
    installed_lock = _read_json(installed_lock_path, "installed dependency lockfile")
    project_packages = (
        project_lock.get("packages") if isinstance(project_lock, dict) else None
    )
    installed_packages = (
        installed_lock.get("packages") if isinstance(installed_lock, dict) else None
    )
    if not isinstance(project_packages, dict) or not isinstance(
        installed_packages, dict
    ):
        raise PreflightError("dependency lockfile package map is malformed")

    evidence = {}
    for dependency in REQUIRED_DEPENDENCIES:
        package_key = f"node_modules/{dependency}"
        project_entry = project_packages.get(package_key)
        installed_entry = installed_packages.get(package_key)
        if not isinstance(project_entry, dict) or not isinstance(installed_entry, dict):
            raise PreflightError("required dependency is absent from lockfile evidence")
        for field in ("version", "resolved", "integrity"):
            if installed_entry.get(field) != project_entry.get(field):
                raise PreflightError(
                    "installed dependency evidence does not match project lockfile"
                )
        version = project_entry.get("version")
        if not isinstance(version, str) or not version:
            raise PreflightError("required dependency version evidence is malformed")

        package_dir = modules.joinpath(*dependency.split("/"))
        manifest_path = _regular_local_file(
            package_dir / "package.json", modules, "dependency package manifest"
        )
        manifest = _read_json(manifest_path, "dependency package manifest")
        if not isinstance(manifest, dict) or manifest.get("name") != dependency:
            raise PreflightError("dependency package identity is malformed")
        if manifest.get("version") != version:
            raise PreflightError("installed dependency version does not match lockfile")
        evidence[dependency] = {
            "version": version,
            "lock_entry_sha256": hashlib.sha256(
                json.dumps(project_entry, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest(),
            "package_json_sha256": _sha256(manifest_path),
        }

    vitest_manifest = _read_json(
        modules / "vitest" / "package.json", "Vitest package manifest"
    )
    bin_value = (
        vitest_manifest.get("bin") if isinstance(vitest_manifest, dict) else None
    )
    if isinstance(bin_value, dict):
        bin_value = bin_value.get("vitest")
    if isinstance(bin_value, str) and bin_value.startswith("./"):
        bin_value = bin_value[2:]
    if (
        not isinstance(bin_value, str)
        or not bin_value
        or pathlib.PurePosixPath(bin_value).is_absolute()
    ):
        raise PreflightError("Vitest package executable declaration is malformed")
    if any(part in ("", ".", "..") for part in pathlib.PurePosixPath(bin_value).parts):
        raise PreflightError("Vitest package executable declaration is malformed")
    vitest_cli = _regular_local_file(
        modules / "vitest" / pathlib.Path(*pathlib.PurePosixPath(bin_value).parts),
        modules / "vitest",
        "Vitest package executable",
    )
    vitest_launcher = modules / ".bin" / "vitest"
    try:
        launcher_stat = vitest_launcher.lstat()
        launcher_target = os.readlink(vitest_launcher)
        resolved_launcher = vitest_launcher.resolve(strict=True)
    except OSError as error:
        raise PreflightError(
            "Vitest dependency launcher is missing or unsafe"
        ) from error
    expected_launcher_target = os.path.relpath(vitest_cli, vitest_launcher.parent)
    if (
        not stat.S_ISLNK(launcher_stat.st_mode)
        or launcher_stat.st_uid != os.geteuid()
        or launcher_target != expected_launcher_target
        or resolved_launcher != vitest_cli
    ):
        raise PreflightError(
            "Vitest dependency launcher is not bound to its package entrypoint"
        )
    evidence["vitest"]["entrypoint"] = str(vitest_cli.relative_to(modules))
    evidence["vitest"]["entrypoint_sha256"] = _sha256(vitest_cli)
    evidence["vitest"]["launcher_target"] = launcher_target
    return {
        "evidence": evidence,
        "installed_lockfile_sha256": _sha256(installed_lock_path),
        "vitest_cli": vitest_cli,
    }


def _prepare_dependencies(state, timeout):
    _run(
        ["npm", "ci", "--ignore-scripts"],
        cwd=state["frontend"],
        classification="isolated frontend dependency preparation",
        timeout=timeout,
    )


def _test_target(state, raw_target):
    target = pathlib.PurePosixPath(raw_target)
    if (
        target.is_absolute()
        or not target.parts
        or any(part in ("", ".", "..") for part in target.parts)
        or "\\" in raw_target
    ):
        raise PreflightError(
            "test target must be a canonical path relative to apps/web"
        )
    unresolved = state["frontend"] / pathlib.Path(*target.parts)
    candidate = _regular_local_file(unresolved, state["frontend"], "test target")
    return {
        "path": target.as_posix(),
        "sha256": _sha256(candidate),
        "canonical": candidate,
    }


def _probe_vitest(state, dependencies, target, timeout):
    descriptor, report_name = tempfile.mkstemp(
        prefix="knightmind-vitest-", suffix=".json"
    )
    os.close(descriptor)
    report = pathlib.Path(report_name)
    try:
        _run(
            [
                "node",
                str(dependencies["vitest_cli"]),
                "run",
                target["path"],
                "--reporter=json",
                "--outputFile",
                str(report),
            ],
            cwd=state["frontend"],
            classification="focused Vitest readiness probe",
            timeout=timeout,
        )
        proof = _read_json(report, "focused Vitest readiness proof")
    finally:
        try:
            report.unlink()
        except OSError:
            pass
    results = proof.get("testResults") if isinstance(proof, dict) else None
    matching = []
    if isinstance(results, list):
        for result in results:
            if not isinstance(result, dict) or not isinstance(result.get("name"), str):
                continue
            try:
                result_path = pathlib.Path(result["name"]).resolve(strict=True)
            except OSError:
                continue
            if result_path == target["canonical"]:
                matching.append(result)
    if (
        type(proof.get("numPassedTests")) is not int
        or proof["numPassedTests"] < 1
        or proof.get("numFailedTests") != 0
        or len(matching) != 1
        or matching[0].get("status") != "passed"
        or not isinstance(matching[0].get("assertionResults"), list)
        or not matching[0]["assertionResults"]
        or any(
            item.get("status") != "passed"
            for item in matching[0]["assertionResults"]
            if isinstance(item, dict)
        )
    ):
        raise PreflightError("focused Vitest readiness proof is invalid")


def _canonical_marker_root():
    authority_home = pathlib.Path(pwd.getpwuid(os.geteuid()).pw_dir)
    return (
        authority_home
        / ".local"
        / "state"
        / "knightmind"
        / "frontend-worktree-preflight"
    )


def _safe_marker_dir(raw_marker_dir, state, *, create):
    expected = _canonical_marker_root()
    _validate_marker_root_arg(raw_marker_dir, state)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(expected.anchor, flags)
    try:
        for part in expected.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        root_stat = os.fstat(descriptor)
    except OSError as error:
        os.close(descriptor)
        raise PreflightError("marker root is missing or unsafe") from error
    if root_stat.st_uid != os.geteuid() or stat.S_IMODE(root_stat.st_mode) != 0o700:
        os.close(descriptor)
        raise PreflightError("marker root ownership or mode is unsafe")
    return expected, descriptor


def _validate_marker_root_arg(raw_marker_dir, state):
    expected = _canonical_marker_root()
    supplied = pathlib.Path(raw_marker_dir).expanduser()
    if not supplied.is_absolute() or supplied.parts != expected.parts:
        raise PreflightError("marker root is not the canonical authority-owned root")
    if _is_within(expected, state["worktree"]) or _is_within(
        expected, state["canonical_checkout"]
    ):
        raise PreflightError("marker root must be outside every repository checkout")


def _marker_key(state):
    return hashlib.sha256(str(state["worktree"]).encode("utf-8")).hexdigest()[:24]


def _marker_payload(state, dependencies, target):
    return {
        "version": MARKER_VERSION,
        "status": "ready",
        "worktree": str(state["worktree"]),
        "head": state["head"],
        "requested_ref": state["requested_ref"],
        "frontend_lockfile": str(state["lockfile"].relative_to(state["worktree"])),
        "frontend_lockfile_sha256": state["lockfile_sha256"],
        "installed_lockfile_sha256": dependencies["installed_lockfile_sha256"],
        "test_target": target["path"],
        "test_target_sha256": target["sha256"],
        "dependencies": dependencies["evidence"],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _write_marker(marker_dir, marker_dir_fd, payload, state):
    key = _marker_key(state)
    destination = marker_dir / f"{key}.json"
    temporary = f".{key}.{secrets.token_hex(16)}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=marker_dir_fd,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temporary,
            destination.name,
            src_dir_fd=marker_dir_fd,
            dst_dir_fd=marker_dir_fd,
        )
        os.fsync(marker_dir_fd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=marker_dir_fd)
        except OSError:
            pass
        raise
    return destination


def _validate_marker_shape(payload):
    if not isinstance(payload, dict) or set(payload) != MARKER_FIELDS:
        raise PreflightError("marker schema is invalid")
    scalar_types = {
        "version": int,
        "status": str,
        "worktree": str,
        "head": str,
        "requested_ref": str,
        "frontend_lockfile": str,
        "frontend_lockfile_sha256": str,
        "installed_lockfile_sha256": str,
        "test_target": str,
        "test_target_sha256": str,
        "created_at": str,
    }
    if any(
        type(payload[key]) is not expected for key, expected in scalar_types.items()
    ):
        raise PreflightError("marker schema is invalid")
    if payload["version"] != MARKER_VERSION or payload["status"] != "ready":
        raise PreflightError("marker schema is invalid")
    if not isinstance(payload["dependencies"], dict) or set(
        payload["dependencies"]
    ) != set(REQUIRED_DEPENDENCIES):
        raise PreflightError("marker schema is invalid")
    for dependency, evidence in payload["dependencies"].items():
        fields = {"version", "lock_entry_sha256", "package_json_sha256"}
        if dependency == "vitest":
            fields |= {"entrypoint", "entrypoint_sha256", "launcher_target"}
        if (
            not isinstance(evidence, dict)
            or set(evidence) != fields
            or any(type(value) is not str or not value for value in evidence.values())
        ):
            raise PreflightError("marker schema is invalid")
    try:
        created = datetime.datetime.fromisoformat(payload["created_at"])
    except ValueError as error:
        raise PreflightError("marker schema is invalid") from error
    if created.tzinfo is None:
        raise PreflightError("marker schema is invalid")


def _load_marker(marker, marker_dir, marker_dir_fd):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(marker.name, flags, dir_fd=marker_dir_fd)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise PreflightError("marker file ownership or mode is unsafe")
        payload = _read_json(marker, "marker", descriptor=descriptor)
    except OSError as error:
        raise PreflightError("marker is missing or unsafe") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if marker.parent != marker_dir:
        raise PreflightError("marker path is outside the canonical marker root")
    return payload


def _load_and_verify_marker(raw_marker, marker_dir, marker_dir_fd, state, dependencies):
    supplied = pathlib.Path(raw_marker).expanduser()
    expected = marker_dir / f"{_marker_key(state)}.json"
    if not supplied.is_absolute() or supplied.parts != expected.parts:
        raise PreflightError("marker path is not canonical for this worktree")
    payload = _load_marker(supplied, marker_dir, marker_dir_fd)
    _validate_marker_shape(payload)
    target = _test_target(state, payload["test_target"])
    expected_payload = _marker_payload(state, dependencies, target)
    expected_payload["created_at"] = payload["created_at"]
    if payload != expected_payload:
        raise PreflightError("stale marker: certified worktree evidence changed")
    return supplied, payload


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Practical workflow readiness control for an isolated KnightMind frontend "
            "worktree. It checks clean/ref state, worktree-local dependencies, and a real "
            "focused Vitest result."
        ),
        epilog=(
            "Supported workflow: run with --prepare and --test-target to perform "
            "worktree-local `npm ci --ignore-scripts` from package-lock.json and then "
            "write the default marker; run --verify-marker later without a path. "
            "This is not an integrity attestation, adversarial sandbox, or defense "
            "against a same-UID actor able to change the candidate or marker."
        ),
    )
    parser.add_argument(
        "--worktree", required=True, help="exact isolated linked worktree root"
    )
    parser.add_argument(
        "--ref", required=True, help="commit/ref that must equal candidate HEAD"
    )
    parser.add_argument(
        "--marker-dir",
        default=str(_canonical_marker_root()),
        help="marker root (default: %(default)s)",
    )
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="run worktree-local npm ci --ignore-scripts before the readiness probe",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--test-target", help="focused test path relative to apps/web")
    action.add_argument(
        "--verify-marker",
        nargs="?",
        const="",
        metavar="PATH",
        help="verify the marker for this worktree (default path when PATH is omitted)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 600:
        parser.error("--timeout-seconds must be between 1 and 600")
    if args.prepare and args.verify_marker is not None:
        parser.error("--prepare is only valid with --test-target")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        state = _worktree_state(args.worktree, args.ref, require_clean=False)
        _validate_marker_root_arg(args.marker_dir, state)
        if args.verify_marker is not None:
            marker_dir, marker_dir_fd = _safe_marker_dir(
                args.marker_dir, state, create=False
            )
            try:
                try:
                    dependencies = _check_dependencies(state)
                except PreflightError as error:
                    raise PreflightError(
                        "stale marker: certified dependency evidence changed"
                    ) from error
                marker, payload = _load_and_verify_marker(
                    args.verify_marker
                    or str(marker_dir / f"{_marker_key(state)}.json"),
                    marker_dir,
                    marker_dir_fd,
                    state,
                    dependencies,
                )
                if state["status"]:
                    raise PreflightError("candidate Git worktree is not clean")
                result = {
                    "status": "verified",
                    "marker": str(marker),
                    "worktree": payload["worktree"],
                    "head": payload["head"],
                    "frontend_lockfile_sha256": payload["frontend_lockfile_sha256"],
                }
            finally:
                os.close(marker_dir_fd)
        else:
            if state["status"]:
                raise PreflightError("candidate Git worktree is not clean")
            if args.prepare:
                _prepare_dependencies(state, args.timeout_seconds)
                state = _worktree_state(args.worktree, args.ref)
            dependencies = _check_dependencies(state)
            marker_dir, marker_dir_fd = _safe_marker_dir(
                args.marker_dir, state, create=True
            )
            target = _test_target(state, args.test_target)
            try:
                before = _marker_payload(state, dependencies, target)
                _probe_vitest(state, dependencies, target, args.timeout_seconds)
                after_state = _worktree_state(args.worktree, args.ref)
                after_dependencies = _check_dependencies(after_state)
                after_target = _test_target(after_state, args.test_target)
                after = _marker_payload(after_state, after_dependencies, after_target)
                before["created_at"] = after["created_at"]
                if before != after:
                    raise PreflightError(
                        "worktree evidence changed during readiness probe"
                    )
                marker = _write_marker(marker_dir, marker_dir_fd, after, after_state)
                result = {**after, "marker": str(marker)}
            finally:
                os.close(marker_dir_fd)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except PreflightError as error:
        print(f"frontend worktree preflight failed: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError):
        print(
            "frontend worktree preflight failed: filesystem validation failed",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
