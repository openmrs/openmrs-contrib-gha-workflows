#!/usr/bin/env python3
"""Tests for the npm publish helpers: registry resolution, the publish target
check, publish-package.sh and verify-npm-credentials.sh."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import check_npm_publish_target as check
import utils

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
NPMJS = "https://registry.npmjs.org"


class TestPublishRegistry(unittest.TestCase):
    CONFIG = {
        "npmPublishRegistry": None,
        "npmRegistryServer": "https://registry.yarnpkg.com",
        "npmScopes": {
            "x": {"npmPublishRegistry": "https://x.example.org/", "npmRegistryServer": "https://registry.yarnpkg.com"},
            "y": {"npmPublishRegistry": None, "npmRegistryServer": "https://y.example.org"},
        },
    }

    def test_publish_config_wins(self):
        manifest = {"name": "@x/a", "publishConfig": {"registry": "https://p.example.org/"}}
        self.assertEqual(utils.publish_registry(manifest, self.CONFIG), "https://p.example.org")

    def test_scope_publish_registry(self):
        self.assertEqual(utils.publish_registry({"name": "@x/a"}, self.CONFIG), "https://x.example.org")

    def test_scope_without_publish_registry_falls_back_to_default(self):
        # Yarn ignores a scope's npmRegistryServer when publishing.
        self.assertEqual(utils.publish_registry({"name": "@y/a"}, self.CONFIG), "https://registry.yarnpkg.com")

    def test_top_level_publish_registry(self):
        config = {**self.CONFIG, "npmPublishRegistry": "https://pub.example.org"}
        self.assertEqual(utils.publish_registry({"name": "plain"}, config), "https://pub.example.org")

    def test_defaults_to_npmjs_without_config(self):
        self.assertEqual(utils.publish_registry({"name": "plain"}, {}), NPMJS)


class TestYarnRegistryConfig(unittest.TestCase):
    def test_parses_json_values(self):
        values = {
            "npmPublishRegistry": "null",
            "npmRegistryServer": '"https://registry.yarnpkg.com"',
            "npmScopes": '{"x": {}}',
        }

        def run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=values[cmd[3]] + "\n")

        with patch.object(utils.subprocess, "run", side_effect=run):
            self.assertEqual(
                utils.yarn_registry_config(),
                {"npmPublishRegistry": None, "npmRegistryServer": "https://registry.yarnpkg.com", "npmScopes": {"x": {}}},
            )

    def test_undefined_is_unset(self):
        values = {
            "npmPublishRegistry": '"https://pkgs.dev.azure.com/o/_packaging/f/npm/registry/"',
            "npmRegistryServer": '"https://registry.yarnpkg.com"',
            "npmScopes": "undefined",
        }

        def run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=values[cmd[3]] + "\n")

        with patch.object(utils.subprocess, "run", side_effect=run):
            config = utils.yarn_registry_config()
        self.assertIsNone(config["npmScopes"])
        self.assertEqual(
            utils.publish_registry({"name": "@openmrs/foo"}, config),
            "https://pkgs.dev.azure.com/o/_packaging/f/npm/registry",
        )

    def test_none_when_yarn_fails(self):
        with patch.object(utils.subprocess, "run", side_effect=OSError("no yarn")):
            self.assertIsNone(utils.yarn_registry_config())

    def test_unknown_registry_without_config(self):
        self.assertIsNone(utils.publish_registry({"name": "@o/a"}, None))
        self.assertEqual(
            utils.publish_registry({"name": "@o/a", "publishConfig": {"registry": "https://p.example.org/"}}, None),
            "https://p.example.org",
        )


class TestPublishablePackages(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for location, manifest in {
            ".": {"name": "@o/root", "version": "2.1.0"},
            "packages/a": {"name": "@o/a", "version": "2.1.0"},
            "packages/b": {"name": "@o/b", "version": "1.0.0", "publishConfig": {"registry": "https://b.example.org"}},
        }.items():
            os.makedirs(os.path.join(self._tmp.name, location), exist_ok=True)
            with open(os.path.join(self._tmp.name, location, "package.json"), "w") as f:
                json.dump(manifest, f)

    def test_reports_each_package_at_its_own_version_and_registry(self):
        workspaces = [
            {"location": ".", "name": "@o/root"},
            {"location": "packages/a", "name": "@o/a"},
            {"location": "packages/b", "name": "@o/b"},
        ]
        with patch.object(utils, "yarn_workspaces", return_value=workspaces), \
                patch.object(utils, "yarn_registry_config", return_value={}):
            self.assertEqual(
                utils.publishable_packages(self._tmp.name),
                [
                    {"name": "@o/a", "version": "2.1.0", "registry": NPMJS},
                    {"name": "@o/b", "version": "1.0.0", "registry": "https://b.example.org"},
                ],
            )


class TestPublishablePackagesFailures(unittest.TestCase):
    def test_none_when_workspaces_cannot_be_listed(self):
        with patch.object(utils.subprocess, "run", side_effect=OSError("no yarn")):
            self.assertIsNone(utils.publishable_packages())

    def test_none_when_a_manifest_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(utils, "yarn_workspaces", return_value=[{"location": ".", "name": "a"}]), \
                patch.object(utils, "yarn_registry_config", return_value={}):
            self.assertIsNone(utils.publishable_packages(tmp))


class TestCheckNpmPublishTarget(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"REPOSITORY_OWNER": "acme", "PUBLISH": "true"})
        env.start()
        self.addCleanup(env.stop)

    def run_check(self, packages):
        with patch.object(check, "publishable_packages", return_value=packages) as listed:
            check.main()
        self.listed = listed

    def test_is_reserved(self):
        for name, registry, reserved in (
            ("@openmrs/esm-a", NPMJS, True),
            ("@openmrs/esm-a", "https://registry.yarnpkg.com", True),
            ("openmrs", NPMJS, True),
            ("openmrs-esm-a", NPMJS, False),
            ("@openmrs/esm-a", "https://pkgs.dev.azure.com/acme/_packaging/feed/npm/registry", False),
            ("@acme/esm-a", NPMJS, False),
        ):
            with self.subTest(name=name, registry=registry):
                self.assertEqual(check.is_reserved({"name": name, "registry": registry}), reserved)

    def test_allows_own_names(self):
        self.run_check([{"name": "@acme/a", "registry": NPMJS}])

    def test_fails_reserved_names_on_public_npm(self):
        with self.assertRaises(SystemExit):
            self.run_check([{"name": "@openmrs/a", "registry": NPMJS}])

    def test_fails_when_packages_cannot_be_listed(self):
        with self.assertRaises(SystemExit):
            self.run_check(None)

    def test_fails_when_registry_is_unknown(self):
        with self.assertRaises(SystemExit):
            self.run_check([{"name": "@openmrs/a", "registry": None}])

    def test_openmrs_org_is_not_checked(self):
        os.environ["REPOSITORY_OWNER"] = "openmrs"
        self.run_check([{"name": "@openmrs/a", "registry": NPMJS}])
        self.listed.assert_not_called()

    def test_not_publishing_is_not_checked(self):
        os.environ["PUBLISH"] = "false"
        self.run_check([{"name": "@openmrs/a", "registry": NPMJS}])
        self.listed.assert_not_called()


class _StubYarnTest(unittest.TestCase):
    """Runs a script with a stub `yarn` on PATH that logs its arguments."""

    STUB = """#!/usr/bin/env bash
