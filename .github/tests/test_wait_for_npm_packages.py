#!/usr/bin/env python3
"""Tests for wait_for_npm_packages.py."""

import gzip
import http.client
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
TARBALL = NPMJS + "/@o/a/-/a-1.0.1-pre.7.tgz"


def pkg(name="@o/a", version="1.0.1-pre.7", registry=NPMJS):
    return {"name": name, "version": version, "registry": registry}


class FakeResponse(io.BytesIO):
    def __init__(self, body, encoding=None):
        super().__init__(body)
        self.headers = {"Content-Encoding": encoding} if encoding else {}


class UnreadResponse(FakeResponse):
    was_read = False

    def read(self, *args):
        self.was_read = True
        return super().read(*args)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestDocumentState(unittest.TestCase):
    def fetch(self, response=None, error=None, gzipped=False):
        def urlopen(request, timeout):
            self.request = request
            if error:
                raise error
            body = json.dumps(response).encode()
            return FakeResponse(gzip.compress(body), "gzip") if gzipped else FakeResponse(body)

        with patch.object(waiter.urllib.request, "urlopen", urlopen):
            self.state, self.entry = waiter.document_state("@o/a", "1.0.1-pre.7", NPMJS + "/", waiter.INSTALL_ACCEPT)
        return self.state

    def http_error(self, code):
        return urllib.error.HTTPError(NPMJS, code, "", {}, None)

    def test_reads_the_document_for_the_accept_it_is_given(self):
        self.fetch({"versions": {}})
        self.assertEqual(self.request.full_url, "https://registry.npmjs.org/@o%2Fa")
        self.assertEqual(
            self.request.get_header("Accept"),
            "application/vnd.npm.install-v1+json; q=1.0, application/json; q=0.8, */*",
        )
        self.assertEqual(self.request.get_header("Accept-encoding"), "gzip")

    def test_reads_a_gzipped_document(self):
        self.assertEqual(self.fetch({"versions": {"1.0.1-pre.7": {}}}, gzipped=True), waiter.LISTED)
        self.assertEqual(self.fetch({"versions": {"1.0.1-pre.6": {}}}, gzipped=True), waiter.NOT_LISTED)

    def test_listed_when_the_document_has_the_version(self):
        self.assertEqual(self.fetch({"versions": {"1.0.1-pre.7": {}}}), waiter.LISTED)

    def test_returns_the_entry_for_the_version(self):
        entry = {"dist": {"tarball": TARBALL}}
        self.fetch({"versions": {"1.0.1-pre.6": {}, "1.0.1-pre.7": entry}})
        self.assertEqual(self.entry, entry)
        self.fetch({"versions": {"1.0.1-pre.6": {}}})
        self.assertIsNone(self.entry)

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
        self.assertEqual(self.fetch(error=http.client.BadStatusLine("")), waiter.UNREACHABLE)
        for body in (FakeResponse(b"<html>"), FakeResponse(b"not gzip", "gzip"),
                     FakeResponse(gzip.compress(b"{}")[:-4], "gzip"),
                     FakeResponse(gzip.compress(b"{}")[:10] + b"\xff" * 20, "gzip")):
            with self.subTest(body=body.getvalue()), \
                    patch.object(waiter.urllib.request, "urlopen", lambda r, timeout: body):
                self.assertEqual(waiter.document_state("@o/a", "1", NPMJS, waiter.INSTALL_ACCEPT)[0], waiter.UNREACHABLE)

    def test_unreachable_when_the_body_is_cut_short(self):
        class CutShort(FakeResponse):
            def read(self, *args):
                raise http.client.IncompleteRead(b'{"vers', 100)

        with patch.object(waiter.urllib.request, "urlopen", lambda r, timeout: CutShort(b"")):
            self.assertEqual(waiter.document_state("@o/a", "1", NPMJS, waiter.INSTALL_ACCEPT)[0], waiter.UNREACHABLE)


class TestTarballState(unittest.TestCase):
    def fetch(self, error=None):
        def urlopen(request, timeout):
            self.request = request
            if error:
                raise error
            self.response = UnreadResponse(b"tarball")
            return self.response

        with patch.object(waiter.urllib.request, "urlopen", urlopen):
            return waiter.tarball_state(TARBALL)

    def http_error(self, code):
        return urllib.error.HTTPError(TARBALL, code, "", {}, None)

    def test_gets_the_tarball_without_reading_it(self):
        self.assertEqual(self.fetch(), waiter.LISTED)
        self.assertEqual(self.request.full_url, TARBALL)
        self.assertEqual(self.request.get_method(), "GET")
        self.assertFalse(self.response.was_read)

    def test_not_found_until_the_tarball_is_served(self):
        self.assertEqual(self.fetch(error=self.http_error(404)), waiter.NOT_FOUND)

    def test_no_access_when_the_registry_wants_credentials(self):
        self.assertEqual(self.fetch(error=self.http_error(401)), waiter.NO_ACCESS)
        self.assertEqual(self.fetch(error=self.http_error(403)), waiter.NO_ACCESS)

    def test_unreachable_on_server_errors_and_dropped_connections(self):
        self.assertEqual(self.fetch(error=self.http_error(503)), waiter.UNREACHABLE)
        self.assertEqual(self.fetch(error=OSError("reset")), waiter.UNREACHABLE)
        self.assertEqual(self.fetch(error=http.client.RemoteDisconnected("")), waiter.UNREACHABLE)
        self.assertEqual(self.fetch(error=http.client.BadStatusLine("")), waiter.UNREACHABLE)


