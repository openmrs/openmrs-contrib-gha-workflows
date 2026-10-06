#!/usr/bin/env python3
"""Work out what a failed release left behind, and whether to roll it back.

Runs after a tagged release that created its tag (or reused one, in
publish-only mode) fails without a successful publish. Establishes:

- whether the tag reached the remote. A push step that did not succeed may
  still have landed (e.g. the connection dropped after GitHub accepted it), so
  the remote is checked for the exact tag object this run created; only this
  run can have pushed that object.
- for each publishable workspace, whether it is live, missing or unknown on
  npm (see `npm_version_state`).

Rolling back, i.e. deleting the pushed tag and reverting the release commit,
is only safe when this run pushed the tag and npm has none of the packages.
Anything live or unknown means the tag may match what is on npm, so it must
stay.

Configuration comes from the environment:

  RELEASE_TAG      tag this run created or reused
  RELEASE_VERSION  version behind the release tag
  PUSH_OUTCOME     outcome of the push step ("success", "failure", ...)
  PUBLISH_ONLY     "true" when this run reused an existing tag
  PUBLISH_RAN      "true" when the publish step started; the registry is then
                   given time to serve anything that failed run uploaded
  NODE_AUTH_TOKEN  optional npm token, to see restricted packages
  STATE_FILE       where to write the result as JSON, for the report

Writes `push` (pushed, not_pushed, unknown or preexisting) and
`safe_to_roll_back=true|false` to GITHUB_OUTPUT.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    npm_version_state,
    publishable_package_names,
    write_github_outputs,
    yarn_workspaces,
)

SETTLE_SECONDS = 60


def git(*args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def tag_landed(tag):
    """Return "pushed", "not_pushed" or "unknown" for this run's local `tag`.

    The tag counts as pushed only if the remote holds the same annotated tag
    object, so a tag someone else pushed under the same name is never mistaken
    for this run's.
    """
    try:
        local = git("rev-parse", f"refs/tags/{tag}")
        remote = git("ls-remote", "--tags", "origin", f"refs/tags/{tag}")
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"::warning::Could not compare tag {tag} with the remote: {e}", file=sys.stderr)
        return "unknown"
    if not remote:
        return "not_pushed"
    return "pushed" if remote.split()[0] == local else "unknown"


def push_state(tag, push_outcome, publish_only, landed=None):
    """Return "preexisting", "pushed", "not_pushed" or "unknown"."""
    if publish_only:
        return "preexisting"
    if push_outcome == "success":
        return "pushed"
    return (landed or tag_landed)(tag)


def classify(packages, version, probe=None):
    """Return {"live": [...], "missing": [...], "unknown": [...]} for `packages`."""
    probe = probe or npm_version_state
    state = {"live": [], "missing": [], "unknown": []}
    for package in packages:
        state[probe(package, version)].append(package)
    return state


def safe_to_roll_back(state):
    """True only if this run pushed the tag and npm has none of the packages."""
    return (
        state["push"] == "pushed"
        and bool(state["missing"])
        and not state["live"]
        and not state["unknown"]
    )


def main():
    env = os.environ.get
    version = env("RELEASE_VERSION", "").strip()
    push = push_state(
        env("RELEASE_TAG", "").strip(),
        env("PUSH_OUTCOME", ""),
        env("PUBLISH_ONLY", "") == "true",
    )

    # Publishing only ever follows a successful push, so with the tag not (or
    # not provably) on the remote there is nothing on npm to look for.
    state = {"push": push, "live": [], "missing": [], "unknown": []}
    if push in ("pushed", "preexisting"):
        if env("PUBLISH_RAN", "") == "true":
            time.sleep(SETTLE_SECONDS)
        token = env("NODE_AUTH_TOKEN") or None
        packages = publishable_package_names(yarn_workspaces(("--no-private",)))
        state.update(classify(packages, version, probe=lambda p, v: npm_version_state(p, v, token)))

    with open(os.environ["STATE_FILE"], "w") as f:
        json.dump(state, f)
    write_github_outputs({
        "push": push,
        "safe_to_roll_back": str(safe_to_roll_back(state)).lower(),
    })


if __name__ == "__main__":
    main()
