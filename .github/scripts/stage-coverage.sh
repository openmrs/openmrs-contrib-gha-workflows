#!/usr/bin/env bash
# Stage JaCoCo reports into COVERAGE_DIR, flattened to per-module names, for the
# upload workflow to send to Codecov. Emits staged=true|false.
set -euo pipefail

VALUE=$(echo "${UPLOAD_COVERAGE:-}" | tr '[:upper:]' '[:lower:]')
if [ "$VALUE" = "false" ]; then
  echo "staged=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

# No reports: skip so the upload job isn't handed an empty file list.
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

echo "staged=true" >> "$GITHUB_OUTPUT"
