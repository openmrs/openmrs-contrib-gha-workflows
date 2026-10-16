#!/usr/bin/env python3
"""Tests for maven-release.sh.

Runs the script against a stub `mvn` on PATH that records each invocation's
arguments and fails for the goals named in FAIL_GOALS.
"""

import json
import os
import subprocess
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "maven-release.sh")
PLUGIN = "org.apache.maven.plugins:maven-release-plugin:3.1.1"

STUB_MVN = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["MVN_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
fail = os.environ.get("FAIL_GOALS", "").split()
sys.exit(1 if any(goal in sys.argv[1:] for goal in fail) else 0)
"""


class MavenReleaseTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = self._tmpdir.name
        bin_dir = os.path.join(tmp, "bin")
        os.mkdir(bin_dir)
        mvn = os.path.join(bin_dir, "mvn")
        with open(mvn, "w") as f:
            f.write(STUB_MVN)
        os.chmod(mvn, 0o755)
        self.log = os.path.join(tmp, "mvn.log")
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "MVN_LOG": self.log,
            "RUNNER_TEMP": tmp,
        }

    def run_script(self, *args, fail_goals=""):
        env = {**self.env, "FAIL_GOALS": fail_goals}
        return subprocess.run(
            ["bash", SCRIPT, *args], env=env, capture_output=True, text=True,
            cwd=self._tmpdir.name,
        )

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def test_prepares_performs_and_deploys_snapshot(self):
        result = self.run_script("-r", "1.3.3", "-d", "1.4.0", "-s", "-p", "ci")
        self.assertEqual(result.returncode, 0, result.stderr)
        prepare, perform, deploy = self.calls()
        self.assertEqual(prepare[0], f"{PLUGIN}:prepare")
        self.assertEqual(perform[0], f"{PLUGIN}:perform")
        self.assertEqual(deploy, ["-B", "deploy", "-DskipTests", "-Pci"])
        for call in (prepare, perform):
            self.assertIn("-DreleaseVersion=1.3.3", call)
            self.assertIn("-DdevelopmentVersion=1.4.0-SNAPSHOT", call)
            self.assertIn("-Darguments=-DskipTests -Pci", call)
            self.assertTrue(any(a.startswith(f"-Dmaven.repo.local={self._tmpdir.name}") for a in call))

    def test_runs_the_requested_plugin_version(self):
        self.run_script("-r", "1.3.3", "-v", "3.3.1")
        prepare, perform, _ = self.calls()
        self.assertEqual(prepare[0], "org.apache.maven.plugins:maven-release-plugin:3.3.1:prepare")
        self.assertEqual(perform[0], "org.apache.maven.plugins:maven-release-plugin:3.3.1:perform")

    def test_passes_properties_to_release_builds_and_snapshot_deploy(self):
        prop = "altReleaseDeploymentRepository=r::default::https://repo.example.org/releases"
        result = self.run_script("-r", "1.3.3", "-s", "-D", prop)
        self.assertEqual(result.returncode, 0, result.stderr)
        prepare, perform, deploy = self.calls()
        for call in (prepare, perform):
            self.assertIn(f"-Darguments=-DskipTests -D{prop}", call)
        self.assertEqual(deploy, ["-B", "deploy", "-DskipTests", f"-D{prop}"])

    def test_rejects_properties_with_whitespace(self):
        result = self.run_script("-r", "1.3.3", "-D", "a=b c")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_keeps_an_existing_snapshot_suffix(self):
        self.run_script("-r", "1.3.3", "-d", "1.4.0-SNAPSHOT")
        self.assertIn("-DdevelopmentVersion=1.4.0-SNAPSHOT", self.calls()[0])

    def test_minor_release_defaults_to_next_minor_snapshot(self):
        self.run_script("-r", "2.18.0")
        self.assertIn("-DdevelopmentVersion=2.19.0-SNAPSHOT", self.calls()[0])

    def test_pre_release_defaults_to_its_release_snapshot(self):
        for version in ("1.2.0-beta.1", "1.2.3-rc", "2.0.0-alpha-2"):
            with self.subTest(version=version):
                open(self.log, "w").close()
                self.run_script("-r", version)
                base = version.split("-")[0]
                self.assertIn(f"-DdevelopmentVersion={base}-SNAPSHOT", self.calls()[0])

    def test_patch_release_leaves_development_version_to_the_plugin(self):
        self.run_script("-r", "2.18.1")
        self.assertFalse(any(a.startswith("-DdevelopmentVersion") for a in self.calls()[0]))

    def test_rolls_back_when_prepare_fails(self):
        result = self.run_script("-r", "1.3.3", fail_goals=f"{PLUGIN}:prepare")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c[0] for c in self.calls()], [f"{PLUGIN}:prepare", f"{PLUGIN}:rollback"])

    def test_stops_without_snapshot_deploy_when_perform_fails(self):
        result = self.run_script("-r", "1.3.3", fail_goals=f"{PLUGIN}:perform")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c[0] for c in self.calls()], [f"{PLUGIN}:prepare", f"{PLUGIN}:perform"])

    def test_reports_release_before_snapshot_deploy(self):
        output = os.path.join(self._tmpdir.name, "output")
        self.env["GITHUB_OUTPUT"] = output
        result = self.run_script("-r", "1.3.3", fail_goals="deploy")
        self.assertEqual(result.returncode, 1)
        self.assertIn("1.3.3 was released", result.stderr)
        with open(output) as f:
            self.assertEqual(f.read(), "released=true\n")

    def test_requires_a_release_version(self):
        result = self.run_script("-d", "1.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
