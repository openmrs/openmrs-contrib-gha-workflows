#!/usr/bin/env python3
"""Infer the project version from a Maven POM file.

Extracts the <version> (or <parent><version> as fallback) from pom.xml.
Writes project_version as a GitHub Actions step output.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import parse_pom, write_github_outputs


def find_project_version(root):
    """Extract project version from POM root element.

    Checks <version> first, then falls back to <parent><version>.
    """
    ver = root.findtext("version")
    if ver:
        return ver.strip()
    parent_ver = root.findtext("parent/version")
    if parent_ver:
        return parent_ver.strip()
    return None


def main():
    root = parse_pom()
    if root is None:
        return
    version = find_project_version(root)
    write_github_outputs({"project_version": version})


if __name__ == "__main__":
    main()
