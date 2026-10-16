#!/usr/bin/env bash
# Cut a Maven release with maven-release-plugin: prepare (tag and bump), perform
# (deploy the tagged release), then deploy the next SNAPSHOT.
#
# Run from the root of a checkout whose `origin` can be pushed to. The caller is
# expected to have validated the versions and checked the release tag is free.
# Once the release is deployed, writes released=true to $GITHUB_OUTPUT (if set),
# so a failed SNAPSHOT deploy can be told apart from a failed release.
# Release builds use a throwaway local repository so nothing cached from earlier
# builds (e.g. stale SNAPSHOTs) can leak into the released artifacts.
#
# Usage: maven-release.sh -r <release-version> [-d <development-version>] [-v <plugin-version>] [-s] [-p <profile>] [-D <property=value>]
#   -r  version to release, e.g. 1.3.3
#   -d  next development version; -SNAPSHOT is appended if missing. When omitted,
#       a pre-release such as X.Y.Z-beta.1 continues as X.Y.Z-SNAPSHOT, an X.Y.0
#       release as X.(Y+1).0-SNAPSHOT, and any other release takes the plugin's
#       default.
#   -v  maven-release-plugin version to run (default 3.1.1)
#   -s  skip tests in the release builds
#   -p  Maven profile to activate in the release builds and the SNAPSHOT deploy; repeatable
#   -D  system property for the release builds and the SNAPSHOT deploy, e.g.
#       altReleaseDeploymentRepository=id::default::url; repeatable, no whitespace

set -euo pipefail

usage() {
  echo "Usage: $(basename "$0") -r <release-version> [-d <development-version>] [-v <plugin-version>] [-s] [-p <profile>] [-D <property=value>]" >&2
  exit 1
}

RELEASE_VERSION=""
DEV_VERSION=""
PLUGIN_VERSION="3.1.1"
FORKED_ARGS=""
DEPLOY_ARGS=()

while getopts "r:d:v:p:sD:" opt; do
  case $opt in
    r) RELEASE_VERSION=$OPTARG ;;
    d) DEV_VERSION=$OPTARG ;;
    v) PLUGIN_VERSION=$OPTARG ;;
    p)
      DEPLOY_ARGS+=("-P$OPTARG")
      FORKED_ARGS="${FORKED_ARGS:+$FORKED_ARGS }-P$OPTARG" ;;
    s) FORKED_ARGS="${FORKED_ARGS:+$FORKED_ARGS }-DskipTests" ;;
    D)
      # -Darguments is split on whitespace in the forked builds.
      if [[ "$OPTARG" =~ [[:space:]] ]]; then
        echo "::error::-D value must not contain whitespace: $OPTARG" >&2
        exit 1
      fi
      DEPLOY_ARGS+=("-D$OPTARG")
      FORKED_ARGS="${FORKED_ARGS:+$FORKED_ARGS }-D$OPTARG" ;;
    *) usage ;;
  esac
done

[ -n "$RELEASE_VERSION" ] || usage
RELEASE_PLUGIN="org.apache.maven.plugins:maven-release-plugin:$PLUGIN_VERSION"

LOCAL_REPO=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/release.XXXXXXX")
# -Darguments must stay one token so the forked release builds receive every flag.
ARGS=(-B -Dmaven.repo.local="$LOCAL_REPO" -DreleaseVersion="$RELEASE_VERSION" -Darguments="$FORKED_ARGS")

if [ -n "$DEV_VERSION" ]; then
  ARGS+=(-DdevelopmentVersion="${DEV_VERSION%-SNAPSHOT}-SNAPSHOT")
elif [[ "$RELEASE_VERSION" =~ ^([0-9]+\.[0-9]+\.[0-9]+)-.+$ ]]; then
  # Otherwise the plugin would continue a pre-release line, e.g. 1.2.0-beta.2-SNAPSHOT.
  ARGS+=(-DdevelopmentVersion="${BASH_REMATCH[1]}-SNAPSHOT")
elif [[ "$RELEASE_VERSION" =~ ^([0-9]+)\.([0-9]+)\.0$ ]]; then
  # https://talk.openmrs.org/t/releasing-modules-using-semantic-versioning/7797/15
  ARGS+=(-DdevelopmentVersion="${BASH_REMATCH[1]}.$((BASH_REMATCH[2] + 1)).0-SNAPSHOT")
fi

echo "Running $RELEASE_PLUGIN:prepare ${ARGS[*]}"
if ! mvn "$RELEASE_PLUGIN:prepare" "${ARGS[@]}"; then
  echo "::error::release:prepare failed; attempting release:rollback. Delete the release tag from the remote if it was pushed." >&2
  mvn "$RELEASE_PLUGIN:rollback" "${ARGS[@]}" || true
  exit 1
fi

echo "Running $RELEASE_PLUGIN:perform ${ARGS[*]}"
if ! mvn "$RELEASE_PLUGIN:perform" "${ARGS[@]}"; then
  echo "::error::release:perform failed after the release tag was pushed. Fix the problem and release the next version." >&2
  exit 1
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "released=true" >> "$GITHUB_OUTPUT"; fi

echo "Deploying the next development SNAPSHOT"
if ! mvn -B deploy -DskipTests ${DEPLOY_ARGS[@]+"${DEPLOY_ARGS[@]}"}; then
  echo "::error::$RELEASE_VERSION was released, but deploying the next development SNAPSHOT failed. Check the POM's <snapshotRepository> or the snapshot repository URL input; the release itself does not need to be repeated." >&2
  exit 1
fi
