#!/usr/bin/env python3
"""Infer frontend module build and release parameters from package.json.

Detects monorepo vs single-app structure and infers sensible defaults
for build commands, publish commands, version commands, artifact paths,
and turborepo cache settings. Writes results as GitHub Actions step outputs.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import write_github_outputs


def load_package_json(path="package.json"):
    """Parse package.json and return the dict, or None with a warning."""
    if not os.path.isfile(path):
        print(
            f"::warning::No {path} found; skipping inference",
            file=sys.stderr,
        )
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"::warning::Failed to parse {path}: {e}", file=sys.stderr)
        return None


def is_monorepo(pkg):
    """Return True if the package.json defines a non-empty workspaces field.

    Handles both array format ("workspaces": ["packages/*"]) and object
    format ("workspaces": {"packages": ["packages/*"]}).
    """
    workspaces = pkg.get("workspaces")
    if isinstance(workspaces, list):
        return len(workspaces) > 0
    if isinstance(workspaces, dict):
        packages = workspaces.get("packages", [])
        return len(packages) > 0
    return False


def get_workspace_globs(pkg):
    """Extract the list of workspace glob patterns from package.json."""
    workspaces = pkg.get("workspaces", [])
    if isinstance(workspaces, dict):
        return workspaces.get("packages", [])
    return workspaces


def workspace_artifact_paths(pkg):
    """Build newline-joined artifact paths from workspace globs.

    Each workspace glob (e.g. "packages/apps/*") gets "/dist" appended,
    producing paths like "packages/apps/*/dist".
    """
    globs = get_workspace_globs(pkg)
    paths = [f"{g}/dist" for g in globs]
    return "\n".join(paths)


def infer_params(path="package.json", base_dir=None):
    """Infer frontend build/release parameters from package.json.

    Args:
        path: Path to package.json.
        base_dir: Directory to check for turbo.json. Defaults to the
                  directory containing package.json.

    Returns:
        Dict of output key-value pairs. Values of None are omitted from output.
    """
    pkg = load_package_json(path)
    if pkg is None:
        return {}

    if base_dir is None:
        base_dir = os.path.dirname(path) or "."

    outputs = {}
    scripts = pkg.get("scripts", {})
    root_name = pkg.get("name", "")

    # If turbo.json is absent, fall back to plain yarn build
    turbo_json = os.path.join(base_dir, "turbo.json")
    has_turbo = os.path.isfile(turbo_json)
    if not has_turbo:
        outputs["build_command"] = "yarn build"
    else:
        outputs["enable_turborepo_cache"] = "true"

    if is_monorepo(pkg):
        outputs["is_monorepo"] = "true"
        outputs["verify_command"] = "yarn verify --concurrency=5"
        outputs["artifact_path"] = workspace_artifact_paths(pkg)

        # Version command: set pre-release version across all workspaces
        outputs["pre_release_version_command"] = (
            "yarn workspaces foreach --all --topological"
            f" --exclude {root_name}"
            ' version "$PRERELEASE_VERSION"'
        )

        # Publish commands: prefer repo-defined CI scripts when available
        if "ci:prepublish" in scripts:
            outputs["pre_release_publish_command"] = "yarn run ci:prepublish"
        elif "ci:publish-next" in scripts:
            outputs["pre_release_publish_command"] = "yarn run ci:publish-next"
        else:
            outputs["pre_release_publish_command"] = (
                "yarn workspaces foreach --all --topological"
                f" --exclude {root_name}"
                " npm publish --access public --tag next"
            )

        if "ci:prepublish-patch" in scripts:
            outputs["pre_release_patch_publish_command"] = (
                "yarn run ci:prepublish-patch"
            )

        if "ci:publish" in scripts:
            outputs["release_publish_command"] = "yarn run ci:publish"
        else:
            outputs["release_publish_command"] = (
                "yarn workspaces foreach --all --topological"
                f" --exclude {root_name}"
                " npm publish --access public"
            )
    else:
        outputs["is_monorepo"] = "false"

    return outputs


def main():
    outputs = infer_params()
    write_github_outputs(outputs)


if __name__ == "__main__":
    main()
