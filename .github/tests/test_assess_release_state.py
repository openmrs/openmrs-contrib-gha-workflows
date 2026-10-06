#!/usr/bin/env python3
"""Tests for assess_release_state.py."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import assess_release_state as ars

VER = "9.9.9"


class TestClassify(unittest.TestCase):
    def test_partitions_on_probe(self):
        states = {"@o/a": "live", "@o/b": "missing", "@o/c": "unknown"}
        result = ars.classify(list(states), VER, probe=lambda p, v: states[p])
        self.assertEqual(
            result, {"live": ["@o/a"], "missing": ["@o/b"], "unknown": ["@o/c"]}
        )

    def test_empty_package_list(self):
        self.assertEqual(
            ars.classify([], VER), {"live": [], "missing": [], "unknown": []}
        )


class TestSafeToRollBack(unittest.TestCase):
    def _state(self, push="pushed", live=(), missing=(), unknown=()):
        return {"push": push, "live": list(live), "missing": list(missing),
                "unknown": list(unknown)}

    def test_safe_when_pushed_and_nothing_published(self):
        self.assertTrue(ars.safe_to_roll_back(self._state(missing=["@o/a"])))

    def test_unsafe_when_anything_is_live(self):
        self.assertFalse(ars.safe_to_roll_back(self._state(live=["@o/a"], missing=["@o/b"])))

    def test_unsafe_when_anything_is_unknown(self):
        self.assertFalse(ars.safe_to_roll_back(self._state(missing=["@o/a"], unknown=["@o/b"])))

    def test_unsafe_when_no_packages_were_found(self):
        self.assertFalse(ars.safe_to_roll_back(self._state()))

    def test_unsafe_unless_this_run_pushed(self):
        for push in ("not_pushed", "unknown", "preexisting"):
            self.assertFalse(ars.safe_to_roll_back(self._state(push=push, missing=["@o/a"])))


class TestPushState(unittest.TestCase):
    def test_publish_only_reuses_an_existing_tag(self):
        self.assertEqual(ars.push_state("v1", "skipped", True), "preexisting")

    def test_successful_push_step(self):
        self.assertEqual(ars.push_state("v1", "success", False), "pushed")

    def test_unsuccessful_push_step_checks_the_remote(self):
        for outcome in ("failure", "cancelled", "skipped"):
            self.assertEqual(
                ars.push_state("v1", outcome, False, landed=lambda tag: "pushed"), "pushed"
            )


class TestTagLanded(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = self._tmp.name
        self.remote = os.path.join(tmp, "remote.git")
        self.work = os.path.join(tmp, "work")
        self._git("init", "--quiet", "--bare", self.remote, cwd=tmp)
        self._git("clone", "--quiet", self.remote, self.work, cwd=tmp)
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet",
                  "--allow-empty", "-m", "init")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "tag", "-a", "v1", "-m", "v1")
        cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, cwd)

    def _git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd or self.work, check=True, capture_output=True)

    def test_not_pushed(self):
        self.assertEqual(ars.tag_landed("v1"), "not_pushed")

    def test_pushed(self):
        self._git("push", "--quiet", "origin", "HEAD:refs/heads/main", "refs/tags/v1")
        self.assertEqual(ars.tag_landed("v1"), "pushed")

    def test_someone_elses_tag_is_not_ours(self):
        other = os.path.join(self._tmp.name, "other")
        self._git("clone", "--quiet", self.remote, other, cwd=self._tmp.name)
        self._git("-c", "user.name=o", "-c", "user.email=o@o", "commit", "--quiet",
                  "--allow-empty", "-m", "other", cwd=other)
        self._git("-c", "user.name=o", "-c", "user.email=o@o", "tag", "-a", "v1", "-m", "v1",
                  cwd=other)
        self._git("push", "--quiet", "origin", "HEAD:refs/heads/main", "refs/tags/v1", cwd=other)
        self.assertEqual(ars.tag_landed("v1"), "unknown")

    def test_unreachable_remote(self):
        self._git("remote", "set-url", "origin", os.path.join(self._tmp.name, "missing.git"))
        self.assertEqual(ars.tag_landed("v1"), "unknown")


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output = os.path.join(self._tmp.name, "output")
        self.state_file = os.path.join(self._tmp.name, "state.json")
        self._env = patch.dict(os.environ, {
            "GITHUB_OUTPUT": self.output,
            "STATE_FILE": self.state_file,
            "RELEASE_TAG": "v" + VER,
            "RELEASE_VERSION": VER,
            "PUSH_OUTCOME": "success",
            "PUBLISH_ONLY": "false",
            "PUBLISH_RAN": "true",
        })
        self._env.start()
        self.addCleanup(self._env.stop)
        workspaces = [
            {"location": ".", "name": "@o/root"},
            {"location": "packages/a", "name": "@o/a"},
        ]
        for target, kwargs in (
            ("yarn_workspaces", {"return_value": workspaces}),
            ("npm_version_state", {"return_value": "missing"}),
        ):
            p = patch.object(ars, target, **kwargs)
            setattr(self, target, p.start())
            self.addCleanup(p.stop)
        p = patch.object(ars.time, "sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def test_writes_state_and_output(self):
        ars.main()
        with open(self.state_file) as f:
            self.assertEqual(
                json.load(f),
                {"push": "pushed", "live": [], "missing": ["@o/a"], "unknown": []},
            )
        with open(self.output) as f:
            output = f.read()
        self.assertIn("push=pushed", output)
        self.assertIn("safe_to_roll_back=true", output)

    def test_skips_npm_when_the_tag_did_not_land(self):
        os.environ["PUSH_OUTCOME"] = "failure"
        with patch.object(ars, "tag_landed", return_value="not_pushed"):
            ars.main()
        self.yarn_workspaces.assert_not_called()
        self.sleep.assert_not_called()
        with open(self.output) as f:
            self.assertIn("safe_to_roll_back=false", f.read())

    def test_rolls_back_a_push_that_landed_despite_failing(self):
        os.environ["PUSH_OUTCOME"] = "failure"
        os.environ["PUBLISH_RAN"] = "false"
        with patch.object(ars, "tag_landed", return_value="pushed"):
            ars.main()
        with open(self.output) as f:
            self.assertIn("safe_to_roll_back=true", f.read())

    def test_waits_for_registry_only_after_a_publish_attempt(self):
        ars.main()
        self.sleep.assert_called_once_with(ars.SETTLE_SECONDS)
        self.sleep.reset_mock()
        os.environ["PUBLISH_RAN"] = "false"
        ars.main()
        self.sleep.assert_not_called()

    def test_live_package_blocks_rollback(self):
        self.npm_version_state.return_value = "live"
        ars.main()
        with open(self.output) as f:
            self.assertIn("safe_to_roll_back=false", f.read())


if __name__ == "__main__":
    unittest.main()