echo "$*" >> "$YARN_LOG"
if [ "$1" = "--version" ]; then echo "$YARN_VERSION"; fi
exit "${YARN_EXIT:-0}"
"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        bin_dir = os.path.join(self._tmp.name, "bin")
        os.mkdir(bin_dir)
        with open(os.path.join(bin_dir, "yarn"), "w") as f:
            f.write(self.STUB)
        os.chmod(os.path.join(bin_dir, "yarn"), 0o755)
        with open(os.path.join(self._tmp.name, "package.json"), "w") as f:
            json.dump({"name": "@acme/a", "version": "1.2.3"}, f)
        self.yarn_log = os.path.join(self._tmp.name, "yarn.log")
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": self._tmp.name,
            "YARN_LOG": self.yarn_log,
        }

    def run_script(self, name, *args, **env):
        return subprocess.run(
            ["bash", os.path.join(SCRIPTS, name), *args],
            env={**self.env, **env}, cwd=self._tmp.name, capture_output=True, text=True,
        )


class TestPublishPackage(_StubYarnTest):
    def test_publishes_and_records(self):
        log = os.path.join(self._tmp.name, "published.txt")
        result = self.run_script("publish-package.sh", "--access", "public", "--tag", "next", PUBLISH_LOG=log)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(self.yarn_log) as f:
            self.assertEqual(f.read(), "npm publish --access public --tag next\n")
        with open(log) as f:
            self.assertEqual(f.read(), "@acme/a@1.2.3\n")

    def test_records_nothing_when_publish_fails(self):
        log = os.path.join(self._tmp.name, "published.txt")
        result = self.run_script("publish-package.sh", PUBLISH_LOG=log, YARN_EXIT="1")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(os.path.exists(log))


class TestVerifyNpmCredentials(_StubYarnTest):
    def test_token_is_enough(self):
        self.assertEqual(self.run_script("verify-npm-credentials.sh", NODE_AUTH_TOKEN="t").returncode, 0)

    def test_fails_without_token_or_oidc(self):
        result = self.run_script("verify-npm-credentials.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("NPM_AUTH_TOKEN", result.stdout)

    def test_oidc_needs_a_recent_yarn(self):
        for version, ok in (("4.10.3", True), ("4.12.0", True), ("4.10.2", False), ("3.6.1", False)):
            with self.subTest(version=version):
                result = self.run_script(
                    "verify-npm-credentials.sh",
                    ACTIONS_ID_TOKEN_REQUEST_URL="https://oidc", YARN_VERSION=version,
                )
                self.assertEqual(result.returncode == 0, ok, result.stdout)


if __name__ == "__main__":
    unittest.main()