class TestListedState(unittest.TestCase):
    def check(self, abbreviated, full, tarball=waiter.LISTED, entry=None):
        calls = []
        entry = {"dist": {"tarball": TARBALL}} if entry is None else entry

        def document_state(package, version, registry, accept):
            calls.append(accept)
            state = abbreviated if accept == waiter.INSTALL_ACCEPT else full
            return state, entry if state == waiter.LISTED else None

        def tarball_state(url):
            calls.append(url)
            return tarball

        with patch.object(waiter, "document_state", document_state), \
                patch.object(waiter, "tarball_state", tarball_state):
            return waiter.listed_state("@o/a", "1.0.1-pre.7", NPMJS), calls

    def test_reads_the_abbreviated_document_then_the_full_one_then_the_tarball(self):
        self.assertEqual(self.check(waiter.LISTED, waiter.LISTED),
                         (waiter.LISTED, [waiter.INSTALL_ACCEPT, "application/json", TARBALL]))

    def test_not_listed_until_the_full_document_has_it_too(self):
        def urlopen(request, timeout):
            full = request.get_header("Accept") == "application/json"
            return FakeResponse(json.dumps({"versions": {} if full else {"1.0.1-pre.7": {}}}).encode())

        with patch.object(waiter.urllib.request, "urlopen", urlopen):
            self.assertEqual(waiter.listed_state("@o/a", "1.0.1-pre.7", NPMJS), "full document not listed")

    def test_not_listed_until_the_tarball_downloads(self):
        tarball_served = False

        def urlopen(request, timeout):
            if request.full_url == TARBALL:
                if not tarball_served:
                    raise urllib.error.HTTPError(request.full_url, 404, "", {}, None)
                return FakeResponse(b"")
            return FakeResponse(json.dumps({"versions": {"1.0.1-pre.7": {"dist": {"tarball": TARBALL}}}}).encode())

        with patch.object(waiter.urllib.request, "urlopen", urlopen):
            self.assertEqual(waiter.listed_state("@o/a", "1.0.1-pre.7", NPMJS), "tarball not found")
            tarball_served = True
            self.assertEqual(waiter.listed_state("@o/a", "1.0.1-pre.7", NPMJS), waiter.LISTED)

    def test_asks_for_the_tarball_only_once_both_documents_list_the_version(self):
        self.assertEqual(self.check(waiter.NOT_LISTED, waiter.LISTED)[1], [waiter.INSTALL_ACCEPT])
        self.assertEqual(self.check(waiter.LISTED, waiter.NOT_LISTED)[1], [waiter.INSTALL_ACCEPT, "application/json"])

    def test_names_what_is_behind(self):
        self.assertEqual(self.check(waiter.NOT_LISTED, waiter.LISTED)[0], "abbreviated document not listed")
        self.assertEqual(self.check(waiter.LISTED, waiter.UNREACHABLE)[0], "full document unreachable")
        self.assertEqual(self.check(waiter.LISTED, waiter.LISTED, waiter.NOT_FOUND)[0], "tarball not found")
        self.assertEqual(self.check(waiter.LISTED, waiter.LISTED, waiter.UNREACHABLE)[0], "tarball unreachable")

    def test_listed_without_a_tarball_check_when_the_documents_name_no_tarball(self):
        self.assertEqual(self.check(waiter.LISTED, waiter.LISTED, entry={}),
                         (waiter.LISTED, [waiter.INSTALL_ACCEPT, "application/json"]))

    def test_no_access_to_either_document_or_the_tarball_is_not_waited_for(self):
        self.assertEqual(self.check(waiter.NO_ACCESS, waiter.LISTED)[0], waiter.NO_ACCESS)
        self.assertEqual(self.check(waiter.LISTED, waiter.NO_ACCESS)[0], waiter.NO_ACCESS)
        self.assertEqual(self.check(waiter.LISTED, waiter.LISTED, waiter.NO_ACCESS)[0], waiter.NO_ACCESS)


class TestWait(unittest.TestCase):
    def run_wait(self, states, timeout=60, packages=None):
        clock = FakeClock()
        calls = []

        def state(name, version, registry):
            calls.append(name)
            return states[name].pop(0) if len(states[name]) > 1 else states[name][0]

        missing = waiter.wait(packages or [pkg(n) for n in states], timeout, 10, state, clock.sleep, clock)
        return missing, calls, clock.now

    def test_stops_at_the_deadline_rather_than_finishing_a_slow_sweep(self):
        clock = FakeClock()

        def slow_state(name, version, registry):
            clock.now += 20
            return waiter.NOT_LISTED

        packages = [pkg("@o/a"), pkg("@o/b"), pkg("@o/c")]
        missing = waiter.wait(packages, 70, 10, slow_state, clock.sleep, clock)

        self.assertEqual(set(missing), {"@o/a@1.0.1-pre.7", "@o/b@1.0.1-pre.7", "@o/c@1.0.1-pre.7"})
        self.assertEqual(clock.now, 70)

    def test_stops_at_the_deadline_within_the_first_sweep_naming_what_went_unchecked(self):
        clock = FakeClock()

        def slow_state(name, version, registry):
            clock.now += 60
            return waiter.UNREACHABLE

        packages = [pkg("@o/a"), pkg("@o/b"), pkg("@o/c")]
        missing = waiter.wait(packages, 100, 10, slow_state, clock.sleep, clock)

        self.assertEqual(
            missing,
            {"@o/a@1.0.1-pre.7": waiter.UNREACHABLE, "@o/b@1.0.1-pre.7": waiter.UNREACHABLE, "@o/c@1.0.1-pre.7": "not checked"},
        )
        self.assertEqual(clock.now, 120)

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
        waiter.wait([pkg("@o/a", "1.0.0"), pkg("@o/b", "2.0.0")], 60, 10,
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
