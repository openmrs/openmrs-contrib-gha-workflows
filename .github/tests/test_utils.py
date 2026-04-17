#!/usr/bin/env python3
"""Tests for utils.py."""

import os
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import utils


class TestStripNs(unittest.TestCase):
    def test_removes_namespace(self):
        root = ET.fromstring(
            '<project xmlns="http://maven.apache.org/POM/4.0.0">'
            "<version>1.0</version>"
            "</project>"
        )
        utils.strip_ns(root)
        self.assertEqual(root.tag, "project")
        self.assertEqual(root.find("version").text, "1.0")

    def test_no_namespace(self):
        root = ET.fromstring("<project><version>1.0</version></project>")
        utils.strip_ns(root)
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
        utils.strip_ns(root)
        self.assertEqual(root.find("parent/version").text, "2.0")


class TestParsePom(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_valid_pom(self):
        pom_path = os.path.join(self.tmpdir, "pom.xml")
        with open(pom_path, "w") as f:
            f.write(
                textwrap.dedent("""\
                <project xmlns="http://maven.apache.org/POM/4.0.0">
                  <version>1.0.0</version>
                </project>""")
            )
        root = utils.parse_pom(pom_path)
        self.assertIsNotNone(root)
        # Namespace should be stripped
        self.assertEqual(root.tag, "project")
        self.assertEqual(root.findtext("version"), "1.0.0")

    def test_missing_file(self):
        root = utils.parse_pom("/nonexistent/pom.xml")
        self.assertIsNone(root)

    def test_invalid_xml(self):
        pom_path = os.path.join(self.tmpdir, "pom.xml")
        with open(pom_path, "w") as f:
            f.write("this is not xml")
        root = utils.parse_pom(pom_path)
        self.assertIsNone(root)


class TestWriteGithubOutputs(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self._old_github_output = os.environ.get("GITHUB_OUTPUT")

    def tearDown(self):
        if self._old_github_output is None:
            os.environ.pop("GITHUB_OUTPUT", None)
        else:
            os.environ["GITHUB_OUTPUT"] = self._old_github_output
        self._tmpdir.cleanup()

    def test_writes_to_file(self):
        out_path = os.path.join(self.tmpdir, "output")
        os.environ["GITHUB_OUTPUT"] = out_path
        utils.write_github_outputs({"key1": "val1", "key2": "val2"})
        with open(out_path) as f:
            content = f.read()
        self.assertIn("key1=val1", content)
        self.assertIn("key2=val2", content)

    def test_skips_none_values(self):
        out_path = os.path.join(self.tmpdir, "output")
        os.environ["GITHUB_OUTPUT"] = out_path
        utils.write_github_outputs({"present": "yes", "absent": None})
        with open(out_path) as f:
            content = f.read()
        self.assertIn("present=yes", content)
        self.assertNotIn("absent", content)

    def test_all_none_writes_nothing(self):
        out_path = os.path.join(self.tmpdir, "output")
        os.environ["GITHUB_OUTPUT"] = out_path
        utils.write_github_outputs({"a": None, "b": None})
        self.assertFalse(os.path.exists(out_path))

    def test_prints_to_stdout_without_github_output(self):
        os.environ.pop("GITHUB_OUTPUT", None)
        import io
        from unittest.mock import patch

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            utils.write_github_outputs({"key": "value"})
            self.assertIn("key=value", mock_out.getvalue())

    def test_multiline_value_uses_heredoc_syntax(self):
        out_path = os.path.join(self.tmpdir, "output")
        os.environ["GITHUB_OUTPUT"] = out_path
        utils.write_github_outputs({"paths": "a/dist\nb/dist"})
        with open(out_path) as f:
            content = f.read()
        self.assertIn("paths<<EOF", content)
        self.assertIn("a/dist\nb/dist", content)
        self.assertIn("EOF", content)
        # Should NOT use key=value format for multiline
        self.assertNotIn("paths=", content)

    def test_mixed_single_and_multiline_values(self):
        out_path = os.path.join(self.tmpdir, "output")
        os.environ["GITHUB_OUTPUT"] = out_path
        utils.write_github_outputs(
            {
                "simple": "value",
                "multi": "line1\nline2",
            }
        )
        with open(out_path) as f:
            content = f.read()
        self.assertIn("simple=value", content)
        self.assertIn("multi<<EOF", content)
        self.assertIn("line1\nline2", content)


if __name__ == "__main__":
    unittest.main()
