#!/usr/bin/env bash
# Stage JaCoCo reports found under the current directory into COVERAGE_DIR,
# flattened to per-module file names, next to a metadata file recording the
# commit/branch/PR the coverage belongs to. The companion upload-coverage
# workflow uploads these to Codecov from a trusted context, where it no longer
# has access to this run's event payload — hence capturing the metadata here.
# Emits `staged=true|false` to GITHUB_OUTPUT.
set -euo pipefail

VALUE=$(echo "${UPLOAD_COVERAGE:-}" | tr '[:upper:]' '[:lower:]')
if [ "$VALUE" = "false" ]; then
  echo "staged=false" >> "$GITHUB_OUTPUT"
  exit 0
fi
if [ "$VALUE" != "true" ] && ! find . -name 'jacoco.xml' -print -quit 2>/dev/null | grep -q .; then
  echo "staged=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

mkdir -p "$COVERAGE_DIR"
find . -name 'jacoco.xml' | while read -r report; do
  module=$(echo "$report" | sed -e 's#^\./##' -e 's#/\{0,1\}target/.*##' -e 's#/#-#g')
  [ -z "$module" ] && module=root
  cp "$report" "$COVERAGE_DIR/${module}-jacoco.xml"
done

if [ "${EVENT_NAME:-}" = "pull_request" ] || [ "${EVENT_NAME:-}" = "pull_request_target" ]; then
  COMMIT="${PR_HEAD_SHA:-}"; BRANCH="${PR_HEAD_REF:-}"; PR="${PR_NUMBER:-}"
else
  COMMIT="${PUSH_SHA:-}"; BRANCH="${PUSH_REF:-}"; PR=""
fi
{
  echo "COMMIT=$COMMIT"
  echo "BRANCH=$BRANCH"
  echo "PR=$PR"
} > "$COVERAGE_DIR/codecov-metadata.env"

echo "staged=true" >> "$GITHUB_OUTPUT"
