import json
import pathlib
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
        self.canonical = self.root / "canonical"
        self.candidate = self.root / "candidate"
        self.marker_dir = self.root / "markers"
        self._git("init", "-b", "main", str(self.canonical), cwd=self.root)
        self._git("config", "user.email", "preflight@example.invalid")
        self._git("config", "user.name", "Preflight Test")
        (self.canonical / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        web = self.canonical / "apps" / "web"
        web.mkdir(parents=True)
        (web / "package.json").write_text('{"private":true}\n', encoding="utf-8")
        (web / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
        (web / "focused.test.js").write_text("// focused test\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "fixture")
        self._git("worktree", "add", "-b", "candidate", str(self.candidate), "HEAD")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.canonical,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def _prepare_dependencies(self):
        modules = self.candidate / "apps" / "web" / "node_modules"
        for package in ("vitest", "eslint"):
            package_dir = modules / package
            package_dir.mkdir(parents=True)
            (package_dir / "package.json").write_text(
                json.dumps({"name": package, "main": "index.js"}), encoding="utf-8"
            )
            (package_dir / "index.js").write_text("module.exports = {}\n", encoding="utf-8")
        plugin = modules / "@vitejs" / "plugin-react"
        plugin.mkdir(parents=True)
        (plugin / "package.json").write_text(
            json.dumps({"name": "@vitejs/plugin-react", "main": "index.js"}),
            encoding="utf-8",
        )
        (plugin / "index.js").write_text("module.exports = {}\n", encoding="utf-8")

        bin_dir = modules / ".bin"
        bin_dir.mkdir()
        vitest_bin = bin_dir / "vitest"
        vitest_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        vitest_bin.chmod(0o755)

    def _run(self, worktree=None, ref="HEAD", verify_marker=None):
        command = [
            sys.executable,
            str(PREFLIGHT),
            "--worktree",
            str(worktree or self.candidate),
            "--ref",
            ref,
            "--marker-dir",
            str(self.marker_dir),
            "--timeout-seconds",
            "5",
        ]
        if verify_marker is not None:
            command.extend(["--verify-marker", str(verify_marker)])
        else:
            command.extend(["--test-target", "focused.test.js"])
        return subprocess.run(command, text=True, capture_output=True)

    def test_prepared_candidate_passes_and_marker_verifies(self):
        self._prepare_dependencies()
        canonical_before = self._git("status", "--porcelain", cwd=self.canonical)
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ready")
        marker = pathlib.Path(payload["marker"])
        self.assertTrue(marker.is_file())
        self.assertFalse(marker.is_relative_to(self.candidate))

        verified = self._run(verify_marker=marker)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["status"], "verified")
        self.assertEqual(
            canonical_before, self._git("status", "--porcelain", cwd=self.canonical)
        )

    def test_dependency_free_candidate_fails_before_probe(self):
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dependency", result.stderr.lower())
        self.assertFalse(self.marker_dir.exists())

    def test_marker_is_rejected_after_source_head_change(self):
        self._prepare_dependencies()
        certified = self._run()
        self.assertEqual(certified.returncode, 0, certified.stderr)
        marker = pathlib.Path(json.loads(certified.stdout)["marker"])

        source = self.candidate / "apps" / "web" / "focused.test.js"
        source.write_text("// changed focused test\n", encoding="utf-8")
        self._git("add", "apps/web/focused.test.js", cwd=self.candidate)
        self._git("commit", "-m", "change source", cwd=self.candidate)

        rejected = self._run(verify_marker=marker)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("stale marker", rejected.stderr.lower())

    def test_marker_is_rejected_after_uncommitted_lockfile_change(self):
        self._prepare_dependencies()
        certified = self._run()
        self.assertEqual(certified.returncode, 0, certified.stderr)
        marker = pathlib.Path(json.loads(certified.stdout)["marker"])

        lockfile = self.candidate / "apps" / "web" / "package-lock.json"
        lockfile.write_text('{"lockfileVersion":3,"changed":true}\n', encoding="utf-8")

        rejected = self._run(verify_marker=marker)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("stale marker", rejected.stderr.lower())

    def test_canonical_checkout_is_rejected_without_running_probe(self):
        self._prepare_dependencies()
        before = self._git("status", "--porcelain", cwd=self.canonical)
        result = self._run(worktree=self.canonical)
        after = self._git("status", "--porcelain", cwd=self.canonical)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical checkout", result.stderr.lower())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
