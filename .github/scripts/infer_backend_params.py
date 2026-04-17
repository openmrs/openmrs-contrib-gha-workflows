#!/usr/bin/env python3
"""Infer backend build parameters from a Maven POM file.

Parses pom.xml once and extracts:
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import parse_pom, write_github_outputs

# Maps OpenMRS platform version ranges to the Java versions they support.
# Each entry is (lower_inclusive, upper_exclusive_or_None, java_versions).
# Update this table when new platform versions add or drop Java support.
PLATFORM_JAVA_MAP = [
    ((2, 0, 0), (2, 4, 0), [8]),
    ((2, 4, 0), (2, 7, 0), [8, 11]),
    ((2, 7, 0), (2, 8, 0), [8, 11, 17]),
    ((2, 8, 0), (3, 0, 0), [8, 11, 17, 21]),
    ((3, 0, 0), None, [25]),
]

OPENMRS_DEPS = {
    ("org.openmrs.api", "openmrs-api"),
    ("org.openmrs.web", "openmrs-web"),
}


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
# POM property resolution
# ---------------------------------------------------------------------------

def resolve(val, props, depth=0):
    """Resolve ${property} references from the properties dict."""
    if not val or depth > 10:
        return val
    m = re.match(r"^\$\{(.+)\}$", val.strip())
    if m:
        resolved = props.get(m.group(1))
        return resolve(resolved, props, depth + 1) if resolved else val
    return val


def get_properties(root):
    """Extract all <properties> and implicit project properties."""
    props = {}
    el = root.find("properties")
    if el is not None:
        for child in el:
            if child.text:
                props[child.tag] = child.text.strip()
    ver = root.findtext("version")
    if ver:
        props.setdefault("project.version", ver.strip())
    parent_ver = root.findtext("parent/version")
    if parent_ver:
        props.setdefault("project.parent.version", parent_ver.strip())
        props.setdefault("project.version", parent_ver.strip())
    return props


# ---------------------------------------------------------------------------
# Java version inference
# ---------------------------------------------------------------------------

def find_compiler_version(root, props):
    """Find main_java_version from compiler settings (priority order).

    Checks: maven.compiler.release property, maven.compiler.target property,
    then <release>/<target> in maven-compiler-plugin configuration.
    """
    for key in ("maven.compiler.release", "maven.compiler.target"):
        val = props.get(key)
        if val:
            return normalize_java(resolve(val, props))
    for path in ("build/pluginManagement/plugins", "build/plugins"):
        plugins = root.find(path)
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
                        return normalize_java(resolve(el.text.strip(), props))
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


def find_openmrs_version(root, props):
    """Find the OpenMRS platform dependency version.

    Searches root dependencyManagement, root dependencies, then submodule POMs.
    """
    for path in ("dependencyManagement/dependencies", "dependencies"):
        v = find_openmrs_dep_in(root.find(path))
        if v:
            return resolve(v, props)
    modules_el = root.find("modules")
    if modules_el is None:
        return None
    for mod in modules_el.findall("module"):
        if not mod.text:
            continue
        mod_pom = os.path.join(mod.text.strip(), "pom.xml")
        mod_root = parse_pom(mod_pom)
        if mod_root is None:
            continue
        mod_props = {**props, **get_properties(mod_root)}
        for p in ("dependencyManagement/dependencies", "dependencies"):
            v = find_openmrs_dep_in(mod_root.find(p))
            if v:
                return resolve(v, mod_props)
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


# ---------------------------------------------------------------------------
# POM project version
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    root = parse_pom()
    if root is None:
        return

    props = get_properties(root)

    # Java versions
    compiler_version = find_compiler_version(root, props)
    openmrs_ver = find_openmrs_version(root, props)
    java_versions = map_to_java(openmrs_ver) if openmrs_ver else None

    if compiler_version and java_versions:
        compiler_int = int(compiler_version)
        # Only build against Java versions that can satisfy the compiler requirement
        java_versions = [v for v in java_versions if v >= compiler_int]
        if compiler_int not in java_versions:
            java_versions.append(compiler_int)
            java_versions.sort()

    if java_versions:
        main_java = str(min(java_versions))
    elif compiler_version:
        main_java = compiler_version
    else:
        main_java = None

    # Server IDs
    release_id, snapshot_id = find_server_ids(root)

    # Project version
    project_version = find_project_version(root)

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
