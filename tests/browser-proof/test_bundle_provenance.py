import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from bundle_provenance import (
    MANIFEST_NAME,
    assert_build_inputs_clean,
    certify_build,
    validate_manifest,
    write_manifest,
)


COMMIT = "a" * 40


class BundleProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dist = pathlib.Path(self.temp_dir.name) / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<html></html>", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_matching_manifest_is_accepted(self):
        write_manifest(self.dist, COMMIT)
        validate_manifest(self.dist, COMMIT)

    def test_missing_manifest_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "missing or malformed"):
            validate_manifest(self.dist, COMMIT)

    def test_malformed_manifest_is_rejected(self):
        (self.dist / MANIFEST_NAME).write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "missing or malformed"):
            validate_manifest(self.dist, COMMIT)

    def test_mismatched_manifest_is_rejected(self):
        write_manifest(self.dist, COMMIT)
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            validate_manifest(self.dist, "b" * 40)

    def test_unexpected_manifest_fields_are_rejected(self):
        self._write_manifest(label="green")
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_manifest(self.dist, COMMIT)

    def test_boolean_version_is_rejected(self):
        self._write_manifest(version=True)
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_manifest(self.dist, COMMIT)

    def test_non_integer_versions_are_rejected(self):
        for version in ("1", 1.0, None, 0, -1):
            with self.subTest(version=version):
                self._write_manifest(version=version)
                with self.assertRaisesRegex(RuntimeError, "malformed"):
                    validate_manifest(self.dist, COMMIT)

    def test_malformed_manifest_commit_is_rejected(self):
        self._write_manifest(commit="not-a-sha")
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_manifest(self.dist, COMMIT)

    def test_malformed_expected_commit_is_rejected(self):
        write_manifest(self.dist, COMMIT)
        for commit in (True, "A" * 40, "a" * 39):
            with self.subTest(commit=commit):
                with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed$"):
                    validate_manifest(self.dist, commit)

    def test_malformed_expected_commit_error_does_not_leak_input(self):
        write_manifest(self.dist, COMMIT)
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed$"):
            validate_manifest(self.dist, "caller-secret")

    def test_invalid_dist_path_is_rejected(self):
        self._write_manifest(dist=str(self.dist / "other"))
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_manifest(self.dist, COMMIT)

    def test_manifest_dist_must_be_the_exact_canonical_absolute_path(self):
        alias = pathlib.Path(self.temp_dir.name) / "dist-alias"
        alias.symlink_to(self.dist, target_is_directory=True)
        variants = (
            f"{self.dist}/.",
            str(self.dist) + "//",
            os.path.relpath(self.dist),
            str(alias),
            str(self.dist).replace("/", "\\"),
            str(self.dist.parent / "missing"),
        )
        for manifest_dist in variants:
            with self.subTest(manifest_dist=manifest_dist):
                self._write_manifest(dist=manifest_dist)
                with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed$"):
                    validate_manifest(self.dist, COMMIT)

    def test_symlinked_manifest_target_is_rejected(self):
        alias = pathlib.Path(self.temp_dir.name) / "dist-alias"
        alias.symlink_to(self.dist, target_is_directory=True)
        self._write_manifest(dist=str(alias))
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed$"):
            validate_manifest(self.dist, COMMIT)

    def test_embedded_nul_manifest_dist_is_rejected_as_malformed(self):
        self._write_manifest(dist="\0")
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed"):
            validate_manifest(self.dist, COMMIT)

    def test_path_resolution_failures_are_bounded_and_redacted(self):
        loop = pathlib.Path(self.temp_dir.name) / "loop"
        loop.symlink_to(loop)
        write_manifest(self.dist, COMMIT)
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed$"):
            validate_manifest(loop, COMMIT)

    def test_expected_dist_type_coercions_are_rejected_as_malformed(self):
        write_manifest(self.dist, COMMIT)
        for invalid_dist in (
            None,
            True,
            7,
            b"dist",
            "\0",
            self.dist / "missing",
            object(),
        ):
            with self.subTest(invalid_dist=type(invalid_dist).__name__):
                with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed$"):
                    validate_manifest(invalid_dist, COMMIT)

    def test_mismatch_error_does_not_leak_commits(self):
        write_manifest(self.dist, COMMIT)
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance mismatch$"):
            validate_manifest(self.dist, "b" * 40)

    def test_clean_build_inputs_allow_unrelated_untracked_proof_artifact(self):
        repo = self._make_repo()
        artifact = repo / "tests/browser-proof/generated-proof.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("generated", encoding="utf-8")
        assert_build_inputs_clean(repo)

    def test_dirty_tracked_build_source_is_rejected(self):
        repo = self._make_repo()
        source = repo / "apps/web/src/App.tsx"
        source.write_text("export default 'dirty'\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", str(source.relative_to(repo))], check=True)
        with self.assertRaisesRegex(RuntimeError, "tracked build input is dirty"):
            assert_build_inputs_clean(repo)

    def test_post_check_dirty_build_source_is_denied_before_manifest_write(self):
        repo = self._make_repo()
        assert_build_inputs_clean(repo)
        (repo / "apps/web/src/App.tsx").write_text(
            "export default 'mutated after build'\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "tracked build input is dirty"):
            certify_build(repo, self.dist, COMMIT)
        self.assertFalse((self.dist / MANIFEST_NAME).exists())

    def test_vite_environment_override_is_rejected(self):
        repo = self._make_repo()
        with mock.patch.dict(os.environ, {"VITE_USE_LOCAL_API": "true"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "Vite environment overrides"):
                assert_build_inputs_clean(repo)

    def test_vite_environment_file_is_rejected(self):
        repo = self._make_repo()
        (repo / "apps/web/.env.production.local").write_text(
            "VITE_USE_LOCAL_API=true\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "Vite environment files"):
            assert_build_inputs_clean(repo)

    def _write_manifest(self, **overrides):
        payload = {"version": 1, "commit": COMMIT, "dist": str(self.dist)}
        payload.update(overrides)
        (self.dist / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")

    def _make_repo(self):
        repo = pathlib.Path(self.temp_dir.name) / "repo"
        source = repo / "apps/web/src/App.tsx"
        source.parent.mkdir(parents=True)
        source.write_text("export default 'clean'\n", encoding="utf-8")
        (repo / "apps/web/index.html").write_text("<div id=\"root\"></div>\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "apps/web"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
        return repo


if __name__ == "__main__":
    unittest.main()
