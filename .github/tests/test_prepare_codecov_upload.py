#!/usr/bin/env python3
"""Tests for prepare-codecov-upload.sh.

Drives the script against a fake downloaded-artifact directory and reads back
the GITHUB_OUTPUT it writes plus the report files it stages.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "prepare-codecov-upload.sh"
)


class PrepareCodecovUploadTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.coverage_dir = os.path.join(self.tmpdir, "coverage")
        self.output_dir = os.path.join(self.tmpdir, "out")
        self.github_output = os.path.join(self.tmpdir, "github_output")
        self.bin_dir = os.path.join(self.tmpdir, "bin")
        os.makedirs(self.coverage_dir)
        os.makedirs(self.bin_dir)
        open(self.github_output, "w").close()
        self._write_fake_gh()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_fake_gh(self):
        """A stub `gh` that prints $FAKE_GH_PR, or fails if $FAKE_GH_FAIL is set,
        standing in for the `gh api .../pulls --jq ...` PR lookup."""
        gh = os.path.join(self.bin_dir, "gh")
        with open(gh, "w") as f:
            f.write(
                "#!/usr/bin/env bash\n"
                '[ -n "${FAKE_GH_FAIL:-}" ] && exit 1\n'
                'printf "%s" "${FAKE_GH_PR:-}"\n'
            )
        os.chmod(gh, 0o755)

    def write_report(self, name, content="<report/>"):
        with open(os.path.join(self.coverage_dir, name), "w") as f:
            f.write(content)

    def run_script(self, check=True, **env):
        full_env = {
            **os.environ,
            "PATH": self.bin_dir + os.pathsep + os.environ.get("PATH", ""),
            "COVERAGE_DIR": self.coverage_dir,
            "OUTPUT_DIR": self.output_dir,
            "GITHUB_OUTPUT": self.github_output,
            "HEAD_SHA": "cafe",
            "HEAD_BRANCH": "feature/x",
            "HEAD_REPO": "openmrs/repo",
            "BASE_REPO": "openmrs/repo",
            **env,
        }
        return subprocess.run(
            ["bash", SCRIPT], env=full_env, check=check, capture_output=True, text=True
        )

    def outputs(self):
        result = {}
        with open(self.github_output) as f:
            for line in f:
                key, _, value = line.rstrip("\n").partition("=")
                result[key] = value
        return result


class TestMetadataSource(PrepareCodecovUploadTestBase):
    def test_commit_and_branch_from_trusted_env(self):
        self.write_report("api-jacoco.xml")
        self.run_script(HEAD_SHA="deadbeef", HEAD_BRANCH="main")
        out = self.outputs()
        self.assertEqual(out["commit"], "deadbeef")
        self.assertEqual(out["branch"], "main")
        self.assertEqual(out["found"], "true")

    def test_same_repo_uses_bare_branch(self):
        self.write_report("api-jacoco.xml")
        self.run_script(HEAD_BRANCH="main", HEAD_REPO="openmrs/repo", BASE_REPO="openmrs/repo")
        self.assertEqual(self.outputs()["branch"], "main")

    def test_fork_branch_is_namespaced(self):
        self.write_report("api-jacoco.xml")
        self.run_script(HEAD_BRANCH="main", HEAD_REPO="attacker/repo", BASE_REPO="openmrs/repo")
        self.assertEqual(self.outputs()["branch"], "attacker:main")

    def test_empty_head_repo_fails_closed_and_namespaces(self):
        self.write_report("api-jacoco.xml")
        self.run_script(HEAD_BRANCH="main", HEAD_REPO="", BASE_REPO="openmrs/repo")
        self.assertNotEqual(self.outputs()["branch"], "main")

    def test_empty_head_branch_stays_namespaced(self):
        self.write_report("api-jacoco.xml")
        self.run_script(HEAD_BRANCH="", HEAD_REPO="attacker/repo", BASE_REPO="openmrs/repo")
        self.assertEqual(self.outputs()["branch"], "attacker:")


class TestPrNumber(PrepareCodecovUploadTestBase):
    def test_pr_from_api_lookup(self):
        self.write_report("api-jacoco.xml")
        self.run_script(FAKE_GH_PR="42")
        self.assertEqual(self.outputs()["pr"], "42")

    def test_no_open_pr_gives_empty(self):
        self.write_report("api-jacoco.xml")
        self.run_script(FAKE_GH_PR="")
        self.assertEqual(self.outputs()["pr"], "")

    def test_gh_failure_gives_empty_pr(self):
        self.write_report("api-jacoco.xml")
        self.run_script(FAKE_GH_FAIL="1")
        out = self.outputs()
        self.assertEqual(out["pr"], "")
        self.assertEqual(out["found"], "true")

    def test_non_numeric_api_output_sanitized_to_empty(self):
        self.write_report("api-jacoco.xml")
        self.run_script(FAKE_GH_PR="not-a-number")
        self.assertEqual(self.outputs()["pr"], "")


class TestReportStaging(PrepareCodecovUploadTestBase):
    def test_reports_copied_to_safe_names(self):
        self.write_report("api-jacoco.xml", "A")
        self.write_report("omod-jacoco.xml", "B")
        self.run_script()
        files = self.outputs()["files"].split(",")
        self.assertEqual(len(files), 2)
        self.assertTrue(all(os.path.isfile(f) for f in files))

    def test_comma_in_filename_does_not_break_list(self):
        self.write_report("ev,il-jacoco.xml", "A")
        self.write_report("ok-jacoco.xml", "B")
        self.run_script()
        files = self.outputs()["files"].split(",")
        self.assertEqual(len(files), 2)
        self.assertTrue(all(os.path.isfile(f) for f in files))

    def test_report_named_report_0_is_not_clobbered(self):
        self.write_report("report-0-jacoco.xml", "ORIG")
        self.write_report("aaa-jacoco.xml", "OTHER")
        self.run_script()
        files = self.outputs()["files"].split(",")
        self.assertEqual(len(files), 2)
        self.assertEqual(sorted(Path(f).read_text() for f in files), ["ORIG", "OTHER"])

    def test_no_reports_sets_found_false(self):
        self.run_script()
        self.assertEqual(self.outputs()["found"], "false")
        self.assertNotIn("files", self.outputs())


if __name__ == "__main__":
    unittest.main()
