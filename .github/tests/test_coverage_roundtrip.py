#!/usr/bin/env python3
"""Round-trip test tying stage-coverage.sh to prepare-codecov-upload.sh.

Guards the filename contract between the two scripts: stage writes
`${module}-jacoco.xml`, prepare finds `*-jacoco.xml`. Each script's own unit
tests use hand-written fixtures, so a rename breaking the contract would still
pass them while live uploads silently fell into prepare's found=false branch.
"""

import os
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
STAGE = os.path.join(SCRIPTS, "stage-coverage.sh")
PREPARE = os.path.join(SCRIPTS, "prepare-codecov-upload.sh")


class CoverageRoundTripTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.project = os.path.join(self.tmpdir, "project")
        self.coverage_dir = os.path.join(self.tmpdir, "coverage")
        self.output_dir = os.path.join(self.tmpdir, "out")
        self.bin_dir = os.path.join(self.tmpdir, "bin")
        report_dir = os.path.join(self.project, "api", "target", "site", "jacoco")
        os.makedirs(report_dir)
        os.makedirs(self.bin_dir)
        with open(os.path.join(report_dir, "jacoco.xml"), "w") as f:
            f.write("<report/>")
        # prepare resolves the PR via `gh`; a stub returning no PR is enough here.
        gh = os.path.join(self.bin_dir, "gh")
        with open(gh, "w") as f:
            f.write("#!/usr/bin/env bash\nprintf ''\n")
        os.chmod(gh, 0o755)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run(self, script, cwd=None, **env):
        subprocess.run(
            ["bash", script],
            cwd=cwd,
            env={**os.environ, **env},
            check=True,
            capture_output=True,
            text=True,
        )

    def _outputs(self, path):
        result = {}
        with open(path) as f:
            for line in f:
                key, _, value = line.rstrip("\n").partition("=")
                result[key] = value
        return result

    def test_staged_reports_are_found_by_prepare(self):
        stage_out = os.path.join(self.tmpdir, "stage_output")
        open(stage_out, "w").close()
        self._run(
            STAGE,
            cwd=self.project,
            COVERAGE_DIR=self.coverage_dir,
            GITHUB_OUTPUT=stage_out,
        )
        self.assertEqual(self._outputs(stage_out)["staged"], "true")

        prepare_out = os.path.join(self.tmpdir, "prepare_output")
        open(prepare_out, "w").close()
        self._run(
            PREPARE,
            PATH=self.bin_dir + os.pathsep + os.environ.get("PATH", ""),
            COVERAGE_DIR=self.coverage_dir,
            OUTPUT_DIR=self.output_dir,
            GITHUB_OUTPUT=prepare_out,
            HEAD_SHA="cafe",
            HEAD_BRANCH="x",
            HEAD_REPO="openmrs/repo",
            BASE_REPO="openmrs/repo",
        )
        out = self._outputs(prepare_out)
        self.assertEqual(out["found"], "true")
        self.assertTrue(out["files"])


if __name__ == "__main__":
    unittest.main()
