#!/usr/bin/env python3
"""Tests for utils.py."""

import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from unittest.mock import patch

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


class TestYarnWorkspaces(unittest.TestCase):
    """yarn is the authority on which workspaces exist."""

    def _result(self, stdout):
        class R:
            pass

        r = R()
        r.stdout = stdout
        return r

    def test_parses_ndjson(self):
        out = '{"location":".","name":"@o/root"}\n{"location":"packages/a","name":"@o/a"}\n'
        with patch.object(utils.subprocess, "run", return_value=self._result(out)):
            self.assertEqual(
                utils.yarn_workspaces(),
                [
                    {"location": ".", "name": "@o/root"},
                    {"location": "packages/a", "name": "@o/a"},
                ],
            )

    def test_skips_blank_and_malformed_lines(self):
        out = '{"location":"packages/a"}\n\nnot json\n'
        with patch.object(utils.subprocess, "run", return_value=self._result(out)):
            self.assertEqual(utils.yarn_workspaces(), [{"location": "packages/a"}])

    def test_passes_extra_args_through(self):
        with patch.object(
            utils.subprocess, "run", return_value=self._result("")
        ) as run:
            utils.yarn_workspaces(("--no-private",))
        self.assertIn("--no-private", run.call_args[0][0])

    def test_degrades_to_empty_when_yarn_is_missing(self):
        with patch.object(utils.subprocess, "run", side_effect=OSError("no yarn")):
            self.assertEqual(utils.yarn_workspaces(), [])

    def test_degrades_to_empty_when_yarn_fails(self):
        err = utils.subprocess.CalledProcessError(1, "yarn")
        with patch.object(utils.subprocess, "run", side_effect=err):
            self.assertEqual(utils.yarn_workspaces(), [])


class TestPublishablePackageNames(unittest.TestCase):
    """The root is excluded by the publish command, so the report must match."""

    def test_drops_root_when_other_workspaces_exist(self):
        ws = [
            {"location": ".", "name": "@o/root"},
            {"location": "packages/a", "name": "@o/a"},
            {"location": "packages/b", "name": "@o/b"},
        ]
        self.assertEqual(utils.publishable_package_names(ws), ["@o/a", "@o/b"])

    def test_keeps_root_for_a_single_package_repo(self):
        ws = [{"location": ".", "name": "@o/solo"}]
        self.assertEqual(utils.publishable_package_names(ws), ["@o/solo"])

    def test_private_root_already_filtered_by_yarn(self):
        ws = [{"location": "packages/a", "name": "@o/a"}]
        self.assertEqual(utils.publishable_package_names(ws), ["@o/a"])

    def test_ignores_entries_without_a_name(self):
        ws = [{"location": "packages/a"}, {"location": "packages/b", "name": "@o/b"}]
        self.assertEqual(utils.publishable_package_names(ws), ["@o/b"])

    def test_empty_input(self):
        self.assertEqual(utils.publishable_package_names([]), [])


class TestNpmVersionState(unittest.TestCase):
    def _respond(self, body=None, status=200, error=None):
        if error is None and status != 200:
            error = urllib.error.HTTPError("url", status, "err", {}, None)
        if error is not None:
            return patch.object(utils.urllib.request, "urlopen", side_effect=error)
        response = io.BytesIO(json.dumps(body).encode())
        response.__enter__ = lambda self=response: self
        response.__exit__ = lambda *a: None
        return patch.object(utils.urllib.request, "urlopen", return_value=response)

    def test_live_on_exact_version(self):
        with self._respond({"name": "@o/a", "version": "9.9.9"}):
            self.assertEqual(utils.npm_version_state("@o/a", "9.9.9"), "live")

    def test_missing_on_404(self):
        with self._respond(status=404):
            self.assertEqual(utils.npm_version_state("@o/a", "9.9.9"), "missing")

    def test_unknown_on_other_http_errors(self):
        for status in (401, 429, 503):
            with self._respond(status=status):
                self.assertEqual(utils.npm_version_state("@o/a", "9.9.9"), "unknown")

    def test_unknown_on_network_error(self):
        with self._respond(error=urllib.error.URLError("down")):
            self.assertEqual(utils.npm_version_state("@o/a", "9.9.9"), "unknown")

    def test_unknown_on_unexpected_body(self):
        with self._respond({"version": "1.0.0"}):
            self.assertEqual(utils.npm_version_state("@o/a", "9.9.9"), "unknown")

    def test_requests_the_per_version_endpoint_with_token(self):
        with self._respond({"version": "9.9.9"}) as urlopen:
            utils.npm_version_state("@o/a", "9.9.9", token="secret")
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://registry.npmjs.org/@o%2Fa/9.9.9")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
