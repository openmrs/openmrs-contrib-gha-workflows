#!/usr/bin/env python3
"""Infer main_java_version and java_versions from a Maven POM file.

Extracts the Java compiler target from Maven compiler settings and maps the
OpenMRS platform dependency version to supported Java versions. Writes results
as GitHub Actions step outputs.
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

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


def strip_ns(root):
    """Remove XML namespace prefixes from all elements."""
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


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
        if not os.path.isfile(mod_pom):
            continue
        try:
            mod_root = ET.parse(mod_pom).getroot()
            strip_ns(mod_root)
        except ET.ParseError:
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


def main():
    if not os.path.isfile("pom.xml"):
        print(
            "::warning::No pom.xml found; skipping Java version inference",
            file=sys.stderr,
        )
        return
    try:
        root = ET.parse("pom.xml").getroot()
    except ET.ParseError as e:
        print(f"::warning::Failed to parse pom.xml: {e}", file=sys.stderr)
        return

    strip_ns(root)
    props = get_properties(root)
    compiler_version = find_compiler_version(root, props)
    openmrs_ver = find_openmrs_version(root, props)
    java_versions = map_to_java(openmrs_ver) if openmrs_ver else None

    # Ensure the compiler target is in java_versions (it should be tested against)
    if (
        compiler_version
        and java_versions
        and int(compiler_version) not in java_versions
    ):
        java_versions.append(int(compiler_version))
        java_versions.sort()

    # main_java_version is the minimum supported version
    if java_versions:
        main_java = str(min(java_versions))
    elif compiler_version:
        main_java = compiler_version
    else:
        main_java = None

    out_file = os.environ.get("GITHUB_OUTPUT", "")
    lines = []
    if java_versions is not None:
        lines.append(f"java_versions={json.dumps(java_versions)}")
    if main_java is not None:
        lines.append(f"main_java_version={main_java}")
    if out_file and lines:
        with open(out_file, "a") as f:
            f.write("\n".join(lines) + "\n")
    elif lines:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
