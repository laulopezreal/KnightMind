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
    load_certified_bundle,
    validate_manifest,
    write_manifest,
)

COMMIT = "a" * 40
ROOT_BUILD_INPUT_FILES = (
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


class _MutatingScandir:
    def __init__(self, iterator, mutate):
        self.iterator = iterator
        self.mutate = mutate

    def __enter__(self):
        self.iterator.__enter__()
        return self

    def __iter__(self):
        return iter(self.iterator)

    def __exit__(self, exc_type, exc_value, traceback):
        result = self.iterator.__exit__(exc_type, exc_value, traceback)
        if exc_type is None:
            self.mutate()
        return result


class BundleProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dist = pathlib.Path(self.temp_dir.name) / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<html></html>", encoding="utf-8")
        assets = self.dist / "assets"
        assets.mkdir()
        (assets / "app.js").write_text("console.log('clean')\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_matching_manifest_is_accepted(self):
        write_manifest(self.dist, COMMIT)
        validate_manifest(str(self.dist), COMMIT)

    def test_caller_dist_must_be_the_exact_canonical_absolute_path_string(self):
        write_manifest(self.dist, COMMIT)
        alias = pathlib.Path(self.temp_dir.name) / "dist-alias"
        alias.symlink_to(self.dist, target_is_directory=True)
        variants = (
            self.dist,
            f"{self.dist}/.",
            str(self.dist) + "//",
            os.path.relpath(self.dist),
            str(alias),
        )
        for caller_dist in variants:
            with self.subTest(caller_dist=str(caller_dist)):
                with self.assertRaisesRegex(
                    RuntimeError, r"^bundle provenance malformed$"
                ):
                    validate_manifest(caller_dist, COMMIT)

    def test_missing_manifest_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "missing or malformed"):
            validate_manifest(str(self.dist), COMMIT)

    def test_malformed_manifest_is_rejected(self):
        (self.dist / MANIFEST_NAME).write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "missing or malformed"):
            validate_manifest(str(self.dist), COMMIT)

    def test_manifest_inventory_is_deterministic_and_content_bound(self):
        write_manifest(self.dist, COMMIT)
        payload = json.loads((self.dist / MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 2)
        self.assertEqual(
            [item["path"] for item in payload["inventory"]],
            ["assets/app.js", "index.html"],
        )
        self.assertTrue(all(len(item["sha256"]) == 64 for item in payload["inventory"]))

    def test_changed_bundle_files_are_rejected(self):
        for relative_path in ("index.html", "assets/app.js"):
            with self.subTest(relative_path=relative_path):
                write_manifest(self.dist, COMMIT)
                target = self.dist / relative_path
                original = target.read_text(encoding="utf-8")
                target.write_text(original + "mutated", encoding="utf-8")
                with self.assertRaisesRegex(
                    RuntimeError, r"^bundle provenance mismatch$"
                ):
                    validate_manifest(str(self.dist), COMMIT)
                target.write_text(original, encoding="utf-8")

    def test_loaded_bundle_is_immutable_and_detached_from_later_replacements(self):
        write_manifest(self.dist, COMMIT)
        served = load_certified_bundle(str(self.dist), COMMIT)
        certified_bytes = served["assets/app.js"]

        self._replace_file(self.dist / "assets/app.js", b"console.log('replaced')\n")

        self.assertEqual(served["assets/app.js"], certified_bytes)
        with self.assertRaises(TypeError):
            served["assets/app.js"] = b"mutated"

    def test_replacement_after_validation_cannot_enter_loaded_bundle(self):
        write_manifest(self.dist, COMMIT)
        original_inventory = __import__("bundle_provenance")._bundle_inventory
        mutated = False

        def mutate_after_inventory(path):
            nonlocal mutated
            inventory = original_inventory(path)
            if not mutated:
                mutated = True
                self._replace_file(
                    self.dist / "assets/app.js", b"console.log('post-validation')\n"
                )
            return inventory

        with mock.patch(
            "bundle_provenance._bundle_inventory", side_effect=mutate_after_inventory
        ):
            with self.assertRaisesRegex(RuntimeError, r"^bundle provenance mismatch$"):
                load_certified_bundle(str(self.dist), COMMIT)
        self.assertTrue(mutated)

    def test_added_bundle_file_is_rejected(self):
        write_manifest(self.dist, COMMIT)
        (self.dist / "assets/added.js").write_text("added\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance mismatch$"):
            validate_manifest(str(self.dist), COMMIT)

    def test_root_addition_after_scandir_enumeration_is_rejected(self):
        write_manifest(self.dist, COMMIT)
        injected = self.dist / "injected.js"

        self._assert_scan_mutation_rejected(
            self.dist,
            lambda: injected.write_text("injected\n", encoding="utf-8"),
        )

    def test_root_and_nested_directory_entry_mutations_are_rejected(self):
        cases = (
            (
                "root directory addition",
                self.dist,
                lambda: (self.dist / "injected").mkdir(),
            ),
            (
                "nested file addition",
                self.dist / "assets",
                lambda: (self.dist / "assets/injected.js").write_text(
                    "injected\n", encoding="utf-8"
                ),
            ),
            (
                "root file replacement",
                self.dist,
                lambda: self._replace_file(self.dist / "index.html"),
            ),
            (
                "nested file replacement",
                self.dist / "assets",
                lambda: self._replace_file(self.dist / "assets/app.js"),
            ),
        )
        for label, directory, mutate in cases:
            with self.subTest(label=label):
                write_manifest(self.dist, COMMIT)
                self._assert_scan_mutation_rejected(directory, mutate)
                injected = directory / (
                    "injected" if directory == self.dist else "injected.js"
                )
                if injected.is_dir():
                    injected.rmdir()
                elif injected.exists():
                    injected.unlink()

    def test_deleted_bundle_file_is_rejected(self):
        write_manifest(self.dist, COMMIT)
        (self.dist / "assets/app.js").unlink()
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance mismatch$"):
            validate_manifest(str(self.dist), COMMIT)

    def test_symlinked_bundle_entry_is_rejected(self):
        write_manifest(self.dist, COMMIT)
        target = self.dist / "assets/app.js"
        target.unlink()
        target.symlink_to(self.dist / "index.html")
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance mismatch$"):
            validate_manifest(str(self.dist), COMMIT)

    def test_nonregular_bundle_entry_is_rejected(self):
        write_manifest(self.dist, COMMIT)
        os.mkfifo(self.dist / "assets/pipe")
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance mismatch$"):
            validate_manifest(str(self.dist), COMMIT)

    def test_unreadable_bundle_entry_is_rejected_without_disclosure(self):
        write_manifest(self.dist, COMMIT)
        original_open = os.open

        def deny_app(path, flags):
            if pathlib.Path(path).name == "app.js":
                raise PermissionError("sensitive-host-path")
            return original_open(path, flags)

        with mock.patch("bundle_provenance.os.open", side_effect=deny_app):
            with self.assertRaisesRegex(RuntimeError, r"^bundle provenance mismatch$"):
                validate_manifest(str(self.dist), COMMIT)

    def test_malformed_inventory_or_digest_is_rejected(self):
        malformed_values = (
            None,
            {},
            [],
            [{"path": "index.html", "sha256": "not-a-digest"}],
            [{"path": "index.html", "sha256": "A" * 64}],
            [{"path": "index.html", "sha256": "a" * 64, "extra": True}],
            [
                {"path": "index.html", "sha256": "a" * 64},
                {"path": "index.html", "sha256": "b" * 64},
            ],
        )
        for inventory in malformed_values:
            with self.subTest(inventory=inventory):
                self._write_manifest(inventory=inventory)
                with self.assertRaisesRegex(
                    RuntimeError, r"^bundle provenance malformed$"
                ):
                    validate_manifest(str(self.dist), COMMIT)

    def test_unsafe_inventory_paths_are_rejected_without_disclosure(self):
        unsafe_paths = (
            "../index.html",
            "/index.html",
            "assets/../index.html",
            "assets\\app.js",
            "./index.html",
            "assets/secret\n.js",
            MANIFEST_NAME,
            "",
        )
        for unsafe_path in unsafe_paths:
            with self.subTest(unsafe_path=repr(unsafe_path)):
                self._write_manifest(
                    inventory=[{"path": unsafe_path, "sha256": "a" * 64}]
                )
                with self.assertRaisesRegex(
                    RuntimeError, r"^bundle provenance malformed$"
                ):
                    validate_manifest(str(self.dist), COMMIT)

    def test_manifest_writer_refuses_symlinked_target(self):
        target = self.dist / MANIFEST_NAME
        outside = pathlib.Path(self.temp_dir.name) / "outside.json"
        outside.write_text("untouched", encoding="utf-8")
        target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            write_manifest(self.dist, COMMIT)
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")

    def test_mismatched_manifest_is_rejected(self):
        write_manifest(self.dist, COMMIT)
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            validate_manifest(str(self.dist), "b" * 40)

    def test_unexpected_manifest_fields_are_rejected(self):
        self._write_manifest(label="green")
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_manifest(str(self.dist), COMMIT)

    def test_boolean_version_is_rejected(self):
        self._write_manifest(version=True)
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_manifest(str(self.dist), COMMIT)

    def test_non_integer_versions_are_rejected(self):
        for version in ("1", 1.0, None, 0, -1):
            with self.subTest(version=version):
                self._write_manifest(version=version)
                with self.assertRaisesRegex(RuntimeError, "malformed"):
                    validate_manifest(str(self.dist), COMMIT)

    def test_malformed_manifest_commit_is_rejected(self):
        self._write_manifest(commit="not-a-sha")
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_manifest(str(self.dist), COMMIT)

    def test_malformed_expected_commit_is_rejected(self):
        write_manifest(self.dist, COMMIT)
        for commit in (True, "A" * 40, "a" * 39):
            with self.subTest(commit=commit):
                with self.assertRaisesRegex(
                    RuntimeError, r"^bundle provenance malformed$"
                ):
                    validate_manifest(str(self.dist), commit)

    def test_malformed_expected_commit_error_does_not_leak_input(self):
        write_manifest(self.dist, COMMIT)
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed$"):
            validate_manifest(str(self.dist), "caller-secret")

    def test_invalid_dist_path_is_rejected(self):
        self._write_manifest(dist=str(self.dist / "other"))
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_manifest(str(self.dist), COMMIT)

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
                with self.assertRaisesRegex(
                    RuntimeError, r"^bundle provenance malformed$"
                ):
                    validate_manifest(str(self.dist), COMMIT)

    def test_symlinked_manifest_target_is_rejected(self):
        manifest_target = self.dist / MANIFEST_NAME
        linked_manifest = pathlib.Path(self.temp_dir.name) / "linked-manifest.json"
        linked_manifest.write_text(
            json.dumps(
                {
                    "version": 2,
                    "commit": COMMIT,
                    "dist": str(self.dist),
                    "inventory": [],
                }
            ),
            encoding="utf-8",
        )
        manifest_target.symlink_to(linked_manifest)
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed$"):
            validate_manifest(str(self.dist), COMMIT)

    def test_nonregular_manifest_target_is_rejected_without_blocking(self):
        (self.dist / MANIFEST_NAME).mkdir()
        with self.assertRaisesRegex(
            RuntimeError, r"^bundle provenance missing or malformed$"
        ):
            validate_manifest(str(self.dist), COMMIT)

    def test_embedded_nul_manifest_dist_is_rejected_as_malformed(self):
        self._write_manifest(dist="\0")
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance malformed"):
            validate_manifest(str(self.dist), COMMIT)

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
                with self.assertRaisesRegex(
                    RuntimeError, r"^bundle provenance malformed$"
                ):
                    validate_manifest(invalid_dist, COMMIT)

    def test_mismatch_error_does_not_leak_commits(self):
        write_manifest(self.dist, COMMIT)
        with self.assertRaisesRegex(RuntimeError, r"^bundle provenance mismatch$"):
            validate_manifest(str(self.dist), "b" * 40)

    def test_clean_build_inputs_allow_unrelated_untracked_proof_artifact(self):
        repo = self._make_repo()
        artifact = repo / "tests/browser-proof/generated-proof.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("generated", encoding="utf-8")
        assert_build_inputs_clean(repo)

    def test_untracked_build_inputs_are_rejected(self):
        repo = self._make_repo()
        cases = (
            "apps/web/public/injected.txt",
            "apps/web/src/injected.ts",
            *(f"apps/web/{name}" for name in ROOT_BUILD_INPUT_FILES),
        )
        for relative_path in cases:
            with self.subTest(relative_path=relative_path):
                target = repo / relative_path
                if target.exists():
                    target.unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("untracked build input\n", encoding="utf-8")
                try:
                    with self.assertRaises(RuntimeError):
                        assert_build_inputs_clean(repo)
                finally:
                    target.unlink(missing_ok=True)

    def test_ignored_build_inputs_are_rejected(self):
        repo = self._make_repo()
        cases = (
            "apps/web/src/ignored.ts",
            "apps/web/public/ignored.txt",
            "apps/web/.env.production.local",
            "apps/web/.npmrc",
            "apps/web/postcss.config.js",
            "apps/web/tailwind.config.js",
            "apps/web/tsconfig.node.json",
            "apps/web/vite.config.mts",
            "apps/web/package-lock.json",
        )
        exclude = repo / ".git/info/exclude"
        exclude.write_text("\n".join(cases) + "\n", encoding="utf-8")
        for relative_path in cases:
            with self.subTest(relative_path=relative_path):
                target = repo / relative_path
                if target.exists():
                    target.unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("ignored build input\n", encoding="utf-8")
                ignored = subprocess.run(
                    ["git", "-C", str(repo), "check-ignore", "-q", relative_path],
                    check=False,
                )
                try:
                    self.assertEqual(ignored.returncode, 0)
                    with self.assertRaises(RuntimeError):
                        assert_build_inputs_clean(repo)
                finally:
                    target.unlink(missing_ok=True)

    def test_dirty_tracked_build_source_is_rejected(self):
        repo = self._make_repo()
        source = repo / "apps/web/src/App.tsx"
        source.write_text("export default 'dirty'\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", str(source.relative_to(repo))], check=True
        )
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
        write_manifest(self.dist, COMMIT)
        payload = json.loads((self.dist / MANIFEST_NAME).read_text(encoding="utf-8"))
        payload.update(overrides)
        (self.dist / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")

    def _assert_scan_mutation_rejected(self, directory, mutate):
        original_scandir = os.scandir
        mutated = False

        def racing_scandir(path):
            nonlocal mutated
            iterator = original_scandir(path)
            if not mutated and pathlib.Path(path) == directory:
                mutated = True
                return _MutatingScandir(iterator, mutate)
            return iterator

        with mock.patch("bundle_provenance.os.scandir", side_effect=racing_scandir):
            with self.assertRaisesRegex(RuntimeError, r"^bundle provenance mismatch$"):
                validate_manifest(str(self.dist), COMMIT)
        self.assertTrue(mutated)

    @staticmethod
    def _replace_file(path, contents=None):
        if contents is None:
            contents = path.read_bytes()
        path.unlink()
        path.write_bytes(contents)

    def _make_repo(self):
        repo = pathlib.Path(self.temp_dir.name) / "repo"
        source = repo / "apps/web/src/App.tsx"
        source.parent.mkdir(parents=True)
        source.write_text("export default 'clean'\n", encoding="utf-8")
        (repo / "apps/web/index.html").write_text(
            '<div id="root"></div>\n', encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"], check=True
        )
        subprocess.run(["git", "-C", str(repo), "add", "apps/web"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
        return repo


if __name__ == "__main__":
    unittest.main()
