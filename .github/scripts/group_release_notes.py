#!/usr/bin/env python3
"""Regroup GitHub-generated release notes by PR title type.

Reads the body returned by GitHub's `generate-notes` API on stdin and writes
it to stdout with the "What's Changed" entries regrouped under one heading
per title type, in the order breaking changes, features, fixes, tests, docs,
chores. OpenMRS PR titles start with a type in parentheses, e.g.
`(fix) O3-1234: Summary`; conventional-commit style (`fix: ...`,
`fix(scope)!: ...`) is recognised too. Entries without a recognised type are
listed last under "Other changes".

Everything outside the "What's Changed" section (New Contributors, the Full
Changelog link) is passed through unchanged, as is a body with no such
section. Any `###` headings GitHub added from a repo's label categories are
dropped, since the entries under them are regrouped by type instead.
"""

import re
import sys

SECTIONS = [
    ("breaking", "### ⚠️ Breaking changes"),
    ("feat", "### 🚀 New features"),
    ("fix", "### 🐛 Bug fixes"),
    ("test", "### 🧪 Tests"),
    ("docs", "### 📝 Documentation"),
    ("chore", "### 🧹 Housekeeping"),
    (None, "### Other changes"),
]

KNOWN_TYPES = {key for key, _ in SECTIONS if key}

ENTRY_RE = re.compile(r"^[*-] (?P<title>.*)$")
PAREN_TYPE_RE = re.compile(r"^\(\s*(?P<type>[A-Za-z]+)\s*\)")
CONVENTIONAL_TYPE_RE = re.compile(r"^(?P<type>[A-Za-z]+)(?:\([^)]*\))?(?P<bang>!)?:")


def classify(title):
    """Return the section key for a PR title, or None if it has no known type."""
    title = title.strip()
    match = PAREN_TYPE_RE.match(title) or CONVENTIONAL_TYPE_RE.match(title)
    if not match:
        return None
    if match.groupdict().get("bang"):
        return "breaking"
    title_type = match.group("type").lower()
    return title_type if title_type in KNOWN_TYPES else None


def group_notes(body):
    """Return `body` with its "What's Changed" entries grouped by type."""
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines)
                     if line.strip() == "## What's Changed")
    except StopIteration:
        return body

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## ") or lines[i].startswith("**Full Changelog**"):
            end = i
            break

    groups = {key: [] for key, _ in SECTIONS}
    for line in lines[start + 1:end]:
        match = ENTRY_RE.match(line)
        if match:
            groups[classify(match.group("title"))].append(line)

    section = [lines[start]]
    for key, heading in SECTIONS:
        if groups[key]:
            section += ["", heading, ""] + groups[key]

    rest = lines[end:]
    result = lines[:start] + section + ([""] + rest if rest else [])
    return "\n".join(result) + "\n"


def main():
    sys.stdout.write(group_notes(sys.stdin.read()))


if __name__ == "__main__":
    main()
