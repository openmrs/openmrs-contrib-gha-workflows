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

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONCURRENCY = "5"


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


def infer_params(path="package.json", base_dir=None, concurrency=None):
    """Infer frontend build/release parameters from package.json.

    Args:
        path: Path to package.json.
        base_dir: Directory to check for turbo.json. Defaults to the
                  directory containing package.json.
        concurrency: Turborepo concurrency for inferred turbo commands; empty
                  for none. Defaults to $TURBO_CONCURRENCY, else 5.

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
    if concurrency is None:
        concurrency = os.environ.get("TURBO_CONCURRENCY", DEFAULT_CONCURRENCY)
    concurrency_flag = f" --concurrency={concurrency}" if concurrency else ""

    # If turbo.json is absent, fall back to plain yarn build
    turbo_json = os.path.join(base_dir, "turbo.json")
    has_turbo = os.path.isfile(turbo_json)
    if not has_turbo:
        outputs["build_command"] = "yarn build"
    else:
        outputs["build_command"] = f"yarn turbo run build --color{concurrency_flag}"
        outputs["enable_turborepo_cache"] = "true"

    # A repo's `ci:version` (exact version or patch/minor/major) and
    # `ci:current-version` take over versioning, e.g. for lerna-managed versions.
    if "ci:version" in scripts:
        outputs["pre_release_version_command"] = 'yarn run ci:version "$PRERELEASE_VERSION"'
        outputs["release_version_command"] = 'yarn run ci:version "$RELEASE_VERSION"'
    if "ci:current-version" in scripts:
        outputs["current_version_command"] = "yarn run --silent ci:current-version"

    publish = f'bash "{SCRIPTS_DIR}/publish-package.sh" --access public'

    if is_monorepo(pkg):
        outputs["is_monorepo"] = "true"
        outputs["verify_command"] = "yarn verify" + (
            concurrency_flag if passes_concurrency(scripts.get("verify", "")) else ""
        )
        outputs["artifact_path"] = workspace_artifact_paths(pkg)

        exclude_flag = f" --exclude {root_name}" if root_name else ""
        foreach = f"yarn workspaces foreach --all --topological{exclude_flag}"

        if "ci:version" not in scripts:
            # Set the pre-release version on every published workspace, and the
            # release version on all of them (root included), as the O3
            # `release` script does.
            outputs["pre_release_version_command"] = f'{foreach} version "$PRERELEASE_VERSION"'
            outputs["release_version_command"] = (
                'yarn workspaces foreach --all --topological version "$RELEASE_VERSION"'
            )

        outputs["pre_release_publish_command"] = (
            f"{foreach} exec {publish} --tag next --tolerate-republish"
        )
        outputs["release_publish_command"] = (
            f"{foreach} exec {publish} --tolerate-republish"
        )
    else:
        outputs["is_monorepo"] = "false"
        outputs["pre_release_publish_command"] = f"{publish} --tag next --tolerate-republish"
        outputs["release_publish_command"] = f"{publish} --tolerate-republish"

    # Repo-defined publish scripts take over, e.g. for lerna or nx publishing.
    if "ci:prepublish" in scripts:
        outputs["pre_release_publish_command"] = "yarn run ci:prepublish"
    elif "ci:publish-next" in scripts:
        outputs["pre_release_publish_command"] = "yarn run ci:publish-next"
    if "ci:prepublish-patch" in scripts:
        outputs["pre_release_patch_publish_command"] = "yarn run ci:prepublish-patch"
    if "ci:publish" in scripts:
        outputs["release_publish_command"] = "yarn run ci:publish"

    return outputs


def passes_concurrency(script):
    """Whether to append `--concurrency` to `yarn verify`.

    Not when the script already sets it (turbo rejects a repeated flag), nor
    when its last command, which receives appended arguments, isn't turbo.
    """
    if "--concurrency" in script:
        return False
    last = script.replace("&&", ";").split(";")[-1].strip()
    return not last or last.startswith("turbo")


def main():
    outputs = infer_params()
    write_github_outputs(outputs)


if __name__ == "__main__":
    main()
