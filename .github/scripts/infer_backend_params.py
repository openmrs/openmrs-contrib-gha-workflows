#!/usr/bin/env python3
"""Infer backend build parameters from a Maven effective POM.

Reads the effective POM produced by `mvn help:effective-pom -Doutput=...`
(path supplied via the EFFECTIVE_POM environment variable) and extracts:

- Java versions to build against (from OpenMRS platform dependency mapping)
- Main Java version (from Maven compiler settings)
- Maven server IDs for release and snapshot deployment
- POM project version

Writes results as GitHub Actions step outputs.
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import strip_ns, write_github_outputs

# Maps OpenMRS platform version ranges to the Java versions they support.
# Each entry is (lower_inclusive, upper_exclusive_or_None, java_versions).
# Update this table when new platform versions add or drop Java support.
PLATFORM_JAVA_MAP = [
    ((2, 0, 0), (2, 4, 0), [8]),
    ((2, 4, 0), (2, 7, 0), [8, 11]),
    ((2, 7, 0), (3, 0, 0), [8, 11, 17, 21]),
    ((3, 0, 0), None, [25]),
]

OPENMRS_DEPS = {
    ("org.openmrs.api", "openmrs-api"),
    ("org.openmrs.web", "openmrs-web"),
}


# ---------------------------------------------------------------------------
# Effective POM loading
# ---------------------------------------------------------------------------


def load_projects(path):
    """Parse the effective POM at `path` and return its <project> elements.

    `mvn help:effective-pom` emits a single <project> for a non-aggregator
    build and a <projects> wrapper around multiple <project> elements for
    multi-module builds. Both shapes are handled.
    """
    if not os.path.isfile(path):
        sys.exit(f"::error::Effective POM not found at {path}")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        sys.exit(f"::error::Failed to parse effective POM at {path}: {e}")
    strip_ns(root)
    if root.tag == "projects":
        projects = list(root.findall("project"))
    elif root.tag == "project":
        projects = [root]
    else:
        sys.exit(f"::error::Unexpected root element in effective POM: {root.tag}")
    if not projects:
        sys.exit("::error::Effective POM contains no <project> elements")
    return projects


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


def parse_version(s):
    """Parse '2.6.1' or '2.6.1-SNAPSHOT' into a (major, minor, patch) tuple."""
    base = re.split(r"[-+]", s.strip())[0]
    parts = []
    for p in base.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts + [0] * max(0, 3 - len(parts)))


def normalize_java(v):
    """Normalize old-style Java versions: '1.8' -> '8', '1.7' -> '7'."""
    v = v.strip()
    return v[2:] if v.startswith("1.") else v


# ---------------------------------------------------------------------------
# Per-project property access
# ---------------------------------------------------------------------------


def get_properties(project):
    """Return a dict of <properties> entries from a single <project> element."""
    props = {}
    el = project.find("properties")
    if el is not None:
        for child in el:
            if child.text:
                props[child.tag] = child.text.strip()
    return props


# ---------------------------------------------------------------------------
# Java version inference
# ---------------------------------------------------------------------------


def find_compiler_version(project, props):
    """Find main_java_version from compiler settings (priority order).

    Checks: maven.compiler.release property, maven.compiler.target property,
    then <release>/<target> in maven-compiler-plugin configuration.
    Values containing unresolved ${...} references are skipped.
    """
    for key in ("maven.compiler.release", "maven.compiler.target"):
        val = props.get(key)
        if val and "${" not in val:
            return normalize_java(val)
    for path in ("build/pluginManagement/plugins", "build/plugins"):
        plugins = project.find(path)
        if plugins is None:
            continue
        for plugin in plugins.findall("plugin"):
            if plugin.findtext("artifactId", "") != "maven-compiler-plugin":
                continue
            configs = []
            c = plugin.find("configuration")
            if c is not None:
                configs.append(c)
            for exe in plugin.findall("executions/execution"):
                c = exe.find("configuration")
                if c is not None:
                    configs.append(c)
            for config in configs:
                for tag in ("release", "target"):
                    el = config.find(tag)
                    if el is not None and el.text:
                        text = el.text.strip()
                        if text and "${" not in text:
                            return normalize_java(text)
    return None


def find_openmrs_dep_in(deps_elem):
    """Search a <dependencies> element for an OpenMRS dependency version."""
    if deps_elem is None:
        return None
    for dep in deps_elem.findall("dependency"):
        gid = (dep.findtext("groupId") or "").strip()
        aid = (dep.findtext("artifactId") or "").strip()
        if (gid, aid) in OPENMRS_DEPS:
            ver = dep.findtext("version")
            return ver.strip() if ver else None
    return None


def find_openmrs_version(projects):
    """Find the OpenMRS platform dependency version across all projects.

    Search order, since `<dependencies>` is what actually gets compiled
    against and may explicitly override an inherited dependencyManagement
    version:
      1. Each project's `<dependencies>` (actual build deps).
      2. Each project's `<dependencyManagement>` (constraints; covers
         aggregator POMs that declare the version centrally and submodules
         that inherit without re-stating).
      3. Each project's `openmrsPlatformVersion` property (set by the
         OpenMRS contrib maven parent for modules that don't declare the
         dep directly).
    """
    for project in projects:
        v = find_openmrs_dep_in(project.find("dependencies"))
        if v:
            return v
    for project in projects:
        v = find_openmrs_dep_in(project.find("dependencyManagement/dependencies"))
        if v:
            return v
    for project in projects:
        v = get_properties(project).get("openmrsPlatformVersion")
        if v:
            return v
    return None


def map_to_java(version_str):
    """Map an OpenMRS version or range string to a list of Java versions."""
    s = version_str.strip()
    if s and s[0] in "[(":
        return map_range_to_java(s)
    ver = parse_version(s)
    for low, high, javas in PLATFORM_JAVA_MAP:
        if ver >= low and (high is None or ver < high):
            return list(javas)
    return None


def map_range_to_java(range_str):
    """Map a Maven version range to Java versions (union of overlapping tiers)."""
    inner = range_str[1:-1]
    parts = inner.split(",", 1)
    if len(parts) != 2:
        return None
    lower = parse_version(parts[0].strip()) if parts[0].strip() else (0, 0, 0)
    upper = parse_version(parts[1].strip()) if parts[1].strip() else None
    result = set()
    for tier_low, tier_high, javas in PLATFORM_JAVA_MAP:
        t_hi = tier_high or (999, 999, 999)
        d_hi = upper or (999, 999, 999)
        if lower < t_hi and tier_low < d_hi:
            result.update(javas)
    return sorted(result) if result else None


# ---------------------------------------------------------------------------
# Maven server ID inference
# ---------------------------------------------------------------------------


def find_server_ids(project):
    """Extract repository and snapshotRepository server IDs from distributionManagement."""
    release_id = None
    snapshot_id = None
    dm = project.find("distributionManagement")
    if dm is not None:
        repo = dm.find("repository/id")
        if repo is not None and repo.text:
            release_id = repo.text.strip()
        snap = dm.find("snapshotRepository/id")
        if snap is not None and snap.text:
            snapshot_id = snap.text.strip()
    return release_id, snapshot_id


# ---------------------------------------------------------------------------
# POM project version
# ---------------------------------------------------------------------------


def find_project_version(project):
    """Extract project version from a <project> element."""
    ver = project.findtext("version")
    if ver:
        return ver.strip()
    parent_ver = project.findtext("parent/version")
    if parent_ver:
        return parent_ver.strip()
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    pom_path = os.environ.get("EFFECTIVE_POM")
    if not pom_path:
        sys.exit("::error::EFFECTIVE_POM environment variable is not set")

    projects = load_projects(pom_path)
    primary = projects[0]
    props = get_properties(primary)

    # Java versions
    compiler_version = find_compiler_version(primary, props)
    openmrs_ver = find_openmrs_version(projects)
    java_versions = map_to_java(openmrs_ver) if openmrs_ver else None

    if compiler_version and java_versions:
        compiler_int = int(compiler_version)
        # Only build against Java versions that can satisfy the compiler requirement
        filtered = [v for v in java_versions if v >= compiler_int]
        java_versions = filtered if filtered else [compiler_int]

    if java_versions:
        main_java = str(min(java_versions))
    elif compiler_version:
        main_java = compiler_version
    else:
        main_java = None

    # Server IDs and project version come from the aggregator (first project).
    release_id, snapshot_id = find_server_ids(primary)
    project_version = find_project_version(primary)

    write_github_outputs(
        {
            "java_versions": json.dumps(java_versions)
            if java_versions is not None
            else None,
            "main_java_version": main_java,
            "release_server_id": release_id,
            "snapshot_server_id": snapshot_id,
            "project_version": project_version,
        }
    )


if __name__ == "__main__":
    main()
