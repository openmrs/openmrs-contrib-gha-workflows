#!/usr/bin/env python3
"""Select the previous release tag for release-note generation.

The create-release-notes action defaults to GitHub's repo-wide latest
release as the comparison base. That is wrong whenever a repo has more than
one active release line: after 3.0.0 ships, releasing 2.5.4 off the 2.x
branch would otherwise diff 3.0.0...2.5.4. This script picks a sane base:
the greatest STABLE release that shares this artifact's tag prefix and is
strictly older than the version being released.

Candidate tags are read from stdin, one per line (e.g. the output of
`git tag --list "<prefix>*"`). Configuration comes from the environment:

  RELEASE_VERSION  semver being released, e.g. "2.5.4"
  TAG_PREFIX       prefix shared by this artifact's tags, e.g.
                   "openmrs-module-foo-"; may be empty

Writes `base_ref=<tag>` to GITHUB_OUTPUT. The value is empty when no
suitable previous release exists (e.g. an artifact's first release), which
lets the action fall back to its default behavior. Pre-release tags
(anything carrying a semver pre-release suffix such as -alpha/-beta/-rc)
are never selected as a base. RELEASE_VERSION may itself be a pre-release;
its base is then the latest stable release older than its core version
(1.0.1-alpha diffs against the latest stable release before 1.0.1).

Also writes `is_latest=true|false`: whether its GitHub Release should be
marked Latest, i.e. RELEASE_VERSION is stable and no stable release newer
than it exists; and `prerelease=true|false`.
"""

import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import write_github_outputs

# A release tag's version component: three-part semver with optional
# pre-release and build metadata, and an optional leading `v` (OpenMRS repos
# tag inconsistently, e.g. both `v2.3.2` and `2.3.3` occur). Anchored so that
# non-version tags sharing the prefix (and, when the prefix is empty, unrelated
# tags) are ignored, and so a pre-release suffix can be detected and excluded.
VERSION_RE = re.compile(
    r"^[vV]?"
    r"(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


def parse_release_version(version):
    """Return ((major, minor, patch), is_prerelease) for a semver release version."""
    match = VERSION_RE.match(version.strip())
    if not match:
        sys.exit(f"::error::Release version '{version}' is not semver")
    core = (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )
    return core, bool(match.group("prerelease"))


def stable_releases(tags, prefix):
    """Yield (version_tuple, tag) for each stable release tag sharing `prefix`."""
    for raw in tags:
        tag = raw.strip()
        if not tag or not tag.startswith(prefix):
            continue
        match = VERSION_RE.match(tag[len(prefix):])
        if not match or match.group("prerelease"):
            continue
        version = (
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
        )
        yield version, tag


def select_previous(tags, prefix, release_version):
    """Return the previous release tag, or None.

    Among `tags` sharing `prefix` whose remainder is a stable semver version
    strictly less than `release_version`'s core version, returns the one with the greatest
    version. Pre-release and non-version tags are ignored.
    """
    target, _ = parse_release_version(release_version)
    older = [(v, tag) for v, tag in stable_releases(tags, prefix) if v < target]
    return max(older)[1] if older else None


def is_newest(tags, prefix, release_version):
    """True if `release_version` is stable and no stable release in `tags` is newer."""
    target, prerelease = parse_release_version(release_version)
    if prerelease:
        return False
    return all(v <= target for v, _ in stable_releases(tags, prefix))


def main():
    release_version = os.environ.get("RELEASE_VERSION", "").strip()
    if not release_version:
        sys.exit("::error::RELEASE_VERSION is required")
    prefix = os.environ.get("TAG_PREFIX", "")
    tags = sys.stdin.read().splitlines()
    base_ref = select_previous(tags, prefix, release_version)
    write_github_outputs({
        "base_ref": base_ref or "",
        "is_latest": str(is_newest(tags, prefix, release_version)).lower(),
        "prerelease": str(parse_release_version(release_version)[1]).lower(),
    })


if __name__ == "__main__":
    main()
