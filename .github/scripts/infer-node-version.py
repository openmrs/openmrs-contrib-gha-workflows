#!/usr/bin/env python3
"""Infer Node.js version from package.json engines.node field.

Extracts the first major version number from the engines.node constraint.
Writes node_version as a GitHub Actions step output.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pom_utils import write_github_outputs


def infer_node_version(path="package.json"):
    """Extract the major Node.js version from package.json engines.node.

    Returns the major version string (e.g. "22") or None.
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            pkg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"::warning::Failed to parse {path}: {e}", file=sys.stderr)
        return None
    engines_node = pkg.get("engines", {}).get("node")
    if not engines_node:
        return None
    m = re.search(r"[0-9]+", engines_node)
    return m.group(0) if m else None


def main():
    version = infer_node_version()
    if version is not None:
        write_github_outputs({"node_version": version})


if __name__ == "__main__":
    main()
