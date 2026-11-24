#!/usr/bin/env python3
"""Tests for render_stale_messages.py."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import render_stale_messages as render

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "render_stale_messages.py")


class TestHumanizeDays(unittest.TestCase):
    def test_matches_dayjs(self):
        # dayjs 1.11 `from(now, true)` gives these on any date; it uses calendar months, so counts
        # near a month or year cutoff can vary by date there and are not tested.
        cases = {
            1: "a day",
            2: "2 days",
            25: "25 days",
            26: "a month",
            30: "a month",
            45: "a month",
            46: "2 months",
            150: "5 months",
            180: "6 months",
            300: "10 months",
            400: "a year",
            600: "2 years",
        }
        for days, expected in cases.items():
            with self.subTest(days=days):
                self.assertEqual(render.humanize_days(days), expected)

    def test_zero_days(self):
        self.assertEqual(render.humanize_days(0), "a few seconds")


class TestRender(unittest.TestCase):
    def test_replaces_placeholders(self):
        template = "{time-before-stale}, {time-before-close}, {inactive-time-at-close}"
        self.assertEqual(render.render(template, 150, 30), "5 months, a month, 6 months")

    def test_leaves_other_braces(self):
        template = "{unknown} {{time-before-stale}}"
        self.assertEqual(render.render(template, 150, 30), "{unknown} {5 months}")


class TestMain(unittest.TestCase):
    def test_writes_multiline_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "output")
            env = dict(
                os.environ,
                GITHUB_OUTPUT=output,
                DAYS_BEFORE_STALE="150",
                DAYS_BEFORE_CLOSE="30",
                STALE_PR_MESSAGE="Stale after {time-before-stale}.\nClosing in {time-before-close}.\n",
                CLOSE_PR_MESSAGE="Closed after {inactive-time-at-close}.",
            )
            subprocess.run(
                [sys.executable, SCRIPT],
                env=env,
                check=True,
            )
            with open(output) as f:
                self.assertEqual(
                    f.read(),
                    "stale-pr-message<<EOF\nStale after 5 months.\nClosing in a month.\n\nEOF\n"
                    "close-pr-message=Closed after 6 months.\n",
                )


if __name__ == "__main__":
    unittest.main()
