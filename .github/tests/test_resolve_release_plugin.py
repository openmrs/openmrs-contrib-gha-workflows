#!/usr/bin/env python3
"""Tests for resolve_release_plugin.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import resolve_release_plugin as rrp


class TestChooseVersion(unittest.TestCase):
    def test_maven_default_or_none_gets_the_default(self):
        for declared in ("", None, "3.0.1"):
            with self.subTest(declared=declared):
                self.assertEqual(rrp.choose_version(declared, "3.0.1"), rrp.DEFAULT_VERSION)

    def test_older_versions_are_raised_to_the_minimum(self):
        for declared in ("2.5.3", "3.0.0-M7", "3.1.0"):
            with self.subTest(declared=declared):
                self.assertEqual(rrp.choose_version(declared, "3.0.1"), rrp.MINIMUM_VERSION)

    def test_newer_versions_are_kept(self):
        for declared in ("3.1.1", "3.2.0", "3.3.1", "4.0.0-M1"):
            with self.subTest(declared=declared):
                self.assertEqual(rrp.choose_version(declared, "2.5.3"), declared)

    def test_unparseable_versions_are_kept(self):
        self.assertEqual(rrp.choose_version("LATEST", "3.0.1"), "LATEST")


if __name__ == "__main__":
    unittest.main()
