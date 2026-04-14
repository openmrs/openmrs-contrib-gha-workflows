"""Shared utilities for GitHub Actions inference scripts."""

import os
import sys
import xml.etree.ElementTree as ET


def strip_ns(root):
    """Remove XML namespace prefixes from all elements."""
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def parse_pom(path="pom.xml"):
    """Parse a POM file, strip namespaces, and return the root element.

    Returns None and prints a GitHub Actions warning if the file is missing
    or cannot be parsed.
    """
    if not os.path.isfile(path):
        print(
            f"::warning::No {path} found; skipping inference",
            file=sys.stderr,
        )
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        print(f"::warning::Failed to parse {path}: {e}", file=sys.stderr)
        return None
    strip_ns(root)
    return root


def write_github_outputs(outputs):
    """Write key=value pairs to GITHUB_OUTPUT or stdout.

    Args:
        outputs: dict of {key: value} pairs. None values are skipped.

    Values containing newlines use the heredoc delimiter syntax required
    by GitHub Actions for multiline outputs.
    """
    entries = []
    for key, value in outputs.items():
        if value is not None:
            if "\n" in str(value):
                entries.append(f"{key}<<EOF\n{value}\nEOF")
            else:
                entries.append(f"{key}={value}")
    if not entries:
        return
    out_file = os.environ.get("GITHUB_OUTPUT", "")
    if out_file:
        with open(out_file, "a") as f:
            f.write("\n".join(entries) + "\n")
    else:
        print("\n".join(entries))
