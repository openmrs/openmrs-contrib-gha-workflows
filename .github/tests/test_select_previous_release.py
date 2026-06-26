#!/usr/bin/env python3
"""Tests for select_previous_release.py."""

import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import select_previous_release as spr


class TestSelectPrevious(unittest.TestCase):
    def test_picks_greatest_older_release(self):
        tags = ["foo-2.5.1", "foo-2.5.2", "foo-2.5.3"]
        self.assertEqual(spr.select_previous(tags, "foo-", "2.5.4"), "foo-2.5.3")

    def test_ignores_newer_line_when_releasing_older(self):
        # Releasing 2.5.4 off the 2.x branch after 3.0.0 already shipped must
        # diff against 2.5.3, never the newer 3.0.0.
        tags = ["foo-2.5.3", "foo-3.0.0"]
        self.assertEqual(spr.select_previous(tags, "foo-", "2.5.4"), "foo-2.5.3")

    def test_cross_minor(self):
        tags = ["foo-2.5.3", "foo-2.5.4"]
        self.assertEqual(spr.select_previous(tags, "foo-", "2.6.0"), "foo-2.5.4")

    def test_tolerates_v_prefixed_tags(self):
        tags = ["foo-v2.3.1", "foo-v2.3.2"]
        self.assertEqual(spr.select_previous(tags, "foo-", "2.3.3"), "foo-v2.3.2")

    def test_tolerates_v_prefixed_release_version(self):
        tags = ["foo-2.3.3"]
        self.assertEqual(spr.select_previous(tags, "foo-", "v2.3.4"), "foo-2.3.3")

    def test_mixed_v_and_plain_tags_order_together(self):
        tags = ["v2.3.2", "2.3.3"]
        self.assertEqual(spr.select_previous(tags, "", "2.3.4"), "2.3.3")

    def test_first_major_uses_latest_prior_line(self):
        tags = ["foo-2.5.2", "foo-2.5.3"]
        self.assertEqual(spr.select_previous(tags, "foo-", "3.0.0"), "foo-2.5.3")

    def test_excludes_prereleases(self):
        # 3.0.0-beta.1 sorts below 3.0.0 in semver but must not be a base.
        tags = ["foo-2.5.3", "foo-3.0.0-beta.1"]
        self.assertEqual(spr.select_previous(tags, "foo-", "3.0.0"), "foo-2.5.3")

    def test_only_prereleases_yields_none(self):
        tags = ["foo-1.0.0-alpha", "foo-1.0.0-rc.1"]
        self.assertIsNone(spr.select_previous(tags, "foo-", "1.0.0"))

    def test_no_prior_release_yields_none(self):
        self.assertIsNone(spr.select_previous(["foo-1.0.0"], "foo-", "1.0.0"))
        self.assertIsNone(spr.select_previous([], "foo-", "1.0.0"))

    def test_equal_version_excluded(self):
        tags = ["foo-2.5.3", "foo-2.5.4"]
        self.assertEqual(spr.select_previous(tags, "foo-", "2.5.4"), "foo-2.5.3")

    def test_isolates_other_artifacts(self):
        # A sibling artifact's tags share no prefix and must be ignored, even
        # when one prefix is itself a prefix of another (foo- vs foo-api-).
        tags = ["foo-1.0.0", "foo-api-9.9.9", "bar-5.0.0"]
        self.assertEqual(spr.select_previous(tags, "foo-", "1.0.1"), "foo-1.0.0")

    def test_empty_prefix_matches_bare_version_tags(self):
        tags = ["2.5.2", "2.5.3", "not-a-version"]
        self.assertEqual(spr.select_previous(tags, "", "2.5.4"), "2.5.3")

    def test_build_metadata_tolerated(self):
        tags = ["foo-2.5.3+build.7"]
        self.assertEqual(spr.select_previous(tags, "foo-", "2.5.4"), "foo-2.5.3+build.7")

    def test_ignores_blank_and_nonmatching_lines(self):
        tags = ["", "  ", "foo-1.2.0", "foo-1.2", "foo-1.2.0.3"]
        self.assertEqual(spr.select_previous(tags, "foo-", "1.3.0"), "foo-1.2.0")

    def test_rejects_prerelease_release_version(self):
        with self.assertRaises(SystemExit):
            spr.select_previous(["foo-1.0.0"], "foo-", "1.1.0-rc.1")


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.out_path = os.path.join(self._tmpdir.name, "output")
        self._env = patch.dict(
            os.environ, {"GITHUB_OUTPUT": self.out_path}, clear=False
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmpdir.cleanup()

    def _run(self, tags):
        os.environ["TAG_PREFIX"] = "foo-"
        os.environ["RELEASE_VERSION"] = "2.5.4"
        with patch("sys.stdin", io.StringIO("\n".join(tags))):
            spr.main()
        with open(self.out_path) as f:
            return f.read()

    def test_writes_selected_base_ref(self):
        self.assertIn("base_ref=foo-2.5.3", self._run(["foo-2.5.3", "foo-3.0.0"]))

    def test_writes_empty_base_ref_when_none(self):
        self.assertIn("base_ref=", self._run(["bar-1.0.0"]))

    def test_requires_release_version(self):
        os.environ["RELEASE_VERSION"] = ""
        os.environ["TAG_PREFIX"] = "foo-"
        with patch("sys.stdin", io.StringIO("foo-1.0.0")):
            with self.assertRaises(SystemExit):
                spr.main()


if __name__ == "__main__":
    unittest.main()
