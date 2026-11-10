#!/usr/bin/env python3
"""Record the packages a pre-release published, then wait until npm serves them.

A downstream build resolves `next` through `pacote`, which reads the abbreviated
package document, and npm's installer then reads the full one. The registry's CDN
caches each document separately for up to five minutes, so a build dispatched
straight after a publish can resolve the previous version, or resolve the new one
and then fail to install it.
Waiting on the per-version endpoint would not help: it is served uncached and
answers as soon as the publish lands.

`record` runs after the pre-release version is set and writes the `packages`
output. `wait` takes that output as PACKAGES, a JSON array of {name, version,
registry}, and polls every INTERVAL seconds for up to TIMEOUT.
"""

import gzip
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import publishable_packages, write_github_outputs

# The Accept headers pacote sends; the CDN caches each Accept variant separately.
INSTALL_ACCEPT = "application/vnd.npm.install-v1+json; q=1.0, application/json; q=0.8, */*"
FULL_ACCEPT = "application/json"
DOCUMENTS = (("abbreviated", INSTALL_ACCEPT), ("full", FULL_ACCEPT))

TIMEOUT = 900
INTERVAL = 10

LISTED, NOT_LISTED, NO_ACCESS, UNREACHABLE = "listed", "not listed", "no access", "unreachable"


def listed_state(package, version, registry):
    """LISTED once both cached documents for `package` list `version`, else the first one's shortfall."""
    for name, accept in DOCUMENTS:
        state = document_state(package, version, registry, accept)
        if state == NO_ACCESS:
            return state
        if state != LISTED:
            return f"{name} document {state}"
    return LISTED


def document_state(package, version, registry, accept):
    """Whether the cached document for `package` sent for `accept` lists `version`."""
    url = f"{registry.rstrip('/')}/{urllib.parse.quote(package, safe='@')}"
    request = urllib.request.Request(url, headers={"Accept": accept, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            document = json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return NOT_LISTED
        return NO_ACCESS if e.code in (401, 403) else UNREACHABLE
    except (OSError, ValueError, EOFError, zlib.error, http.client.HTTPException):
        return UNREACHABLE
    return LISTED if version in (document.get("versions") or {}) else NOT_LISTED


def record():
    """Warns rather than fails, so a listing problem never blocks the publish itself."""
    packages = publishable_packages()
    if packages is None:
        print("::warning::Could not list the packages this pre-release publishes, so the downstream build will not be triggered.")
        return
    if not packages:
        print("::warning::This repository has no publishable packages, so the downstream build will not be triggered.")
        return
    for package in packages:
        print(f"Recorded {package['name']}@{package['version']} on {package['registry'] or 'an unknown registry'}")
    write_github_outputs({"packages": json.dumps(packages)})


def parse_packages(raw):
    """The `record` output, or an error message naming what is wrong with it."""
    if not raw:
        return None, "The pre_release job recorded no package list; see the warning on its Record step."
    try:
        packages = json.loads(raw)
    except ValueError:
        return None, f"The pre_release job's package list is not valid JSON: {raw}"
    no_version = [p.get("name", "?") for p in packages if not p.get("version")]
    if no_version:
        return None, f"The pre_release job recorded no version for {', '.join(no_version)}."
    no_registry = [p["name"] for p in packages if not p.get("registry")]
    if no_registry:
        return None, f"The pre_release job could not tell which registry {', '.join(no_registry)} publishes to."
    return packages, None


def wait(packages, timeout=TIMEOUT, interval=INTERVAL, state=listed_state, sleep=time.sleep, clock=time.monotonic):
    """Poll until every package is listed; return the ones still pending at the deadline."""
    deadline = clock() + timeout
    pending = {f"{p['name']}@{p['version']}": p for p in packages}
    last = {}
    while True:
        for key, package in list(pending.items()):
            # Stop mid-sweep at the deadline, so slow requests cannot run into the step's own timeout.
            if key in last and clock() >= deadline:
                break
            last[key] = state(package["name"], package["version"], package["registry"])
            if last[key] == LISTED:
                print(f"✅ {key} is served by {package['registry']}")
                del pending[key]
            elif last[key] == NO_ACCESS:
                print(f"::warning::{package['registry']} needs credentials to read {package['name']}, so {key} is not waited for.")
                del pending[key]
        if not pending or clock() >= deadline:
            return {key: last[key] for key in pending}
        print(f"Waiting for {', '.join(pending)} ...")
        sleep(interval)


def wait_for_recorded(env):
    packages, error = parse_packages(env.get("PACKAGES", ""))
    if error:
        sys.exit(f"::error::{error}")
    missing = wait(packages)
    if missing:
        details = "; ".join(f"{key} ({state})" for key, state in missing.items())
        sys.exit(
            f"::error::Gave up after {TIMEOUT}s waiting for npm to serve {details}. "
            "The downstream build was not triggered, because it would resolve an older version."
        )


def main(argv):
    commands = {"record": lambda env: record(), "wait": wait_for_recorded}
    if len(argv) != 2 or argv[1] not in commands:
        sys.exit(f"usage: {os.path.basename(argv[0])} record|wait")
    commands[argv[1]](os.environ)


if __name__ == "__main__":
    main(sys.argv)
