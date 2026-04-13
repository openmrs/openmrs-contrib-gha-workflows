#!/usr/bin/env python3
"""Tests for infer-maven-server-ids.py."""

import os
import sys
import textwrap
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
import importlib
from pom_utils import strip_ns

infer = importlib.import_module("infer-maven-server-ids")


class TestFindServerIds(unittest.TestCase):
    def _parse(self, xml_str):
        root = ET.fromstring(textwrap.dedent(xml_str))
        strip_ns(root)
        return root

    def test_both_ids(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <repository>
                  <id>openmrs-repo-modules</id>
                  <url>https://example.com/releases</url>
                </repository>
                <snapshotRepository>
                  <id>openmrs-repo-snapshots</id>
                  <url>https://example.com/snapshots</url>
                </snapshotRepository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertEqual(release_id, "openmrs-repo-modules")
        self.assertEqual(snapshot_id, "openmrs-repo-snapshots")

    def test_only_release(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <repository>
                  <id>my-releases</id>
                </repository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertEqual(release_id, "my-releases")
        self.assertIsNone(snapshot_id)

    def test_only_snapshot(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <snapshotRepository>
                  <id>my-snapshots</id>
                </snapshotRepository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertIsNone(release_id)
        self.assertEqual(snapshot_id, "my-snapshots")

    def test_no_distribution_management(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <groupId>org.example</groupId>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertIsNone(release_id)
        self.assertIsNone(snapshot_id)

    def test_no_namespace(self):
        root = self._parse("""\
            <project>
              <distributionManagement>
                <repository>
                  <id>releases</id>
                </repository>
                <snapshotRepository>
                  <id>snapshots</id>
                </snapshotRepository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertEqual(release_id, "releases")
        self.assertEqual(snapshot_id, "snapshots")

    def test_empty_ids(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <repository>
                  <id></id>
                </repository>
                <snapshotRepository>
                  <id></id>
                </snapshotRepository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertIsNone(release_id)
        self.assertIsNone(snapshot_id)


if __name__ == "__main__":
    unittest.main()
