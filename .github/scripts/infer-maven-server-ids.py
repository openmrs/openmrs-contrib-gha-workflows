#!/usr/bin/env python3
"""Infer Maven server IDs from distributionManagement in a POM file.

Extracts the <repository><id> and <snapshotRepository><id> from the
<distributionManagement> section. Writes results as GitHub Actions step outputs.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pom_utils import parse_pom, write_github_outputs


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
    root = parse_pom()
    if root is None:
        return

    release_id, snapshot_id = find_server_ids(root)
    write_github_outputs(
        {
            "release_server_id": release_id,
            "snapshot_server_id": snapshot_id,
        }
    )


if __name__ == "__main__":
    main()
