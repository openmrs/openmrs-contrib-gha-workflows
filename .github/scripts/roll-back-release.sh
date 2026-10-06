#!/usr/bin/env bash
# Rolls back a release whose tag was pushed but whose packages never reached
# npm: deletes the remote tag and pushes a revert of the release commit.
#
# Both changes go in one atomic push, so the remote never keeps one without the
# other. The revert is rebuilt on the latest branch tip and retried, in case
# something was merged after the release commit was pushed.
#
# Only call this once npm has been confirmed to have none of the release's
# packages; deleting a tag that matches published packages strands them.
#
# Environment:
#   BRANCH           branch the release commit was pushed to
#   RELEASE_TAG      tag to delete
#   RELEASE_VERSION  version being rolled back, for the commit message
#   RELEASE_SHA      the release commit to revert
#   RETRY_DELAY      seconds between push attempts (default 10)
#   PUSH_TOKEN       optional GitHub token to use instead of the credentials
#                    actions/checkout persisted, e.g. a fresh App token when the
#                    original may have expired
#
# Writes `sha=<revert commit>` to GITHUB_OUTPUT on success.

set -euo pipefail

if [ -n "${PUSH_TOKEN:-}" ]; then
  # An empty extraheader value clears the persisted Authorization header, so
  # git sends only the new one.
  BASIC=$(printf 'x-access-token:%s' "$PUSH_TOKEN" | base64 | tr -d '\n')
  echo "::add-mask::$BASIC"
  export GIT_CONFIG_COUNT=2
  export GIT_CONFIG_KEY_0="http.https://github.com/.extraheader" GIT_CONFIG_VALUE_0=""
  export GIT_CONFIG_KEY_1="http.https://github.com/.extraheader" GIT_CONFIG_VALUE_1="AUTHORIZATION: basic $BASIC"
fi

for ATTEMPT in 1 2 3; do
  git fetch --quiet origin "refs/heads/$BRANCH"
  git checkout --quiet --force --detach FETCH_HEAD
  if ! git revert --no-commit "$RELEASE_SHA"; then
    git revert --abort || true
    echo "::error::Reverting $RELEASE_SHA conflicts with later changes on $BRANCH"
    exit 1
  fi
  git commit --quiet --no-verify \
    -m "(chore) Roll back release v${RELEASE_VERSION}" \
    -m "Publishing to npm failed and nothing was published, so tag $RELEASE_TAG was removed."
  REVERT_SHA=$(git rev-parse HEAD)
  if git push --atomic origin "HEAD:refs/heads/$BRANCH" ":refs/tags/$RELEASE_TAG"; then
    echo "sha=$REVERT_SHA" >> "$GITHUB_OUTPUT"
    exit 0
  fi
  # A push can land even though the client saw an error. If the branch tip is
  # this revert, the atomic push succeeded and the tag is gone too.
  if [ "$(git ls-remote origin "refs/heads/$BRANCH" | cut -f1)" = "$REVERT_SHA" ]; then
    echo "sha=$REVERT_SHA" >> "$GITHUB_OUTPUT"
    exit 0
  fi
  echo "::warning::Rollback push attempt $ATTEMPT failed; retrying"
  sleep "${RETRY_DELAY:-10}"
done
exit 1
