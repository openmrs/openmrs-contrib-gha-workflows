#!/usr/bin/env bash
# Prepare fork-staged coverage for the trusted Codecov upload: metadata comes
# from trusted env/API (never the fork artifact); reports copied to safe names.
# Emits commit/branch/pr/files/found to GITHUB_OUTPUT.
set -euo pipefail

COVERAGE_DIR="${COVERAGE_DIR:-coverage}"
OUTPUT_DIR="${OUTPUT_DIR:-codecov-reports}"

# A missing dir means the download misbehaved; fail loudly, don't report "no reports".
[ -d "$COVERAGE_DIR" ] || { echo "::error::Coverage directory '$COVERAGE_DIR' not found."; exit 1; }
mkdir -p "$OUTPUT_DIR"

# Copy to controlled names (in a dir disjoint from COVERAGE_DIR) so a fork-chosen
# filename can't corrupt the file list or collide with a target.
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

# Resolve the PR from the trusted head ref (workflow_run has no PR for fork
# builds; the commits/{sha}/pulls endpoint omits open fork PRs). Empty for a
# push or a ref with no open PR.
pr=""
if [ -n "$HEAD_REPO" ] && [ -n "$HEAD_BRANCH" ]; then
  pr=$(gh api -X GET "repos/$BASE_REPO/pulls" \
    -f head="${HEAD_REPO%%/*}:$HEAD_BRANCH" -f state=open \
    --jq '.[0].number // ""' 2>/dev/null || true)
fi
[[ "$pr" =~ ^[0-9]+$ ]] || pr=""

# Namespace forks (and unknown head repos) as owner:branch so they can't be
# attributed to a base branch; a bare name only for a confirmed same-repo build.
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
