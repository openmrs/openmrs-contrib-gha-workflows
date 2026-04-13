#!/usr/bin/env python3
"""Infer Maven server IDs from distributionManagement in a POM file.

Extracts the <repository><id> and <snapshotRepository><id> from the
<distributionManagement> section. Writes results as GitHub Actions step outputs.
"""

import os
import sys
import xml.etree.ElementTree as ET


def strip_ns(root):
    """Remove XML namespace prefixes from all elements."""
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def find_server_ids(root):
    """Extract repository and snapshotRepository server IDs from distributionManagement."""
    release_id = None
    snapshot_id = None
    dm = root.find("distributionManagement")
    if dm is not None:
        repo = dm.find("repository/id")
        if repo is not None and repo.text:
            release_id = repo.text.strip()
        snap = dm.find("snapshotRepository/id")
        if snap is not None and snap.text:
            snapshot_id = snap.text.strip()
    return release_id, snapshot_id


def main():
    if not os.path.isfile("pom.xml"):
        print(
            "::warning::No pom.xml found; skipping Maven server ID inference",
            file=sys.stderr,
        )
        return
    try:
        root = ET.parse("pom.xml").getroot()
    except ET.ParseError as e:
        print(f"::warning::Failed to parse pom.xml: {e}", file=sys.stderr)
        return

    strip_ns(root)
    release_id, snapshot_id = find_server_ids(root)

    out_file = os.environ.get("GITHUB_OUTPUT", "")
    lines = []
    if release_id is not None:
        lines.append(f"release_server_id={release_id}")
    if snapshot_id is not None:
        lines.append(f"snapshot_server_id={snapshot_id}")
    if out_file and lines:
        with open(out_file, "a") as f:
            f.write("\n".join(lines) + "\n")
    elif lines:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
