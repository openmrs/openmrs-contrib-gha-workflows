#!/usr/bin/env python3
"""Tests for report_release_state.py."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import report_release_state as rrs

TAG = "v9.9.9"
VER = "9.9.9"


def state(live=(), missing=(), unknown=()):
    return {"live": list(live), "missing": list(missing), "unknown": list(unknown)}


def render(**kw):
    args = dict(tag=TAG, version=VER, branch="main", publish_only=False, pushed=True)
    args.update(kw)
    return rrs.render(**args)


class TestFailedBeforeTag(unittest.TestCase):
    def test_reports_nothing_escaped(self):
        out = render(tag="", version="", pushed=False)
        self.assertIn("Failed before a release tag was computed", out)
        self.assertNotIn("#### Recovery", out)

    def test_wins_over_every_other_state(self):
        # No tag means no release to describe, whatever else happened.
        out = render(tag="", publish_only=True, pushed=True, state=state(live=["@o/a"]))
        self.assertIn("Failed before a release tag was computed", out)
        self.assertNotIn("@o/a", out)


class TestPublished(unittest.TestCase):
    def test_keeps_tag_when_failure_came_after_publish(self):
        out = render(published=True)
        self.assertIn("Every package was published", out)
        self.assertIn("Do not delete the tag", out)
        self.assertNotIn("git push", out)


class TestNotPushed(unittest.TestCase):
    def test_version_still_free(self):
        out = render(pushed=False)
        self.assertIn("not pushed", out)
        self.assertIn(f"`{VER}` is still free", out)
        self.assertNotIn("#### Recovery", out)

    def test_publish_only_is_not_treated_as_unpushed(self):
        # publish-only never pushes, but the tag it reuses is already live.
        out = render(publish_only=True, pushed=False, state=state(missing=["@o/a"]))
        self.assertNotIn("not pushed", out)
        self.assertIn("pre-existing", out)


class TestPushUnknown(unittest.TestCase):
    def test_says_to_check_the_remote(self):
        out = render(pushed=None, release_sha="abc123")
        self.assertIn("Could not tell whether this run's tag", out)
        self.assertIn("Only if it is `abc123`", out)
        self.assertIn("do not use publish_only", out)
        self.assertNotIn("still free", out)
        self.assertNotIn(":refs/tags/", out)


class TestRolledBack(unittest.TestCase):
    def test_reports_tag_deleted_and_commit_reverted(self):
        out = render(state=state(missing=["@o/a"]), rollback="success", rollback_sha="abc123")
        self.assertIn("rolled back", out)
        self.assertIn(f"tag `{TAG}` was deleted", out)
        self.assertIn("`abc123`", out)
        self.assertIn(f"`{VER}` is still free", out)
        self.assertNotIn("#### Recovery", out)


class TestTagLive(unittest.TestCase):
    def test_normal_run_names_the_branch(self):
        self.assertIn("pushed to `main`", render(state=state(missing=["@o/a"])))

    def test_publish_only_does_not_claim_to_have_pushed(self):
        out = render(publish_only=True, pushed=False, state=state(missing=["@o/a"]))
        self.assertIn("this run committed and pushed nothing", out)
        self.assertNotIn("pushed to `main`", out)


class TestNpmState(unittest.TestCase):
    def test_nothing_published(self):
        out = render(state=state(missing=["@o/a", "@o/b"]))
        self.assertIn(f"nothing published at `{VER}`", out)
        # Listing every package adds nothing once we have said none published.
        self.assertNotIn("NOT published", out)

    def test_partial_publish_names_both_sides(self):
        out = render(state=state(live=["@o/a"], missing=["@o/b"]))
        self.assertIn("already published", out)
        self.assertIn("  - `@o/a`", out)
        self.assertIn("NOT published", out)
        self.assertIn("  - `@o/b`", out)

    def test_fully_published(self):
        out = render(state=state(live=["@o/a", "@o/b"]))
        self.assertIn("already published", out)
        self.assertNotIn("NOT published", out)

    def test_unknown_packages_are_named(self):
        out = render(state=state(missing=["@o/a"], unknown=["@o/b"]))
        self.assertIn("could not check", out)
        self.assertIn("  - `@o/b`", out)
        self.assertNotIn("nothing published", out)

    def test_missing_state_is_not_reported_as_nothing_published(self):
        for missing_state in (None, state()):
            out = render(state=missing_state)
            self.assertIn("could not determine what was published", out)
            self.assertNotIn("nothing published", out)


class TestRecovery(unittest.TestCase):
    def test_partial_publish_warns_version_is_burned(self):
        out = render(state=state(live=["@o/a"], missing=["@o/b"]))
        self.assertIn("cannot be republished or reused", out)
        self.assertIn("do not delete the tag", out)
        self.assertIn("--tolerate-republish", out)
        # Must not offer the clean restart: the version is already spent.
        self.assertNotIn("Start over", out)

    def test_unknown_npm_state_keeps_the_tag(self):
        for uncertain in (state(missing=["@o/a"], unknown=["@o/b"]), None):
            out = render(state=uncertain)
            self.assertIn("do not delete the tag", out)
            self.assertNotIn("Start over", out)

    def test_failed_rollback_offers_both_routes(self):
        out = render(state=state(missing=["@o/a"]), rollback="failure", release_sha="abc123")
        self.assertIn("automatic rollback failed", out)
        self.assertIn("Finish this release", out)
        self.assertIn("publish_only", out)
        self.assertIn("Start over", out)
        self.assertIn("git revert --no-edit abc123", out)
        self.assertIn(f"git push --atomic origin HEAD:refs/heads/main :refs/tags/{TAG}", out)

    def test_failed_rollback_without_sha_reverts_the_tag(self):
        out = render(state=state(missing=["@o/a"]), rollback="failure")
        self.assertIn(f"git revert --no-edit {TAG}^{{commit}}", out)

    def test_publish_only_says_just_re_run(self):
        out = render(publish_only=True, pushed=False, state=state(missing=["@o/a"]))
        self.assertIn("the tag is untouched", out)
        self.assertNotIn("Start over", out)
        self.assertNotIn(":refs/tags/", out)

    def test_github_release_note_present_once_tag_is_live(self):
        self.assertIn(rrs.NO_GH_RELEASE, render(state=state(missing=["@o/a"])))
        self.assertNotIn(rrs.NO_GH_RELEASE, render(pushed=False))


class TestLoadState(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "state.json")

    def test_reads_json(self):
        with open(self.path, "w") as f:
            json.dump(state(live=["@o/a"]), f)
        self.assertEqual(rrs.load_state(self.path), state(live=["@o/a"]))

    def test_absent_or_unreadable_is_none(self):
        self.assertIsNone(rrs.load_state(""))
        self.assertIsNone(rrs.load_state(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertIsNone(rrs.load_state(self.path))


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.summary = os.path.join(self._tmp.name, "summary.md")
        self.state_file = os.path.join(self._tmp.name, "state.json")
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(
            os.environ, {"GITHUB_STEP_SUMMARY": self.summary}, clear=False
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def _run(self, **env):
        os.environ.update(
            {
                "RELEASE_TAG": TAG,
                "RELEASE_VERSION": VER,
                "BRANCH": "main",
                "PUBLISH_ONLY": "false",
                "PUSHED": "false",
                "PUBLISHED": "false",
                "ROLLBACK_OUTCOME": "",
                "STATE_FILE": self.state_file,
                **env,
            }
        )
        rrs.main()
        with open(self.summary) as f:
            return f.read()

    def test_reads_state_file_once_the_tag_is_live(self):
        with open(self.state_file, "w") as f:
            json.dump(state(live=["@o/solo"]), f)
        out = self._run(PUSHED="true")
        self.assertIn("already published", out)
        self.assertIn("@o/solo", out)

    def test_push_state_from_assess_overrides_push_step_outcome(self):
        with open(self.state_file, "w") as f:
            json.dump({"push": "pushed", **state(missing=["@o/a"])}, f)
        out = self._run(PUSHED="false", ROLLBACK_OUTCOME="failure")
        self.assertIn("pushed to `main`", out)
        self.assertIn("automatic rollback failed", out)

    def test_reports_rollback(self):
        out = self._run(PUSHED="true", ROLLBACK_OUTCOME="success", ROLLBACK_SHA="abc123")
        self.assertIn("rolled back", out)

    def test_appends_rather_than_truncating(self):
        with open(self.summary, "w") as f:
            f.write("### Release Parameters\n")
        out = self._run(PUSHED="false")
        self.assertIn("### Release Parameters", out)
        self.assertIn("Release failed", out)

    def test_summary_is_optional(self):
        del os.environ["GITHUB_STEP_SUMMARY"]
        os.environ.update({"RELEASE_TAG": TAG, "RELEASE_VERSION": VER})
        rrs.main()  # must not raise


if __name__ == "__main__":
    unittest.main()
