"""Shared utilities for GitHub Actions inference scripts."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NPM_REGISTRY = "https://registry.npmjs.org"


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


def yarn_workspaces(extra_args=(), cwd=None, strict=False):
    """Return yarn's workspace list as [{"location": ..., "name": ...}].

    Yarn is the authority on which workspaces exist, rather than globbing the
    `workspaces` field ourselves: it resolves nested workspaces and negated
    patterns the same way the publish and version commands will. Pass
    ("--no-private",) to drop workspaces marked private.

    Returns [] if yarn is unavailable or fails, so callers can degrade rather
    than abort, or None with `strict`, for callers that must not guess.
    """
    try:
        result = subprocess.run(
            ["yarn", "workspaces", "list", "--json", *extra_args],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"::warning::Could not list yarn workspaces: {e}", file=sys.stderr)
        return None if strict else []

    workspaces = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            workspaces.append(json.loads(line))
        except ValueError:
            print(
                f"::warning::Skipping unparseable workspace entry: {line}",
                file=sys.stderr,
            )
            if strict:
                return None
    return workspaces


def publishable_package_names(workspaces):
    """Names of the workspaces a publish command would actually push.

    The root workspace is dropped whenever other workspaces exist: the inferred
    publish command excludes it by name (`--exclude <root>`), and `--no-private`
    keeps it whenever the root is not marked private. A repo whose only
    workspace IS the root is a single-package repo, and there the root is the
    package being published, so it stays.
    """
    if len(workspaces) > 1:
        workspaces = [w for w in workspaces if w.get("location") != "."]
    return [w["name"] for w in workspaces if w.get("name")]


def yarn_registry_config(cwd=None):
    """Return the Yarn settings that decide where packages publish.

    Values reflect `.yarnrc.yml` and `YARN_*` environment overrides. Unset
    settings come back as None (Yarn 4 prints `undefined` for them). Returns
    None if yarn is unavailable or fails, since guessing the registry would be
    worse than admitting it is unknown.
    """
    config = {}
    for key in ("npmPublishRegistry", "npmRegistryServer", "npmScopes"):
        try:
            result = subprocess.run(
                ["yarn", "config", "get", key, "--json"],
                capture_output=True,
                text=True,
                check=True,
                cwd=cwd,
            )
            output = result.stdout.strip()
            config[key] = None if output in ("", "undefined") else json.loads(output)
        except (OSError, subprocess.CalledProcessError, ValueError) as e:
            print(f"::warning::Could not read yarn config {key}: {e}", file=sys.stderr)
            return None
    return config


def publish_registry(manifest, config):
    """The registry Yarn publishes `manifest` to, mirroring its own precedence.

    `publishConfig.registry`, then the package scope's `npmPublishRegistry`,
    then the top-level `npmPublishRegistry`, then `npmRegistryServer`. Returns
    None when that depends on Yarn config that could not be read (`config` None).
    """
    registry = (manifest.get("publishConfig") or {}).get("registry")
    if not registry and config is None:
        return None
    name = manifest.get("name", "")
    if not registry and name.startswith("@"):
        scope = name[1:].split("/", 1)[0]
        registry = ((config.get("npmScopes") or {}).get(scope) or {}).get("npmPublishRegistry")
    registry = registry or config.get("npmPublishRegistry") or config.get("npmRegistryServer") or NPM_REGISTRY
    return registry.rstrip("/")


def publishable_packages(cwd=None):
    """Return [{"name", "version", "registry"}] for the packages a publish would push.

    Each package is reported at its own version, which may differ between
    workspaces, and with the registry Yarn would publish it to, or None if that
    could not be determined. Returns None if the packages themselves could not
    be listed.
    """
    config = yarn_registry_config(cwd)
    workspaces = yarn_workspaces(("--no-private",), cwd, strict=True)
    if workspaces is None:
        return None
    names = set(publishable_package_names(workspaces))
    packages = []
    for workspace in workspaces:
        if workspace.get("name") not in names:
            continue
        path = os.path.join(cwd or ".", workspace["location"], "package.json")
        try:
            with open(path) as f:
                manifest = json.load(f)
        except (OSError, ValueError) as e:
            print(f"::warning::Could not read {path}: {e}", file=sys.stderr)
            return None
        packages.append({
            "name": workspace["name"],
            "version": manifest.get("version", ""),
            "registry": publish_registry(manifest, config),
        })
    return packages


def npm_version_state(package, version, token=None, registry=NPM_REGISTRY):
    """Return "live", "missing" or "unknown" for `package@version` on npm.

    Queries the registry's per-version endpoint, which is served uncached, rather
    than `npm view`, which reads the package document from a CDN cache that can
    lag a fresh publish by minutes. "missing" requires a definite 404; any other
    failure (network, auth, rate limiting) is "unknown", so callers never mistake
    an unreachable registry for an absent version. Pass `token` to see
    restricted packages, and `registry` to query a registry other than npmjs.
    """
    url = f"{registry.rstrip('/')}/{urllib.parse.quote(package, safe='@')}/{urllib.parse.quote(version)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        return "missing" if e.code == 404 else "unknown"
    except (OSError, ValueError) as e:
        print(f"::warning::Could not query npm for {package}: {e}", file=sys.stderr)
        return "unknown"
    return "live" if data.get("version") == version else "unknown"
