#!/usr/bin/env python3
"""Tests for stage-coverage.sh.

Drives the script in a temp working directory with a fake project tree and
reads back the GITHUB_OUTPUT it writes plus the staged files it produces.
"""

import os
import subprocess
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "stage-coverage.sh")


class StageCoverageTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.project = os.path.join(self.tmpdir, "project")
        self.coverage_dir = os.path.join(self.tmpdir, "coverage")
        self.github_output = os.path.join(self.tmpdir, "github_output")
        os.makedirs(self.project)
        open(self.github_output, "w").close()

    def tearDown(self):
        self._tmpdir.cleanup()

    def write_report(self, module_dir):
        """Create a jacoco.xml at <project>/<module_dir>/target/site/jacoco/."""
        report_dir = os.path.join(self.project, module_dir, "target", "site", "jacoco")
        os.makedirs(report_dir, exist_ok=True)
        with open(os.path.join(report_dir, "jacoco.xml"), "w") as f:
            f.write("<report/>")

    def run_script(self, **env):
        full_env = {
            **os.environ,
            "COVERAGE_DIR": self.coverage_dir,
            "GITHUB_OUTPUT": self.github_output,
            **env,
        }
        subprocess.run(
            ["bash", SCRIPT], cwd=self.project, env=full_env, check=True
        )

    def outputs(self):
        result = {}
        with open(self.github_output) as f:
            for line in f:
                key, _, value = line.rstrip("\n").partition("=")
                result[key] = value
        return result

    def staged_reports(self):
        return sorted(
            name for name in os.listdir(self.coverage_dir) if name.endswith("-jacoco.xml")
        )


class TestGating(StageCoverageTestBase):
    def test_auto_detect_with_reports_stages(self):
        self.write_report(".")
        self.run_script(UPLOAD_COVERAGE="")
        self.assertEqual(self.outputs()["staged"], "true")

    def test_auto_detect_without_reports_skips(self):
        self.run_script(UPLOAD_COVERAGE="")
        self.assertEqual(self.outputs()["staged"], "false")
        self.assertFalse(os.path.exists(self.coverage_dir))

    def test_force_false_skips_even_with_reports(self):
        self.write_report(".")
        self.run_script(UPLOAD_COVERAGE="false")
        self.assertEqual(self.outputs()["staged"], "false")
        self.assertFalse(os.path.exists(self.coverage_dir))

    def test_force_false_is_case_insensitive(self):
        self.write_report(".")
        self.run_script(UPLOAD_COVERAGE="FALSE")
        self.assertEqual(self.outputs()["staged"], "false")

    def test_no_reports_skips_even_when_enabled(self):
        self.run_script(UPLOAD_COVERAGE="true")
        self.assertEqual(self.outputs()["staged"], "false")
        self.assertFalse(os.path.exists(self.coverage_dir))


class TestReportNaming(StageCoverageTestBase):
    def test_root_and_module_reports_flattened(self):
        self.write_report(".")
        self.write_report("api")
        self.write_report(os.path.join("omod", "sub"))
        self.run_script(UPLOAD_COVERAGE="true")
        self.assertEqual(
            self.staged_reports(),
            ["api-jacoco.xml", "omod-sub-jacoco.xml", "root-jacoco.xml"],
        )


if __name__ == "__main__":
    unittest.main()
