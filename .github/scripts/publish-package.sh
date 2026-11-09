#!/usr/bin/env bash
# Publish the package in the current directory with `yarn npm publish`, passing
# through any arguments, and record `name@version` in $PUBLISH_LOG on success.
#
# The record is what lets a failed release tell which packages already went out
# without asking the registry. Used by the inferred publish commands; monorepos
# run it in each workspace via `yarn workspaces foreach ... exec`.

set -euo pipefail

yarn npm publish "$@"

if [ -n "${PUBLISH_LOG:-}" ]; then
  node -p 'const p = require("./package.json"); p.name + "@" + p.version' >> "$PUBLISH_LOG"
fi
