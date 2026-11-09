#!/usr/bin/env python3
"""Tests for infer-frontend-params.py."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import infer_frontend_params as infer


class _FrontendParamsTestBase(unittest.TestCase):
    """Shared helpers for writing package.json and turbo.json to a temp dir."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_package_json(self, content):
        path = os.path.join(self.tmpdir, "package.json")
        with open(path, "w") as f:
            json.dump(content, f)
        return path

    def _write_turbo_json(self, content=None):
        path = os.path.join(self.tmpdir, "turbo.json")
        with open(path, "w") as f:
            json.dump(content or {"tasks": {"build": {}}}, f)
        return path


class TestIsMonorepo(_FrontendParamsTestBase):
    def test_workspaces_array(self):
        self.assertTrue(infer.is_monorepo({"workspaces": ["packages/*"]}))

    def test_workspaces_object(self):
        self.assertTrue(infer.is_monorepo({"workspaces": {"packages": ["packages/*"]}}))

    def test_no_workspaces(self):
        self.assertFalse(infer.is_monorepo({"name": "single-app"}))

    def test_empty_workspaces_array(self):
        self.assertFalse(infer.is_monorepo({"workspaces": []}))

    def test_empty_workspaces_object(self):
        self.assertFalse(infer.is_monorepo({"workspaces": {"packages": []}}))


class TestWorkspaceArtifactPaths(_FrontendParamsTestBase):
    def test_simple_glob(self):
        pkg = {"workspaces": ["packages/*"]}
        self.assertEqual(infer.workspace_artifact_paths(pkg), "packages/*/dist")

    def test_multiple_globs(self):
        pkg = {
            "workspaces": [
                "packages/apps/*",
                "packages/framework/*",
                "packages/shell/*",
            ]
        }
        self.assertEqual(
            infer.workspace_artifact_paths(pkg),
            "packages/apps/*/dist\npackages/framework/*/dist\npackages/shell/*/dist",
        )

    def test_object_format(self):
        pkg = {"workspaces": {"packages": ["packages/*"]}}
        self.assertEqual(infer.workspace_artifact_paths(pkg), "packages/*/dist")


class TestMonorepoWithCiScripts(_FrontendParamsTestBase):
    def test_prefers_ci_prepublish(self):
        """Patient-chart pattern: ci:prepublish script exists."""
        path = self._write_package_json(
            {
                "name": "@openmrs/esm-patient-chart",
                "workspaces": ["packages/*"],
                "scripts": {
                    "ci:prepublish": "yarn workspaces foreach ... --tag next",
                    "ci:prepublish-patch": "yarn workspaces foreach ... --tag patch",
                    "ci:publish": "yarn workspaces foreach ... --tag latest",
                },
            }
        )
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(
            result["pre_release_publish_command"], "yarn run ci:prepublish"
        )
        self.assertEqual(
            result["pre_release_patch_publish_command"],
            "yarn run ci:prepublish-patch",
        )
        self.assertEqual(result["release_publish_command"], "yarn run ci:publish")

    def test_prefers_ci_publish_next(self):
        """Core pattern: ci:publish-next script exists."""
        path = self._write_package_json(
            {
                "name": "@openmrs/esm-core",
                "workspaces": ["packages/apps/*", "packages/framework/*"],
                "scripts": {
                    "ci:publish-next": "yarn workspaces foreach ... --tag next",
                    "ci:publish": "yarn workspaces foreach ... --tag latest",
                },
            }
        )
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(
            result["pre_release_publish_command"], "yarn run ci:publish-next"
        )
        self.assertNotIn("pre_release_patch_publish_command", result)
        self.assertEqual(result["release_publish_command"], "yarn run ci:publish")

    def test_ci_prepublish_takes_precedence_over_ci_publish_next(self):
        """If both exist, ci:prepublish wins."""
        path = self._write_package_json(
            {
                "name": "@openmrs/test-mono",
                "workspaces": ["packages/*"],
                "scripts": {
                    "ci:prepublish": "custom prepublish",
                    "ci:publish-next": "custom publish-next",
                    "ci:publish": "custom publish",
                },
            }
        )
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(
            result["pre_release_publish_command"], "yarn run ci:prepublish"
        )


