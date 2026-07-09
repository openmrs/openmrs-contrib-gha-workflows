#!/usr/bin/env bash
# Stage JaCoCo reports found under the current directory into COVERAGE_DIR,
# flattened to per-module file names, next to a metadata file recording the PR
# number. The upload-coverage workflow derives commit/branch from the trusted
# workflow_run event; only the PR number (empty there for fork PRs) is carried
# in the artifact. Emits `staged=true|false` to GITHUB_OUTPUT.
set -euo pipefail

VALUE=$(echo "${UPLOAD_COVERAGE:-}" | tr '[:upper:]' '[:lower:]')
if [ "$VALUE" = "false" ]; then
  echo "staged=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

# No reports means nothing to upload; a metadata-only artifact would make the
# upload job invoke Codecov with an empty file list.
reports=$(find . -name 'jacoco.xml')
if [ -z "$reports" ]; then
  echo "staged=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

mkdir -p "$COVERAGE_DIR"
echo "$reports" | while read -r report; do
  module=$(echo "$report" | sed -e 's#^\./##' -e 's#/\{0,1\}target/.*##' -e 's#/#-#g')
  [ -z "$module" ] && module=root
  cp "$report" "$COVERAGE_DIR/${module}-jacoco.xml"
done

if [ "${EVENT_NAME:-}" = "pull_request" ] || [ "${EVENT_NAME:-}" = "pull_request_target" ]; then
  PR="${PR_NUMBER:-}"
else
  PR=""
fi
echo "PR=$PR" > "$COVERAGE_DIR/codecov-metadata.env"

echo "staged=true" >> "$GITHUB_OUTPUT"
