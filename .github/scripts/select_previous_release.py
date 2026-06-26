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
are never selected as a base.
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
    """Parse the release version into a (major, minor, patch) ordering key.

    The caller validates this is stable semver upstream; here we only need
    its numeric ordering key and reject anything non-stable defensively.
    """
    match = VERSION_RE.match(version.strip())
    if not match or match.group("prerelease"):
        sys.exit(f"::error::Release version '{version}' is not stable semver")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def select_previous(tags, prefix, release_version):
    """Return the previous release tag, or None.

    Among `tags` sharing `prefix` whose remainder is a stable semver version
    strictly less than `release_version`, returns the one with the greatest
    version. Pre-release and non-version tags are ignored.
    """
    target = parse_release_version(release_version)
    best = None  # (version_tuple, tag)
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
        if version >= target:
            continue
        if best is None or version > best[0]:
            best = (version, tag)
    return best[1] if best else None


def main():
    release_version = os.environ.get("RELEASE_VERSION", "").strip()
    if not release_version:
        sys.exit("::error::RELEASE_VERSION is required")
    prefix = os.environ.get("TAG_PREFIX", "")
    base_ref = select_previous(sys.stdin.read().splitlines(), prefix, release_version)
    write_github_outputs({"base_ref": base_ref or ""})


if __name__ == "__main__":
    main()
