import json
import os
import pathlib
import pwd
import runpy
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PREFLIGHT = REPO_ROOT / "scripts" / "frontend_worktree_preflight.py"


class FrontendWorktreePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.marker_dir = (
            pathlib.Path(pwd.getpwuid(os.geteuid()).pw_dir)
            / ".local"
            / "state"
            / "knightmind"
            / "frontend-worktree-preflight"
        )
        self.marker_before = set(self.marker_dir.iterdir()) if self.marker_dir.exists() else set()
        self.canonical = self.root / "canonical"
        self.candidate = self.root / "candidate"
        self._git("init", "-b", "main", str(self.canonical), cwd=self.root)
        self._git("config", "user.email", "preflight@example.invalid")
        self._git("config", "user.name", "Preflight Test")
        (self.canonical / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        web = self.canonical / "apps" / "web"
        web.mkdir(parents=True)
        (web / "package.json").write_text(
            json.dumps(
                {
                    "name": "web",
                    "private": True,
                    "devDependencies": {
                        "vitest": "1.0.0",
                        "@vitejs/plugin-react": "1.0.0",
                        "eslint": "1.0.0",
                    },
                }
            ),
            encoding="utf-8",
        )
        lock = {
            "name": "web",
            "lockfileVersion": 3,
            "packages": {
                "": {
                    "name": "web",
                    "devDependencies": {
                        "vitest": "1.0.0",
                        "@vitejs/plugin-react": "1.0.0",
                        "eslint": "1.0.0",
                    },
                },
                "node_modules/vitest": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/vitest/-/vitest-1.0.0.tgz",
                    "integrity": "sha512-QUFBQQ==",
                },
                "node_modules/@vitejs/plugin-react": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/@vitejs/plugin-react/-/plugin-react-1.0.0.tgz",
                    "integrity": "sha512-QkJCQg==",
                },
                "node_modules/eslint": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/eslint/-/eslint-1.0.0.tgz",
                    "integrity": "sha512-Q0NDQw==",
                },
            },
        }
        (web / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
        (web / "focused.test.js").write_text(
            "import { expect, test } from 'vitest'; test('proof', () => expect(1).toBe(1));\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "fixture")
        self._git("worktree", "add", "-b", "candidate", str(self.candidate), "HEAD")

    def tearDown(self):
        if self.marker_dir.exists():
            for artifact in set(self.marker_dir.iterdir()) - self.marker_before:
                artifact.unlink()
        self.temp_dir.cleanup()

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.canonical,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def _prepare_dependencies(self, *, exit_zero_wrapper=False):
        web = self.candidate / "apps" / "web"
        modules = web / "node_modules"
        packages = {
            "vitest": {"name": "vitest", "version": "1.0.0", "bin": {"vitest": "vitest.mjs"}},
            "eslint": {"name": "eslint", "version": "1.0.0", "main": "index.js"},
            "@vitejs/plugin-react": {
                "name": "@vitejs/plugin-react",
                "version": "1.0.0",
                "main": "index.js",
            },
        }
        for package, manifest in packages.items():
            package_dir = modules.joinpath(*package.split("/"))
            package_dir.mkdir(parents=True)
            (package_dir / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
            if package != "vitest":
                (package_dir / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
        (modules / "vitest" / "vitest.mjs").write_text(
            """import fs from 'node:fs'; import path from 'node:path';
const args = process.argv.slice(2); const output = args[args.indexOf('--outputFile') + 1];
const target = args[args.indexOf('run') + 1];
fs.writeFileSync(output, JSON.stringify({numPassedTests:1, numFailedTests:0,
testResults:[{name:path.resolve(target),status:'passed',assertionResults:[{status:'passed'}]}]}));
""",
            encoding="utf-8",
        )
        bin_dir = modules / ".bin"
        bin_dir.mkdir()
        vitest_bin = bin_dir / "vitest"
        if exit_zero_wrapper:
            vitest_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            vitest_bin.chmod(0o755)
        else:
            vitest_bin.symlink_to("../vitest/vitest.mjs")
        lock = json.loads((web / "package-lock.json").read_text(encoding="utf-8"))
        hidden = {"name": "web", "lockfileVersion": 3, "packages": lock["packages"]}
        (modules / ".package-lock.json").write_text(json.dumps(hidden), encoding="utf-8")

    def _run(self, worktree=None, ref="HEAD", verify_marker=None, marker_dir=None):
        command = [
            sys.executable,
            str(PREFLIGHT),
            "--worktree",
            str(worktree or self.candidate),
            "--ref",
            ref,
            "--marker-dir",
            str(marker_dir or self.marker_dir),
            "--timeout-seconds",
            "5",
        ]
        if verify_marker is not None:
            command.extend(["--verify-marker", str(verify_marker)])
        else:
            command.extend(["--test-target", "focused.test.js"])
        return subprocess.run(command, text=True, capture_output=True)

    def _expected_marker(self):
        import hashlib

        key = hashlib.sha256(str(self.candidate.resolve()).encode("utf-8")).hexdigest()[:24]
        return self.marker_dir / f"{key}.json"

    def _certify(self):
        self._prepare_dependencies()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        return pathlib.Path(json.loads(result.stdout)["marker"])

    def test_bound_candidate_passes_and_marker_verifies(self):
        marker = self._certify()
        self.assertTrue(marker.is_file())
        verified = self._run(verify_marker=marker)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["status"], "verified")

    def test_exit_zero_bin_wrapper_cannot_supply_readiness_proof(self):
        self._prepare_dependencies(exit_zero_wrapper=True)
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launcher", result.stderr.lower())

    def test_dependency_free_candidate_fails_before_probe(self):
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dependency", result.stderr.lower())
        self.assertFalse(self._expected_marker().exists())

    def test_minimal_forged_marker_is_rejected(self):
        self._prepare_dependencies()
        self.marker_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        forged = self._expected_marker()
        forged.write_text('{"version":1,"status":"ready"}', encoding="utf-8")
        forged.chmod(0o600)
        result = self._run(verify_marker=forged)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("marker", result.stderr.lower())

    def test_marker_with_unknown_field_is_rejected(self):
        marker = self._certify()
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["surprise"] = True
        marker.write_text(json.dumps(payload), encoding="utf-8")
        result = self._run(verify_marker=marker)
        self.assertNotEqual(result.returncode, 0)

    def test_marker_with_bool_version_is_rejected(self):
        marker = self._certify()
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["version"] = True
        marker.write_text(json.dumps(payload), encoding="utf-8")
        result = self._run(verify_marker=marker)
        self.assertNotEqual(result.returncode, 0)

    def test_symlink_marker_is_rejected(self):
        marker = self._certify()
        backing = self.marker_dir / f".{marker.stem}.backing.json"
        marker.replace(backing)
        marker.symlink_to(backing.name)
        result = self._run(verify_marker=marker)
        self.assertNotEqual(result.returncode, 0)

    def test_marker_path_alias_is_rejected(self):
        marker = self._certify()
        alias = f"{self.marker_dir}/../{self.marker_dir.name}/{marker.name}"
        result = self._run(verify_marker=alias)
        self.assertNotEqual(result.returncode, 0)

    def test_arbitrary_external_marker_root_is_rejected(self):
        self._prepare_dependencies()
        result = self._run(marker_dir=self.root / "other-markers")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("marker root", result.stderr.lower())

    def test_insecure_marker_root_mode_is_rejected(self):
        self._prepare_dependencies()
        script_globals = runpy.run_path(str(PREFLIGHT), run_name="preflight_test")
        unsafe = self.root / "unsafe-marker-root"
        unsafe.mkdir(mode=0o755)
        original = script_globals["_canonical_marker_root"]
        script_globals["_canonical_marker_root"] = lambda: unsafe
        state = {
            "worktree": self.candidate.resolve(),
            "canonical_checkout": self.canonical.resolve(),
        }
        try:
            with self.assertRaises(script_globals["PreflightError"]):
                script_globals["_safe_marker_dir"](str(unsafe), state, create=False)
        finally:
            script_globals["_canonical_marker_root"] = original

    def test_marker_is_rejected_after_test_target_drift(self):
        marker = self._certify()
        target = self.candidate / "apps/web/focused.test.js"
        target.write_text("// drift\n", encoding="utf-8")
        result = self._run(verify_marker=marker)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale marker", result.stderr.lower())

    def test_marker_is_rejected_after_dependency_drift(self):
        marker = self._certify()
        cli = self.candidate / "apps/web/node_modules/vitest/vitest.mjs"
        cli.write_text("process.exit(0);\n", encoding="utf-8")
        result = self._run(verify_marker=marker)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale marker", result.stderr.lower())

    def test_marker_is_rejected_after_uncommitted_lockfile_change(self):
        marker = self._certify()
        lockfile = self.candidate / "apps/web/package-lock.json"
        lockfile.write_text('{"lockfileVersion":3,"changed":true}\n', encoding="utf-8")
        result = self._run(verify_marker=marker)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale marker", result.stderr.lower())

    def test_failing_probe_does_not_leak_raw_diagnostics_or_paths(self):
        self._prepare_dependencies()
        secret = "TOP_SECRET_789"
        sensitive_path = str(self.root / "private" / "token.txt")
        cli = self.candidate / "apps/web/node_modules/vitest/vitest.mjs"
        cli.write_text(
            f"console.error({json.dumps(secret + ' ' + sensitive_path)}); process.exit(2);\n",
            encoding="utf-8",
        )
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stderr)
        self.assertNotIn(str(self.root), result.stderr)
        self.assertLess(len(result.stderr), 200)

    def test_canonical_checkout_is_rejected_without_running_probe(self):
        self._prepare_dependencies()
        before = self._git("status", "--porcelain", cwd=self.canonical)
        result = self._run(worktree=self.canonical)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical checkout", result.stderr.lower())
        self.assertEqual(before, self._git("status", "--porcelain", cwd=self.canonical))


if __name__ == "__main__":
    unittest.main()