class TestMonorepoWithoutCiScripts(_FrontendParamsTestBase):
    def test_generates_workspaces_foreach_commands(self):
        path = self._write_package_json(
            {
                "name": "@openmrs/esm-new-mono",
                "workspaces": ["packages/*"],
                "scripts": {"build": "turbo run build"},
            }
        )
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)

        self.assertIn(
            "--exclude @openmrs/esm-new-mono", result["pre_release_version_command"]
        )
        self.assertIn(
            'version "$PRERELEASE_VERSION"', result["pre_release_version_command"]
        )

        self.assertIn(
            "--exclude @openmrs/esm-new-mono", result["pre_release_publish_command"]
        )
        self.assertIn("--tag next", result["pre_release_publish_command"])
        self.assertIn("--no-private", result["pre_release_publish_command"])
        self.assertIn("--no-private", result["release_publish_command"])
        self.assertNotIn("--no-private", result["pre_release_version_command"])

        self.assertNotIn("pre_release_patch_publish_command", result)

        self.assertIn(
            "--exclude @openmrs/esm-new-mono", result["release_publish_command"]
        )
        self.assertIn(
            f'exec bash "{infer.SCRIPTS_DIR}/publish-package.sh" --access public',
            result["release_publish_command"],
        )
        self.assertNotIn("--tag", result["release_publish_command"])
        self.assertIn("--tolerate-republish", result["release_publish_command"])

        self.assertEqual(
            result["release_version_command"],
            'yarn workspaces foreach --all --topological version "$RELEASE_VERSION"',
        )


class TestMonorepoCommonOutputs(_FrontendParamsTestBase):
    def test_common_monorepo_outputs(self):
        path = self._write_package_json(
            {
                "name": "@openmrs/esm-test",
                "workspaces": ["packages/*"],
                "scripts": {},
            }
        )
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(result["is_monorepo"], "true")
        self.assertEqual(result["verify_command"], "yarn verify --concurrency=5")
        self.assertEqual(result["enable_turborepo_cache"], "true")
        self.assertEqual(result["artifact_path"], "packages/*/dist")

    def test_monorepo_multiple_workspace_artifact_paths(self):
        path = self._write_package_json(
            {
                "name": "@openmrs/esm-core",
                "workspaces": [
                    "packages/apps/*",
                    "packages/framework/*",
                    "packages/shell/*",
                    "packages/tooling/*",
                ],
                "scripts": {"ci:publish": "..."},
            }
        )
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)
        expected_paths = (
            "packages/apps/*/dist\n"
            "packages/framework/*/dist\n"
            "packages/shell/*/dist\n"
            "packages/tooling/*/dist"
        )
        self.assertEqual(result["artifact_path"], expected_paths)


class TestSingleApp(_FrontendParamsTestBase):
    def test_single_app_minimal_output(self):
        path = self._write_package_json(
            {
                "name": "@openmrs/esm-dispensing-app",
                "scripts": {"build": "webpack", "verify": "turbo run lint test"},
            }
        )
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(result["is_monorepo"], "false")
        self.assertEqual(result["enable_turborepo_cache"], "true")
        self.assertNotIn("artifact_path", result)
        self.assertNotIn("pre_release_version_command", result)
        self.assertNotIn("release_version_command", result)
        self.assertNotIn("verify_command", result)
        publish = f'bash "{infer.SCRIPTS_DIR}/publish-package.sh" --access public'
        self.assertEqual(
            result["pre_release_publish_command"], f"{publish} --tag next --tolerate-republish"
        )
        self.assertEqual(result["release_publish_command"], f"{publish} --tolerate-republish")

    def test_single_app_uses_ci_publish_scripts(self):
        path = self._write_package_json(
            {
                "name": "@acme/esm-app",
                "scripts": {"ci:prepublish": "...", "ci:publish": "..."},
            }
        )
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(result["pre_release_publish_command"], "yarn run ci:prepublish")
        self.assertEqual(result["release_publish_command"], "yarn run ci:publish")

    def test_single_app_turbo_build_command(self):
        path = self._write_package_json({"name": "test-app"})
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(result["build_command"], "yarn turbo run build --color --concurrency=5")


