#!/usr/bin/env bash
# Fail early when a publish cannot authenticate: there must be an npm token
# ($NODE_AUTH_TOKEN) or, for npm trusted publishing, an OIDC token and a Yarn
# that can exchange it. Whether npm accepts the OIDC token is only known at
# publish time.
#
# Run from the repository root, after checkout, so the repo's own Yarn answers.

set -euo pipefail

# Yarn exchanges OIDC tokens from 4.10.0, and for scoped packages from 4.10.3.
MIN_OIDC_YARN=4.10.3

if [ -n "${NODE_AUTH_TOKEN:-}" ]; then
  exit 0
fi

if [ -z "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]; then
  echo "::error::Publishing needs NPM_AUTH_TOKEN or, for npm trusted publishing, 'id-token: write' in the caller's permissions. Set publish: false to build without publishing."
  exit 1
fi

YARN_VERSION=$(yarn --version)
if [ "$(printf '%s\n%s\n' "$MIN_OIDC_YARN" "$YARN_VERSION" | sort -V | head -n1)" != "$MIN_OIDC_YARN" ]; then
  echo "::error::npm trusted publishing needs Yarn $MIN_OIDC_YARN or later; this repository uses Yarn $YARN_VERSION. Upgrade Yarn or pass NPM_AUTH_TOKEN."
  exit 1
fi
