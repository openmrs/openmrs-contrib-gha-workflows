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
  # Suffix on collision so a module with multiple reports (e.g. unit + IT) keeps
  # both instead of one silently overwriting the other.
  dest="$COVERAGE_DIR/${module}-jacoco.xml"
  n=1
  while [ -e "$dest" ]; do
    dest="$COVERAGE_DIR/${module}-${n}-jacoco.xml"
    n=$((n + 1))
  done
  cp "$report" "$dest"
done

echo "staged=true" >> "$GITHUB_OUTPUT"
