#!/usr/bin/env bash
# Prepare fork-staged coverage for the trusted Codecov upload: commit/branch/PR
# all come from trusted sources (the workflow_run HEAD_* env and the base-repo
# API), never the fork's artifact, and reports are copied to controlled names.
# Emits commit/branch/pr/files/found to GITHUB_OUTPUT.
set -euo pipefail

COVERAGE_DIR="${COVERAGE_DIR:-coverage}"
OUTPUT_DIR="${OUTPUT_DIR:-codecov-reports}"

# A missing directory means the download step misbehaved; fail loudly rather than
# let the find below report "no reports". OUTPUT_DIR must stay disjoint from
# COVERAGE_DIR so the find never re-scans copied reports.
[ -d "$COVERAGE_DIR" ] || { echo "::error::Coverage directory '$COVERAGE_DIR' not found."; exit 1; }
mkdir -p "$OUTPUT_DIR"

# Copy to controlled names so a fork-chosen filename (comma/newline, or a literal
# report-0-jacoco.xml colliding with a target) can't corrupt the file list.
files=""
i=0
while IFS= read -r -d '' report; do
  dest="$OUTPUT_DIR/report-${i}-jacoco.xml"
  cp "$report" "$dest"
  files="${files:+$files,}$dest"
  i=$((i + 1))
done < <(find "$COVERAGE_DIR" -name '*-jacoco.xml' -print0 | sort -z)

if [ -z "$files" ]; then
  echo "No JaCoCo reports staged; nothing to upload."
  echo "found=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

# Resolve the PR number from the trusted head ref via the base-repo API, never
# the fork's artifact (workflow_run carries no PR for fork builds). The head
# filter is owner:branch, both from the trusted workflow_run payload; -f
# url-encodes them. Empty for a push build or a ref with no open PR. (The
# commits/{sha}/pulls endpoint is not used: it omits open fork PRs.)
pr=""
if [ -n "$HEAD_REPO" ] && [ -n "$HEAD_BRANCH" ]; then
  pr=$(gh api -X GET "repos/$BASE_REPO/pulls" \
    -f head="${HEAD_REPO%%/*}:$HEAD_BRANCH" -f state=open \
    --jq '.[0].number // ""' 2>/dev/null || true)
fi
[[ "$pr" =~ ^[0-9]+$ ]] || pr=""

# Use a bare branch name only for a confirmed same-repo build; a fork (or an
# unknown/empty head repo) is namespaced owner:branch so it can never be
# attributed to a base branch.
if [ -n "$HEAD_REPO" ] && [ "$HEAD_REPO" = "$BASE_REPO" ]; then
  branch="$HEAD_BRANCH"
else
  branch="${HEAD_REPO%%/*}:$HEAD_BRANCH"
fi

{
  echo "commit=$HEAD_SHA"
  echo "branch=$branch"
  echo "pr=$pr"
  echo "files=$files"
  echo "found=true"
} >> "$GITHUB_OUTPUT"
