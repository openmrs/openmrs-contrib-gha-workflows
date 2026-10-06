#!/usr/bin/env python3
"""Tests for group_release_notes.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import group_release_notes as grn

PR = "https://github.com/openmrs/openmrs-esm-example/pull"


class TestClassify(unittest.TestCase):
    def test_parenthesised_types(self):
        self.assertEqual(grn.classify("(feat) O3-1: Add a thing"), "feat")
        self.assertEqual(grn.classify("(fix) Fix a thing"), "fix")
        self.assertEqual(grn.classify("(test) Cover a thing"), "test")
        self.assertEqual(grn.classify("(docs) Document a thing"), "docs")
        self.assertEqual(grn.classify("(chore) Bump a thing"), "chore")
        self.assertEqual(grn.classify("(BREAKING) O3-2: Drop a thing"), "breaking")

    def test_conventional_types(self):
        self.assertEqual(grn.classify("fix: Fix a thing"), "fix")
        self.assertEqual(grn.classify("chore(release): v1.2.3"), "chore")
        self.assertEqual(grn.classify("feat(api)!: Replace a thing"), "breaking")

    def test_ignores_surrounding_whitespace(self):
        self.assertEqual(grn.classify(" (feat) O3-5851: Add a thing"), "feat")
        self.assertEqual(grn.classify("\tfix: Fix a thing "), "fix")

    def test_unknown_or_missing_type(self):
        self.assertIsNone(grn.classify("(refactor) Move a thing"))
        self.assertIsNone(grn.classify("Revert \"(feat) Add a thing\""))
        self.assertIsNone(grn.classify("O3-3: Untyped title"))


class TestGroupNotes(unittest.TestCase):
    def test_groups_entries_in_section_order(self):
        body = (
            "<!-- Release notes generated using configuration in .github/release.yml at v1.1.0 -->\n"
            "\n"
            "## What's Changed\n"
            f"* (chore) Bump deps by @a in {PR}/4\n"
            f"* (fix) Fix a bug by @b in {PR}/3\n"
            f"* Untyped change by @c in {PR}/5\n"
            f"* (feat) Add a feature by @d in {PR}/2\n"
            f"* (BREAKING) Drop an API by @e in {PR}/1\n"
            "\n"
            "## New Contributors\n"
            f"* @c made their first contribution in {PR}/5\n"
            "\n"
            "**Full Changelog**: https://github.com/openmrs/openmrs-esm-example/compare/v1.0.0...v1.1.0\n"
        )
        expected = (
            "<!-- Release notes generated using configuration in .github/release.yml at v1.1.0 -->\n"
            "\n"
            "## What's Changed\n"
            "\n"
            "### ⚠️ Breaking changes\n"
            "\n"
            f"* (BREAKING) Drop an API by @e in {PR}/1\n"
            "\n"
            "### 🚀 New features\n"
            "\n"
            f"* (feat) Add a feature by @d in {PR}/2\n"
            "\n"
            "### 🐛 Bug fixes\n"
            "\n"
            f"* (fix) Fix a bug by @b in {PR}/3\n"
            "\n"
            "### 🧹 Housekeeping\n"
            "\n"
            f"* (chore) Bump deps by @a in {PR}/4\n"
            "\n"
            "### Other changes\n"
            "\n"
            f"* Untyped change by @c in {PR}/5\n"
            "\n"
            "## New Contributors\n"
            f"* @c made their first contribution in {PR}/5\n"
            "\n"
            "**Full Changelog**: https://github.com/openmrs/openmrs-esm-example/compare/v1.0.0...v1.1.0\n"
        )
        self.assertEqual(grn.group_notes(body), expected)

    def test_keeps_order_within_a_type(self):
        body = (
            "## What's Changed\n"
            f"* (fix) First by @a in {PR}/1\n"
            f"* (fix) Second by @a in {PR}/2\n"
        )
        result = grn.group_notes(body)
        self.assertLess(result.index("First"), result.index("Second"))

    def test_regroups_label_categories(self):
        body = (
            "## What's Changed\n"
            "### Labelled\n"
            f"* (fix) Fix a bug by @a in {PR}/1\n"
            "### Other Changes\n"
            f"* (feat) Add a feature by @a in {PR}/2\n"
        )
        result = grn.group_notes(body)
        self.assertNotIn("### Labelled", result)
        self.assertLess(result.index("New features"), result.index("Bug fixes"))

    def test_body_without_whats_changed_is_unchanged(self):
        body = "**Full Changelog**: https://github.com/openmrs/openmrs-esm-example/compare/v1.0.0...v1.0.1"
        self.assertEqual(grn.group_notes(body), body)


if __name__ == "__main__":
    unittest.main()
