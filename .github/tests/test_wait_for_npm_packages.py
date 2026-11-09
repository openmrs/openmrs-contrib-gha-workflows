#!/usr/bin/env python3
"""Tests for wait_for_npm_packages.py."""

import gzip
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import wait_for_npm_packages as waiter

NPMJS = "https://registry.npmjs.org"


def pkg(name="@o/a", version="1.0.1-pre.7", registry=NPMJS):
    return {"name": name, "version": version, "registry": registry}


class FakeResponse(io.BytesIO):
    def __init__(self, body, encoding=None):
        super().__init__(body)
        self.headers = {"Content-Encoding": encoding} if encoding else {}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestListedState(unittest.TestCase):
    def fetch(self, response=None, error=None, gzipped=False):
        def urlopen(request, timeout):
            self.request = request
            if error:
                raise error
            body = json.dumps(response).encode()
            return FakeResponse(gzip.compress(body), "gzip") if gzipped else FakeResponse(body)

        with patch.object(waiter.urllib.request, "urlopen", urlopen):
            return waiter.listed_state("@o/a", "1.0.1-pre.7", NPMJS + "/")

    def http_error(self, code):
        return urllib.error.HTTPError(NPMJS, code, "", {}, None)

    def test_reads_the_abbreviated_document_pacote_reads(self):
        self.fetch({"versions": {}})
        self.assertEqual(self.request.full_url, "https://registry.npmjs.org/@o%2Fa")
        self.assertEqual(self.request.get_header("Accept"), waiter.INSTALL_ACCEPT)
        self.assertEqual(self.request.get_header("Accept-encoding"), "gzip")

    def test_reads_a_gzipped_document(self):
        self.assertEqual(self.fetch({"versions": {"1.0.1-pre.7": {}}}, gzipped=True), waiter.LISTED)
        self.assertEqual(self.fetch({"versions": {"1.0.1-pre.6": {}}}, gzipped=True), waiter.NOT_LISTED)

    def test_listed_when_the_document_has_the_version(self):
        self.assertEqual(self.fetch({"versions": {"1.0.1-pre.7": {}}}), waiter.LISTED)

    def test_not_listed_while_the_cached_document_is_older(self):
        self.assertEqual(self.fetch({"versions": {"1.0.1-pre.6": {}}}), waiter.NOT_LISTED)

    def test_not_listed_for_a_package_npm_does_not_have_yet(self):
        self.assertEqual(self.fetch(error=self.http_error(404)), waiter.NOT_LISTED)

    def test_no_access_when_the_registry_wants_credentials(self):
        self.assertEqual(self.fetch(error=self.http_error(401)), waiter.NO_ACCESS)
        self.assertEqual(self.fetch(error=self.http_error(403)), waiter.NO_ACCESS)

    def test_unreachable_on_server_errors_and_bad_json(self):
        self.assertEqual(self.fetch(error=self.http_error(503)), waiter.UNREACHABLE)
        self.assertEqual(self.fetch(error=OSError("reset")), waiter.UNREACHABLE)
        for body in (FakeResponse(b"<html>"), FakeResponse(b"not gzip", "gzip"),
                     FakeResponse(gzip.compress(b"{}")[:-4], "gzip"),
                     FakeResponse(gzip.compress(b"{}")[:10] + b"\xff" * 20, "gzip")):
            with self.subTest(body=body.getvalue()), \
                    patch.object(waiter.urllib.request, "urlopen", lambda r, timeout: body):
                self.assertEqual(waiter.listed_state("@o/a", "1", NPMJS), waiter.UNREACHABLE)


