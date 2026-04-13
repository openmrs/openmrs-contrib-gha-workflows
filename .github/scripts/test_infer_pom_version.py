#!/usr/bin/env python3
"""Tests for infer-pom-version.py."""

import os
import sys
import textwrap
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
import importlib
from pom_utils import strip_ns

infer = importlib.import_module("infer-pom-version")


class TestFindProjectVersion(unittest.TestCase):
    def _parse(self, xml_str):
        root = ET.fromstring(textwrap.dedent(xml_str))
        strip_ns(root)
        return root

    def test_direct_version(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>1.2.3</version>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "1.2.3")

    def test_snapshot_version(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>2.0.0-SNAPSHOT</version>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "2.0.0-SNAPSHOT")

    def test_parent_version_fallback(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <parent>
                <version>3.1.0</version>
              </parent>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "3.1.0")

    def test_direct_version_takes_precedence(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>1.0.0</version>
              <parent>
                <version>2.0.0</version>
              </parent>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "1.0.0")

    def test_no_version(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <groupId>org.example</groupId>
            </project>""")
        self.assertIsNone(infer.find_project_version(root))

    def test_whitespace_stripped(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>  1.0.0  </version>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "1.0.0")

    def test_no_namespace(self):
        root = self._parse("""\
            <project>
              <version>4.5.6</version>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "4.5.6")


if __name__ == "__main__":
    unittest.main()
