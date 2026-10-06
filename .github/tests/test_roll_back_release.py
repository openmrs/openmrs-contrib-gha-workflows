#!/usr/bin/env python3
"""Tests for roll-back-release.sh.

Drives the script in a clone of a temporary bare repository that stands in for
the GitHub remote, then inspects the remote's branch and tags.
"""

import os
import subprocess
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "roll-back-release.sh")


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class RollBackReleaseTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = self._tmpdir.name
        self.remote = os.path.join(tmp, "remote.git")
        self.work = os.path.join(tmp, "work")
        self.github_output = os.path.join(tmp, "github_output")
        open(self.github_output, "w").close()

        git("init", "--quiet", "--bare", "--initial-branch=main", self.remote, cwd=tmp)
        git("clone", "--quiet", self.remote, self.work, cwd=tmp)
        self._configure(self.work)
        self._commit(self.work, "package.json", '{"version": "1.0.0"}', "init")
        git("push", "--quiet", "origin", "HEAD:refs/heads/main", cwd=self.work)

        self.release_sha = self._commit(
            self.work, "package.json", '{"version": "1.0.1"}', "(chore) Release v1.0.1"
        )
        git("tag", "-a", "v1.0.1", "-m", "Release v1.0.1", cwd=self.work)
        git("push", "--quiet", "--atomic", "origin", "HEAD:refs/heads/main",
            "refs/tags/v1.0.1", cwd=self.work)

    def _configure(self, repo):
        git("config", "user.name", "test", cwd=repo)
        git("config", "user.email", "test@example.com", cwd=repo)

    def _commit(self, repo, path, content, message):
        with open(os.path.join(repo, path), "w") as f:
            f.write(content + "\n")
        git("add", path, cwd=repo)
        git("commit", "--quiet", "-m", message, cwd=repo)
        return git("rev-parse", "HEAD", cwd=repo)

    def _push_from_elsewhere(self, path, content, message):
        other = os.path.join(self._tmpdir.name, "other")
        if not os.path.isdir(other):
            git("clone", "--quiet", self.remote, other, cwd=self._tmpdir.name)
            self._configure(other)
        git("pull", "--quiet", "origin", "main", cwd=other)
        self._commit(other, path, content, message)
        git("push", "--quiet", "origin", "HEAD:refs/heads/main", cwd=other)

    def run_script(self, **extra):
        env = {
            **os.environ,
            "BRANCH": "main",
            "RELEASE_TAG": "v1.0.1",
            "RELEASE_VERSION": "1.0.1",
            "RELEASE_SHA": self.release_sha,
            "RETRY_DELAY": "0",
            "GITHUB_OUTPUT": self.github_output,
            **extra,
        }
        return subprocess.run(
            ["bash", SCRIPT], cwd=self.work, env=env, capture_output=True, text=True
        )

    def remote_tags(self):
        return git("tag", "--list", cwd=self.remote).split()

    def remote_log(self):
        return git("log", "--format=%s", "main", cwd=self.remote).splitlines()

    def remote_file(self, path):
        return git("show", f"main:{path}", cwd=self.remote)

    def test_deletes_tag_and_reverts_release_commit(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("v1.0.1", self.remote_tags())
        self.assertEqual(self.remote_log()[0], "(chore) Roll back release v1.0.1")
        self.assertIn('"1.0.0"', self.remote_file("package.json"))
        with open(self.github_output) as f:
            self.assertIn(f"sha={git('rev-parse', 'main', cwd=self.remote)}", f.read())

    def test_push_token_does_not_break_rollback(self):
        result = self.run_script(PUSH_TOKEN="fresh-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("v1.0.1", self.remote_tags())

    def test_push_that_lands_despite_an_error_counts_as_success(self):
        # A git wrapper whose push really pushes, then reports failure.
        shim = os.path.join(self._tmpdir.name, "bin")
        os.makedirs(shim)
        real_git = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
        with open(os.path.join(shim, "git"), "w") as f:
            f.write(f'#!/bin/bash\nif [ "$1" = push ]; then "{real_git}" "$@"; exit 1; fi\n'
                    f'exec "{real_git}" "$@"\n')
        os.chmod(os.path.join(shim, "git"), 0o755)
        result = self.run_script(PATH=f"{shim}:{os.environ['PATH']}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("v1.0.1", self.remote_tags())
        self.assertEqual(self.remote_log()[:2], ["(chore) Roll back release v1.0.1", "(chore) Release v1.0.1"])

    def test_reverts_on_top_of_later_commits(self):
        self._push_from_elsewhere("other.txt", "later", "(fix) Later change")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("v1.0.1", self.remote_tags())
        self.assertEqual(
            self.remote_log()[:2], ["(chore) Roll back release v1.0.1", "(fix) Later change"]
        )

    def test_conflicting_revert_changes_nothing(self):
        self._push_from_elsewhere("package.json", '{"version": "1.0.1", "x": 1}', "(fix) Touch manifest")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts", result.stdout + result.stderr)
        self.assertIn("v1.0.1", self.remote_tags())
        self.assertEqual(self.remote_log()[0], "(fix) Touch manifest")


if __name__ == "__main__":
    unittest.main()
