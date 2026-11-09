#!/usr/bin/env python3
"""Record the packages a pre-release published, then wait until npm serves them.

A downstream build resolves `next` through `pacote`, which reads the abbreviated
package document. The registry's CDN caches that document for up to five minutes,
so a build dispatched straight after a publish can still resolve the previous
version. Waiting on the per-version endpoint would not help: it is served
uncached and answers as soon as the publish lands.

  record  run after the pre-release version is set; writes the `packages` output
  wait    reads PACKAGES (that output) and polls until every version is listed
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import publishable_packages, write_github_outputs

# The Accept header pacote sends; the CDN caches each Accept variant separately.
INSTALL_ACCEPT = "application/vnd.npm.install-v1+json; q=1.0, application/json; q=0.8, */*"

LISTED, NOT_LISTED, NO_ACCESS, UNREACHABLE = "listed", "not listed", "no access", "unreachable"


def listed_state(package, version, registry):
    """Whether the cached abbreviated document for `package` lists `version`."""
    url = f"{registry.rstrip('/')}/{urllib.parse.quote(package, safe='@')}"
    request = urllib.request.Request(url, headers={"Accept": INSTALL_ACCEPT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            document = json.load(response)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return NOT_LISTED
        return NO_ACCESS if e.code in (401, 403) else UNREACHABLE
    except (OSError, ValueError):
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


def wait(packages, timeout, interval, state=listed_state, sleep=time.sleep, clock=time.monotonic):
    """Poll until every package is listed; return the ones still pending at the deadline."""
    deadline = clock() + timeout
    pending = {f"{p['name']}@{p['version']}": p for p in packages}
    last = {}
    while True:
        for key, package in list(pending.items()):
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["record", "wait"])
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()

    if args.command == "record":
        record()
        return

    packages, error = parse_packages(os.environ.get("PACKAGES", ""))
    if error:
        sys.exit(f"::error::{error}")
    missing = wait(packages, args.timeout, args.interval)
    if missing:
        details = "; ".join(f"{key} ({state})" for key, state in missing.items())
        sys.exit(
            f"::error::Gave up after {args.timeout}s waiting for npm to serve {details}. "
            "The downstream build was not triggered, because it would resolve an older version."
        )


if __name__ == "__main__":
    main()