class TestWait(unittest.TestCase):
    def run_wait(self, states, timeout=60, packages=None):
        clock = FakeClock()
        calls = []

        def state(name, version, registry):
            calls.append(name)
            return states[name].pop(0) if len(states[name]) > 1 else states[name][0]

        missing = waiter.wait(packages or [pkg(n) for n in states], timeout, 10, state, clock.sleep, clock)
        return missing, calls, clock.now

    def test_returns_straight_away_when_everything_is_listed(self):
        missing, _, elapsed = self.run_wait({"@o/a": [waiter.LISTED], "@o/b": [waiter.LISTED]})
        self.assertEqual(missing, {})
        self.assertEqual(elapsed, 0)

    def test_keeps_polling_only_what_is_still_pending(self):
        missing, calls, elapsed = self.run_wait({
            "@o/a": [waiter.LISTED],
            "@o/b": [waiter.NOT_LISTED, waiter.UNREACHABLE, waiter.LISTED],
        })
        self.assertEqual(missing, {})
        self.assertEqual(calls, ["@o/a", "@o/b", "@o/b", "@o/b"])
        self.assertEqual(elapsed, 20)

    def test_gives_up_at_the_deadline_naming_each_package_and_why(self):
        missing, _, elapsed = self.run_wait(
            {"@o/a": [waiter.NOT_LISTED], "@o/b": [waiter.UNREACHABLE], "@o/c": [waiter.LISTED]}, timeout=30)
        self.assertEqual(missing, {"@o/a@1.0.1-pre.7": waiter.NOT_LISTED, "@o/b@1.0.1-pre.7": waiter.UNREACHABLE})
        self.assertEqual(elapsed, 30)

    def test_does_not_wait_on_a_registry_it_cannot_read(self):
        missing, calls, _ = self.run_wait({"@o/a": [waiter.NO_ACCESS]})
        self.assertEqual(missing, {})
        self.assertEqual(calls, ["@o/a"])

    def test_each_package_is_checked_at_its_own_version(self):
        seen = []
        waiter.wait([pkg("@o/a", "1.0.0"), pkg("@o/b", "2.0.0")], 0, 10,
                    lambda n, v, r: seen.append((n, v)) or waiter.LISTED, lambda s: None, lambda: 0)
        self.assertEqual(seen, [("@o/a", "1.0.0"), ("@o/b", "2.0.0")])


class TestParsePackages(unittest.TestCase):
    def test_parses_the_recorded_list(self):
        packages, error = waiter.parse_packages(json.dumps([pkg()]))
        self.assertEqual(packages, [pkg()])
        self.assertIsNone(error)

    def test_each_problem_has_its_own_message(self):
        cases = {
            "": "recorded no package list",
            "not json": "not valid JSON",
            json.dumps([pkg(version="")]): "no version for @o/a",
            json.dumps([pkg(registry=None)]): "which registry @o/a",
        }
        for raw, message in cases.items():
            with self.subTest(raw=raw):
                packages, error = waiter.parse_packages(raw)
                self.assertIsNone(packages)
                self.assertIn(message, error)


class TestRecord(unittest.TestCase):
    def test_writes_the_packages_output(self):
        with tempfile.NamedTemporaryFile("r", delete=False) as out:
            path = out.name
        self.addCleanup(os.unlink, path)
        with patch.object(waiter, "publishable_packages", return_value=[pkg()]), \
                patch.dict(os.environ, {"GITHUB_OUTPUT": path}), patch("sys.stdout", io.StringIO()):
            waiter.record()
        with open(path) as f:
            self.assertEqual(f.read(), f"packages={json.dumps([pkg()])}\n")

    def test_warns_without_failing_the_publish_when_there_is_nothing_to_record(self):
        for listed, message in ((None, "Could not list"), ([], "no publishable packages")):
            with self.subTest(listed=listed), patch.object(waiter, "publishable_packages", return_value=listed), \
                    patch.dict(os.environ, {"GITHUB_OUTPUT": ""}), patch("sys.stdout", io.StringIO()) as out:
                waiter.record()
            self.assertIn("::warning::", out.getvalue())
            self.assertIn(message, out.getvalue())
            self.assertNotIn("packages=", out.getvalue())


class TestMain(unittest.TestCase):
    def run_main(self, raw, missing):
        with patch.dict(os.environ, {"PACKAGES": raw}), patch.object(waiter, "wait", return_value=missing) as waited:
            waiter.main(["wait_for_npm_packages.py", "wait"])
        return waited

    def test_succeeds_once_everything_is_listed(self):
        self.assertEqual(self.run_main(json.dumps([pkg()]), {}).call_args[0], ([pkg()],))

    def test_fails_naming_what_never_appeared(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_main(json.dumps([pkg()]), {"@o/a@1.0.1-pre.7": waiter.NOT_LISTED})
        self.assertIn("@o/a@1.0.1-pre.7 (not listed)", str(raised.exception))
        self.assertIn("was not triggered", str(raised.exception))

    def test_fails_before_polling_on_a_bad_package_list(self):
        with patch.dict(os.environ, {"PACKAGES": ""}), patch.object(waiter, "wait") as waited, \
                self.assertRaises(SystemExit) as raised:
            waiter.main(["wait_for_npm_packages.py", "wait"])
        waited.assert_not_called()
        self.assertIn("recorded no package list", str(raised.exception))

    def test_rejects_an_unknown_command(self):
        for argv in (["w.py"], ["w.py", "poll"], ["w.py", "wait", "extra"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as raised:
                waiter.main(argv)
            self.assertIn("usage: w.py record|wait", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
