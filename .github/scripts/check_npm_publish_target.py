#!/usr/bin/env python3
"""Fail if this repository would publish names OpenMRS owns on public npm.

Outside the openmrs org, publishing `@openmrs/*` or `openmrs` to the public npm
registry is not supported: those names belong to OpenMRS, and the usual cause is
a fork that inherited an OpenMRS caller workflow. The same names on any other
registry (e.g. a private feed) are allowed.

Configuration comes from the environment:

  REPOSITORY_OWNER  owner of the repository running the workflow
  PUBLISH           "true" when the caller wants to publish
"""

import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import publishable_packages

PUBLIC_NPM_HOSTS = {"registry.npmjs.org", "registry.yarnpkg.com"}


def is_reserved(package):
    """True if `package` publishes a name OpenMRS owns on the public registry."""
    name = package["name"]
    host = urllib.parse.urlparse(package["registry"]).hostname
    return host in PUBLIC_NPM_HOSTS and (name == "openmrs" or name.startswith("@openmrs/"))


def main():
    env = os.environ.get
    if env("PUBLISH") != "true" or env("REPOSITORY_OWNER") == "openmrs":
        return

    packages = publishable_packages()
    if packages is None:
        sys.exit("::error::Could not list the packages this repository publishes; see the warnings above.")
    unresolved = [p["name"] for p in packages if not p["registry"]]
    if unresolved:
        sys.exit(f"::error::Could not work out which registry {', '.join(unresolved)} would publish to; see the warnings above.")
    reserved = [p["name"] for p in packages if is_reserved(p)]
    if reserved:
        sys.exit(
            f"::error::{', '.join(reserved)} would publish to the public npm registry under a name "
            "owned by OpenMRS, which only repositories in the openmrs org can do. Rename the packages, "
            "publish them to another registry, or set publish: false to build without publishing."
        )


if __name__ == "__main__":
    main()
