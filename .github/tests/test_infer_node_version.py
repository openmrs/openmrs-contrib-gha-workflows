#!/usr/bin/env python3
"""Tests for infer-node-version.py."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import infer_node_version as infer


class TestInferNodeVersion(unittest.TestCase):
    def _write_package_json(self, content, tmpdir=None):
        if tmpdir is None:
            tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "package.json")
        with open(path, "w") as f:
            json.dump(content, f)
        return path

    def test_exact_version(self):
        path = self._write_package_json({"engines": {"node": "22"}})
        self.assertEqual(infer.infer_node_version(path), "22")

    def test_gte_constraint(self):
        path = self._write_package_json({"engines": {"node": ">=18"}})
        self.assertEqual(infer.infer_node_version(path), "18")

    def test_caret_constraint(self):
        path = self._write_package_json({"engines": {"node": "^18.0.0"}})
        self.assertEqual(infer.infer_node_version(path), "18")

    def test_tilde_constraint(self):
        path = self._write_package_json({"engines": {"node": "~20.10.0"}})
        self.assertEqual(infer.infer_node_version(path), "20")

    def test_range_constraint(self):
        path = self._write_package_json({"engines": {"node": ">=18 <22"}})
        self.assertEqual(infer.infer_node_version(path), "18")

    def test_no_engines(self):
        path = self._write_package_json({"name": "test"})
        self.assertIsNone(infer.infer_node_version(path))

    def test_no_node_in_engines(self):
        path = self._write_package_json({"engines": {"npm": ">=9"}})
        self.assertIsNone(infer.infer_node_version(path))

    def test_missing_file(self):
        self.assertIsNone(infer.infer_node_version("/nonexistent/package.json"))

    def test_invalid_json(self):
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "package.json")
        with open(path, "w") as f:
            f.write("not json")
        self.assertIsNone(infer.infer_node_version(path))

    def test_dot_x_version(self):
        path = self._write_package_json({"engines": {"node": "18.x"}})
        self.assertEqual(infer.infer_node_version(path), "18")

    def test_or_constraint(self):
        path = self._write_package_json({"engines": {"node": "18 || 20"}})
        self.assertEqual(infer.infer_node_version(path), "18")


if __name__ == "__main__":
    unittest.main()