class TestConcurrency(_FrontendParamsTestBase):
    def _infer(self, verify=None, concurrency=None):
        scripts = {} if verify is None else {"verify": verify}
        path = self._write_package_json(
            {"name": "@o/mono", "workspaces": ["packages/*"], "scripts": scripts}
        )
        self._write_turbo_json()
        return infer.infer_params(path, self.tmpdir, concurrency=concurrency)

    def test_appends_to_a_turbo_verify(self):
        self.assertEqual(
            self._infer("turbo run lint test")["verify_command"], "yarn verify --concurrency=5"
        )

    def test_keeps_a_verify_that_sets_its_own(self):
        result = self._infer("turbo run lint test --concurrency=1")
        self.assertEqual(result["verify_command"], "yarn verify")

    def test_skips_a_verify_ending_in_another_command(self):
        result = self._infer("turbo run lint && jest")
        self.assertEqual(result["verify_command"], "yarn verify")

    def test_appends_to_a_chain_ending_in_turbo(self):
        result = self._infer("yarn lint && turbo run test")
        self.assertEqual(result["verify_command"], "yarn verify --concurrency=5")

    def test_uses_the_given_concurrency(self):
        result = self._infer("turbo run test", concurrency="2")
        self.assertEqual(result["verify_command"], "yarn verify --concurrency=2")
        self.assertEqual(result["build_command"], "yarn turbo run build --color --concurrency=2")

    def test_empty_concurrency_omits_the_flag(self):
        result = self._infer("turbo run test", concurrency="")
        self.assertEqual(result["verify_command"], "yarn verify")
        self.assertEqual(result["build_command"], "yarn turbo run build --color")


class TestVersionScripts(_FrontendParamsTestBase):
    def _infer(self, scripts, monorepo=True):
        pkg = {"name": "@o/root", "scripts": scripts}
        if monorepo:
            pkg["workspaces"] = ["packages/*"]
        return infer.infer_params(self._write_package_json(pkg), self.tmpdir)

    def test_ci_version_sets_both_version_commands(self):
        for monorepo in (True, False):
            result = self._infer({"ci:version": "lerna version"}, monorepo)
            self.assertEqual(
                result["pre_release_version_command"], 'yarn run ci:version "$PRERELEASE_VERSION"'
            )
            self.assertEqual(
                result["release_version_command"], 'yarn run ci:version "$RELEASE_VERSION"'
            )

    def test_ci_current_version_reads_the_version(self):
        result = self._infer({"ci:current-version": "node -p ..."})
        self.assertEqual(result["current_version_command"], "yarn run --silent ci:current-version")

    def test_no_current_version_command_without_the_script(self):
        self.assertNotIn("current_version_command", self._infer({}))


class TestNoTurboJson(_FrontendParamsTestBase):
    def test_no_turbo_sets_build_command(self):
        path = self._write_package_json(
            {"name": "test-app", "scripts": {"build": "webpack"}}
        )
        # No turbo.json written
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(result["build_command"], "yarn build")

    def test_monorepo_no_turbo_sets_build_command(self):
        path = self._write_package_json(
            {
                "name": "@openmrs/test-mono",
                "workspaces": ["packages/*"],
                "scripts": {"ci:publish": "..."},
            }
        )
        # No turbo.json written
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(result["build_command"], "yarn build")
        # Should still set monorepo outputs
        self.assertEqual(result["is_monorepo"], "true")


class TestEdgeCases(_FrontendParamsTestBase):
    def test_missing_package_json(self):
        result = infer.infer_params("/nonexistent/package.json")
        self.assertEqual(result, {})

    def test_invalid_json(self):
        path = os.path.join(self.tmpdir, "package.json")
        with open(path, "w") as f:
            f.write("not json")
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(result, {})

    def test_monorepo_missing_name(self):
        """Monorepo without a name field should still work (no --exclude flag)."""
        path = self._write_package_json({"workspaces": ["packages/*"], "scripts": {}})
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(result["is_monorepo"], "true")
        self.assertNotIn("--exclude", result["pre_release_version_command"])

    def test_workspaces_as_object_format(self):
        path = self._write_package_json(
            {
                "name": "@openmrs/test",
                "workspaces": {"packages": ["packages/*"]},
                "scripts": {},
            }
        )
        self._write_turbo_json()
        result = infer.infer_params(path, self.tmpdir)
        self.assertEqual(result["is_monorepo"], "true")
        self.assertEqual(result["artifact_path"], "packages/*/dist")


if __name__ == "__main__":
    unittest.main()
