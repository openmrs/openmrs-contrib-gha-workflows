#!/usr/bin/env python3
"""Tests for maven_publish_checks.py."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import maven_publish_checks as checks

MODULE = ["org.openmrs.module"]


class TestOpenmrsOwnedReason(unittest.TestCase):
    def test_openmrs_maven_repo(self):
        for url in (
            "https://mavenrepo.openmrs.org/modules",
            "https://mavenrepo.openmrs.org/snapshots/",
            "https://MAVENREPO.openmrs.org/releases",
        ):
            self.assertIsNotNone(checks.openmrs_owned_reason(url, []), url)

    def test_openmrs_artifactory_core_repos(self):
        for repo in ("modules", "snapshots", "releases"):
            url = f"https://openmrs.jfrog.io/artifactory/{repo}/"
            self.assertIsNotNone(checks.openmrs_owned_reason(url, []), url)

    def test_other_artifactory_repos_on_openmrs_host_allowed(self):
        for url in (
            "https://openmrs.jfrog.io/artifactory/modules-pih/",
            "https://openmrs.jfrog.io/artifactory/modules-pih-snapshots",
            "https://openmrs.jfrog.io/modules",
        ):
            self.assertIsNone(checks.openmrs_owned_reason(url, MODULE), url)

    def test_maven_central_reserves_openmrs_group_ids(self):
        url = "https://central.sonatype.com/api/v1/publisher"
        self.assertIsNotNone(checks.openmrs_owned_reason(url, ["org.openmrs"]))
        self.assertIsNotNone(checks.openmrs_owned_reason(url, ["org.example", "org.openmrs.module"]))
        self.assertIsNone(checks.openmrs_owned_reason(url, ["org.example", "org.openmrsx"]))

    def test_maven_central_hosts(self):
        for url in (
            "https://ossrh-staging-api.central.sonatype.com/service/local/staging/deploy/maven2/",
            "https://s01.oss.sonatype.org/content/repositories/snapshots",
            "https://repo1.maven.org/maven2",
            "https://central.sonatype.com/repository/maven-snapshots/",
        ):
            self.assertIsNotNone(checks.openmrs_owned_reason(url, MODULE), url)
        self.assertIsNone(checks.openmrs_owned_reason("https://notsonatype.com/x", MODULE))

    def test_private_repo_allows_openmrs_group_ids(self):
        url = "https://pkgs.dev.azure.com/acme/_packaging/feed/maven/v1"
        self.assertIsNone(checks.openmrs_owned_reason(url, MODULE))


class TestScmRepository(unittest.TestCase):
    def test_accepted_forms(self):
        for connection in (
            "scm:git:https://github.com/Acme/openmrs-module-x.git",
            "scm:git:https://github.com/acme/openmrs-module-x",
            "scm:git:git@github.com:acme/openmrs-module-x.git",
            "scm:git:ssh://git@github.com/acme/openmrs-module-x.git",
            "scm:git:git://github.com/acme/openmrs-module-x.git/",
            "https://github.com/acme/openmrs-module-x",
        ):
            self.assertEqual(checks.scm_repository(connection), "acme/openmrs-module-x", connection)

    def test_non_github(self):
        self.assertIsNone(checks.scm_repository("scm:git:https://gitlab.com/acme/x.git"))


class ScriptTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.output = os.path.join(self._tmpdir.name, "output")
        open(self.output, "w").close()

    def outputs(self):
        with open(self.output) as f:
            return dict(line.split("=", 1) for line in f.read().splitlines())


class TestCheckTarget(ScriptTestCase):
    def run_check(self, owner, url, group_ids=MODULE, central_publishing="false"):
        checks.check_target({
            "REPOSITORY_OWNER": owner,
            "TARGET_URL": url,
            "GROUP_IDS": json.dumps(group_ids),
            "CENTRAL_PUBLISHING": central_publishing,
        })
        return self.outputs()

    def setUp(self):
        super().setUp()
        os.environ["GITHUB_OUTPUT"] = self.output
        self.addCleanup(os.environ.pop, "GITHUB_OUTPUT", None)

    def test_openmrs_owner_always_allowed(self):
        self.assertEqual(self.run_check("OpenMRS", "https://mavenrepo.openmrs.org/modules")["allowed"], "true")

    def test_other_owner_blocked_from_openmrs_repo(self):
        out = self.run_check("acme", "https://mavenrepo.openmrs.org/snapshots")
        self.assertEqual(out["allowed"], "false")
        self.assertIn("OpenMRS repository", out["reason"])

    def test_other_owner_allowed_elsewhere(self):
        self.assertEqual(self.run_check("PIH", "https://openmrs.jfrog.io/artifactory/modules-pih/")["allowed"], "true")

    def test_any_reserved_target_blocks(self):
        urls = "https://repo.example.org/releases https://mavenrepo.openmrs.org/snapshots"
        self.assertEqual(self.run_check("acme", urls)["allowed"], "false")

    def test_no_target_allowed(self):
        self.assertEqual(self.run_check("acme", "")["allowed"], "true")

    def test_central_publishing_plugin_reserves_openmrs_group_ids(self):
        self.assertEqual(self.run_check("acme", "", central_publishing="true")["allowed"], "false")
        self.assertEqual(self.run_check("acme", "", ["org.acme"], central_publishing="true")["allowed"], "true")
        self.assertEqual(self.run_check("openmrs", "", central_publishing="true")["allowed"], "true")


class TestCheckScm(ScriptTestCase):
    def setUp(self):
        super().setUp()
        os.environ["GITHUB_OUTPUT"] = self.output
        self.addCleanup(os.environ.pop, "GITHUB_OUTPUT", None)

    def run_check(self, *connections):
        checks.check_scm({
            "SCM_CONNECTIONS": json.dumps(list(connections)),
            "GITHUB_REPOSITORY": "DIGI-UW/openmrs-module-ips",
        })
        return self.outputs()

    def test_urls_to_redirect(self):
        out = self.run_check(
            "scm:git:git@github.com:openmrs/openmrs-module-ips.git",
            "scm:git:https://github.com/openmrs/openmrs-module-ips/",
        )
        self.assertEqual(
            out["urls"],
            "git@github.com:openmrs/openmrs-module-ips.git https://github.com/openmrs/openmrs-module-ips/",
        )

    def test_missing_connection_fails(self):
        with self.assertRaises(SystemExit):
            self.run_check()


if __name__ == "__main__":
    unittest.main()
