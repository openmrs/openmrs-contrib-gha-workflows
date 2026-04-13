#!/usr/bin/env python3
"""Tests for pom_utils.py."""

import os
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
import pom_utils


class TestStripNs(unittest.TestCase):
    def test_removes_namespace(self):
        root = ET.fromstring(
            '<project xmlns="http://maven.apache.org/POM/4.0.0">'
            "<version>1.0</version>"
            "</project>"
        )
        pom_utils.strip_ns(root)
        self.assertEqual(root.tag, "project")
        self.assertEqual(root.find("version").text, "1.0")

    def test_no_namespace(self):
        root = ET.fromstring("<project><version>1.0</version></project>")
        pom_utils.strip_ns(root)
        self.assertEqual(root.tag, "project")

    def test_nested_namespaces(self):
        root = ET.fromstring(
            textwrap.dedent("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <parent>
                <version>2.0</version>
              </parent>
            </project>""")
        )
        pom_utils.strip_ns(root)
        self.assertEqual(root.find("parent/version").text, "2.0")


class TestParsePom(unittest.TestCase):
    def test_valid_pom(self):
        tmpdir = tempfile.mkdtemp()
        pom_path = os.path.join(tmpdir, "pom.xml")
        with open(pom_path, "w") as f:
            f.write(
                textwrap.dedent("""\
                <project xmlns="http://maven.apache.org/POM/4.0.0">
                  <version>1.0.0</version>
                </project>""")
            )
        root = pom_utils.parse_pom(pom_path)
        self.assertIsNotNone(root)
        # Namespace should be stripped
        self.assertEqual(root.tag, "project")
        self.assertEqual(root.findtext("version"), "1.0.0")

    def test_missing_file(self):
        root = pom_utils.parse_pom("/nonexistent/pom.xml")
        self.assertIsNone(root)

    def test_invalid_xml(self):
        tmpdir = tempfile.mkdtemp()
        pom_path = os.path.join(tmpdir, "pom.xml")
        with open(pom_path, "w") as f:
            f.write("this is not xml")
        root = pom_utils.parse_pom(pom_path)
        self.assertIsNone(root)


class TestWriteGithubOutputs(unittest.TestCase):
    def test_writes_to_file(self):
        tmpdir = tempfile.mkdtemp()
        out_path = os.path.join(tmpdir, "output")
        old_env = os.environ.get("GITHUB_OUTPUT")
        try:
            os.environ["GITHUB_OUTPUT"] = out_path
            pom_utils.write_github_outputs({"key1": "val1", "key2": "val2"})
            with open(out_path) as f:
                content = f.read()
            self.assertIn("key1=val1", content)
            self.assertIn("key2=val2", content)
        finally:
            if old_env is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = old_env

    def test_skips_none_values(self):
        tmpdir = tempfile.mkdtemp()
        out_path = os.path.join(tmpdir, "output")
        old_env = os.environ.get("GITHUB_OUTPUT")
        try:
            os.environ["GITHUB_OUTPUT"] = out_path
            pom_utils.write_github_outputs({"present": "yes", "absent": None})
            with open(out_path) as f:
                content = f.read()
            self.assertIn("present=yes", content)
            self.assertNotIn("absent", content)
        finally:
            if old_env is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = old_env

    def test_all_none_writes_nothing(self):
        tmpdir = tempfile.mkdtemp()
        out_path = os.path.join(tmpdir, "output")
        old_env = os.environ.get("GITHUB_OUTPUT")
        try:
            os.environ["GITHUB_OUTPUT"] = out_path
            pom_utils.write_github_outputs({"a": None, "b": None})
            self.assertFalse(os.path.exists(out_path))
        finally:
            if old_env is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = old_env

    def test_prints_to_stdout_without_github_output(
        self,
    ):
        old_env = os.environ.get("GITHUB_OUTPUT")
        try:
            os.environ.pop("GITHUB_OUTPUT", None)
            import io
            from unittest.mock import patch

            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                pom_utils.write_github_outputs({"key": "value"})
                self.assertIn("key=value", mock_out.getvalue())
        finally:
            if old_env is not None:
                os.environ["GITHUB_OUTPUT"] = old_env


if __name__ == "__main__":
    unittest.main()
