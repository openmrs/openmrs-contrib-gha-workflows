#!/usr/bin/env python3
"""Choose the maven-release-plugin version for a release.

The project's own version is used when it is at least MINIMUM_VERSION; an older
one is raised to MINIMUM_VERSION, the version these workflows were built
around. A project that does not choose a version gets DEFAULT_VERSION.

The effective POM cannot show whether the project chose its version, because
Maven's super POM pins one too, so the version Maven gives an empty project
(SUPER_EFFECTIVE_POM) is treated as "not chosen". Configuration:

  DECLARED_VERSION     release plugin version in the project's effective POM
  SUPER_EFFECTIVE_POM  effective POM of an empty project, from the same Maven

Writes `version=<version>`.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from infer_backend_params import find_release_plugin_version, load_projects
from utils import write_github_outputs

MINIMUM_VERSION = "3.1.1"
DEFAULT_VERSION = "3.3.1"

VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:-(.+))?$")


def version_key(version):
    """Sort key for a plugin version, or None if it is not `N.N.N[-qualifier]`.

    A qualified version (3.0.0-M7) sorts before the same release (3.0.0).
    """
    match = VERSION_RE.match(version)
    if not match:
        return None
    numbers = [int(n) for n in match.group(1).split(".")]
    numbers += [0] * (3 - len(numbers))
    return (tuple(numbers), 0 if match.group(2) else 1)


def choose_version(declared, maven_default):
    if not declared or declared == maven_default:
        return DEFAULT_VERSION
    key = version_key(declared)
    if key is None:
        print(f"::warning::Could not compare maven-release-plugin version '{declared}'; using it as is.")
        return declared
    if key < version_key(MINIMUM_VERSION):
        print(
            f"::notice::The POM uses maven-release-plugin {declared}; releasing with "
            f"{MINIMUM_VERSION} instead, the oldest version these workflows support."
        )
        return MINIMUM_VERSION
    return declared


def main():
    declared = os.environ.get("DECLARED_VERSION", "").strip()
    maven_default = find_release_plugin_version(load_projects(os.environ["SUPER_EFFECTIVE_POM"])[0])
    write_github_outputs({"version": choose_version(declared, maven_default)})


if __name__ == "__main__":
    main()
