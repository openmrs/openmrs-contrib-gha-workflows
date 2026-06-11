#!/usr/bin/env bash
#
# Backfill GPG signatures for Maven artifacts in JFrog Artifactory.
#
# Lists artifacts under a Maven coordinate prefix in a target Artifactory repo
# via AQL, then for each artifact ensures a detached GPG signature (.asc)
# exists:
#   - If the .asc is missing, the artifact is downloaded, signed, and the .asc
#     is PUT back to the same path.
#   - If the .asc already exists, the artifact and its .asc are downloaded and
#     verified with gpg --verify. A failed verify is flagged (it means a
#     corrupt signature, or one made by a key other than the configured signing
#     key — the throwaway keyring holds only that key, so any signature from a
#     different key fails to verify here). Existing signatures are never
#     overwritten.
#
# Checksums (.md5/.sha1/.sha256) are intentionally NOT generated: Artifactory
# computes and serves those itself for every deployed file, including for the
# .asc once uploaded. The GPG signature is the only thing it won't generate.
#
# When --m2-repo points at a local Maven repository (e.g. ~/.m2/repository), the
# artifact bytes are taken from the cache instead of downloaded whenever the
# cached file's SHA-1 matches Artifactory's server-computed digest (carried in
# the AQL listing); otherwise it falls back to downloading. This only speeds the
# run up — a stale or mismatched cache file is never signed or used to verify.
#
# Artifacts are processed concurrently (see --concurrency) via a bounded
# xargs pool; per-artifact work is independent and idempotent, so a killed run
# is resumed simply by rerunning.
#
# Required env:
#   ARTIFACTORY_USER     Artifactory username
#   ARTIFACTORY_TOKEN    Artifactory identity token / API key (sent via Basic auth)
#   GPG_PRIVATE_KEY      ASCII-armored private key block
#   GPG_PASSPHRASE       Passphrase for the key
#   GPG_KEY_FINGERPRINT  Full fingerprint of the expected signing key. Required
#                        for --apply: the script aborts unless the imported key
#                        matches, so a misconfigured key can never sign
#                        thousands of artifacts with the wrong identity. Spaces
#                        and case are ignored. Optional for dry-run.
#
# Optional env:
#   ARTIFACTORY_URL      Base URL (default: https://openmrs.jfrog.io/artifactory)
#
# Usage:
#   scripts/backfill-gpg-signatures.sh --repo modules
#   scripts/backfill-gpg-signatures.sh --repo modules --repo modules-snapshots
#   scripts/backfill-gpg-signatures.sh --repo modules --maven-prefix org.openmrs.module:xforms --apply
#
# Defaults to dry-run. Pass --apply to actually upload signatures.

set -euo pipefail

ARTIFACTORY_URL="${ARTIFACTORY_URL:-https://openmrs.jfrog.io/artifactory}"
MAVEN_PREFIX="org.openmrs"
REPOS=()
APPLY="false"
PAGE_SIZE=1000
CONCURRENCY=8
M2_REPO=""

# curl timeouts (seconds)
TIMEOUT_HEAD=30
TIMEOUT_AQL=60
TIMEOUT_DOWNLOAD=600
TIMEOUT_UPLOAD=300

# Circuit breaker: abort the whole run once this many artifacts have failed, or
# this many have deferred. A bad token, missing deploy permission, or an
# Artifactory outage manifests as every artifact failing/deferring; we don't
# want to grind through (and re-download) thousands before giving up. Under
# concurrency there is no meaningful "in a row" — several artifacts are in
# flight at once — so this is a cumulative count, not a consecutive streak.
CIRCUIT_BREAKER=5

usage() {
  cat <<EOF
Usage: $0 --repo REPO [--repo REPO ...] [options]

Required:
  --repo REPO              Artifactory repo key (e.g. modules, modules-snapshots,
                           public). Repeatable, and accepts a comma-separated
                           list; all listed repos are scanned in one run.

Options:
  --maven-prefix PREFIX    What to scan. Two forms:

                           (a) Dotted path prefix (no colon). Maps to a storage
                               PATH (dots -> slashes) and matches PATH/*. The
                               trailing segment must be a real directory.
                                 org.openmrs                 (all of org/openmrs)
                                 org.openmrs.module          (all modules)
                                 org.openmrs.module.xforms   (the xforms tree)

                           (b) groupId:artifactId (one colon). Matches that
                               artifact AND its hyphenated sub-projects:
                               GROUP/ART/* plus GROUP/ART-*. E.g.
                                 org.openmrs.module:xforms
                               hits xforms, xforms-api, xforms-omod, etc.

                           Default: org.openmrs
  --artifactory-url URL    Artifactory base URL (default: \$ARTIFACTORY_URL or
                           https://openmrs.jfrog.io/artifactory)
  --concurrency N          Number of artifacts to process in parallel
                           (default: $CONCURRENCY)
  --m2-repo PATH           Local Maven repository to reuse for artifact bytes
                           (e.g. ~/.m2/repository). A cached file is used only
                           when its SHA-1 matches Artifactory's; else download.
                           Default: disabled (always download).
  --apply                  Actually upload signatures (default: dry-run)
  -h, --help               Show this help

Required env: ARTIFACTORY_USER, ARTIFACTORY_TOKEN, GPG_PRIVATE_KEY,
              GPG_PASSPHRASE, and GPG_KEY_FINGERPRINT (the last required for --apply)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)             IFS=',' read -ra _repos <<< "$2"; REPOS+=("${_repos[@]}"); shift 2 ;;
    --maven-prefix)     MAVEN_PREFIX="$2"; shift 2 ;;
    --artifactory-url)  ARTIFACTORY_URL="$2"; shift 2 ;;
    --concurrency)      CONCURRENCY="$2"; shift 2 ;;
    --m2-repo)          M2_REPO="$2"; shift 2 ;;
    --apply)            APPLY="true"; shift ;;
    -h|--help)          usage; exit 0 ;;
    *)                  echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

[ "${#REPOS[@]}" -gt 0 ] || { echo "error: --repo is required" >&2; usage >&2; exit 1; }
ARTIFACTORY_URL="${ARTIFACTORY_URL%/}"

case "$CONCURRENCY" in
  ''|*[!0-9]*) echo "error: --concurrency must be a positive integer: $CONCURRENCY" >&2; exit 1 ;;
esac
[ "$CONCURRENCY" -ge 1 ] || { echo "error: --concurrency must be >= 1" >&2; exit 1; }

case "$ARTIFACTORY_URL" in
  https://*) ;;
  *) echo "error: artifactory URL must use https://: $ARTIFACTORY_URL" >&2; exit 1 ;;
esac

if [ -n "$M2_REPO" ]; then
  M2_REPO="${M2_REPO%/}"
  if [ ! -d "$M2_REPO" ]; then
    echo "warning: --m2-repo '$M2_REPO' is not a directory; every artifact will be downloaded" >&2
  fi
fi

for var in ARTIFACTORY_USER ARTIFACTORY_TOKEN GPG_PRIVATE_KEY GPG_PASSPHRASE; do
  if [ -z "${!var:-}" ]; then
    echo "error: env $var must be set" >&2
    exit 1
  fi
done

# Fail fast: applying without an expected fingerprint is exactly the
# wrong-key-signs-everything hazard this guard exists to prevent. (The match
# itself is checked after import, below.)
if [ "$APPLY" = "true" ] && [ -z "${GPG_KEY_FINGERPRINT:-}" ]; then
  echo "error: GPG_KEY_FINGERPRINT must be set for --apply (wrong-key protection)" >&2
  exit 1
fi

for cmd in jq gpg gpgconf curl awk xargs tr; do
  if ! command -v "$cmd" >/dev/null; then
    echo "error: $cmd is required" >&2
    exit 1
  fi
done

artifactory_host="$(echo "$ARTIFACTORY_URL" | awk -F/ '{print $3}')"

# Build the AQL path criterion from --maven-prefix. Two forms:
#   groupId:artifactId  -> match the artifact AND its hyphenated sub-projects,
#                          i.e. GROUP/ART/* (the artifact's own tree) OR
#                          GROUP/ART-* (siblings like ART-api, ART-omod). AQL
#                          '*' spans '/', so GROUP/ART/* also catches anything
#                          nested deeper under the artifact dir.
#   dotted prefix       -> a plain storage-path prefix, matched as PATH/*.
# $path_clause is an already-escaped JSON fragment spliced into the find object;
# its contents are inserted verbatim (shell does not re-expand a variable's
# value), so the literal $or/$match survive.
if [[ "$MAVEN_PREFIX" == *:* ]]; then
  prefix_group="${MAVEN_PREFIX%%:*}"
  prefix_artifact="${MAVEN_PREFIX#*:}"
  prefix_base="$(echo "$prefix_group" | tr '.' '/')/$prefix_artifact"
  path_clause="\"\$or\":[{\"path\":{\"\$match\":\"$prefix_base/*\"}},{\"path\":{\"\$match\":\"$prefix_base-*\"}}]"
  scope_desc="groupId:artifactId '$MAVEN_PREFIX' (path '$prefix_base/*' or '$prefix_base-*')"
else
  prefix_path="$(echo "$MAVEN_PREFIX" | tr '.' '/')"
  path_clause="\"path\":{\"\$match\":\"$prefix_path/*\"}"
  scope_desc="path '$prefix_path/*'"
fi

# Throwaway GNUPGHOME so we don't pollute the operator's keyring. Exported so
# the parallel workers (separate bash -c processes) share the imported key
# without re-importing it.
GNUPGHOME_TMP="$(mktemp -d)"
chmod 700 "$GNUPGHOME_TMP"
export GNUPGHOME="$GNUPGHOME_TMP"

WORKDIR="$(mktemp -d)"
chmod 700 "$WORKDIR"
NETRC="$WORKDIR/netrc"
urls_file="$WORKDIR/paths.txt"
trap 'rm -rf "$WORKDIR" "$GNUPGHOME_TMP"' EXIT

# Keep credentials out of the process command line; curl reads them from netrc.
{
  echo "machine $artifactory_host"
  echo "login $ARTIFACTORY_USER"
  echo "password $ARTIFACTORY_TOKEN"
} > "$NETRC"
chmod 600 "$NETRC"

# Generic retrying runner for AQL/downloads/uploads. Caller passes the full
# command. Retries any non-zero exit, so a definitive 4xx will burn the retry
# budget — acceptable for AQL/PUT/GET on URLs derived from AQL listings.
http_retry() {
  local max=3 attempt=1 backoff=2
  while [ "$attempt" -le "$max" ]; do
    if "$@"; then return 0; fi
    if [ "$attempt" -lt "$max" ]; then
      echo "  warn: request failed (attempt $attempt/$max); retrying in ${backoff}s" >&2
      sleep "$backoff"
      backoff=$((backoff * 2))
    fi
    attempt=$((attempt + 1))
  done
  return 1
}

# HEAD probe with three-state result:
#   0 — URL exists (2xx)
#   1 — URL definitely absent (404; no retry)
#   2 — unsure (5xx, network error, or other 4xx after exhausted retries —
#       e.g. 401/403/405/429 indicate a real problem worth surfacing rather
#       than papering over as "absent")
# Callers treat 2 as "defer this artifact" rather than risking a clobbering
# PUT or a needless re-sign.
url_exists() {
  local url="$1" attempt=1 max=3 backoff=2 code
  while [ "$attempt" -le "$max" ]; do
    code="$(curl -sI -o /dev/null -w '%{http_code}' \
            --max-time "$TIMEOUT_HEAD" \
            --netrc-file "$NETRC" "$url" 2>/dev/null)" || code="000"
    case "$code" in
      2*)  return 0 ;;
      404) return 1 ;;
    esac
    if [ "$attempt" -lt "$max" ]; then
      echo "  warn: HEAD $url returned '$code' (attempt $attempt/$max); retry in ${backoff}s" >&2
      sleep "$backoff"
      backoff=$((backoff * 2))
    fi
    attempt=$((attempt + 1))
  done
  return 2
}

# Create a detached, armored GPG signature ($f.asc) for $f using the imported
# key. Passphrase is piped via loopback pinentry so it never hits the command
# line. --local-user pins the signing key to the fingerprint validated at
# import, so a key block carrying more than one secret key can't sign with an
# unintended one.
gpg_sign() {
  local f="$1"
  printf '%s' "$GPG_PASSPHRASE" \
    | gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
          --local-user "$SIGNING_KEY" --detach-sign --armor "$f"
}

# Lowercase hex SHA-1 of a file, computed with gpg (already a dependency) to
# avoid a sha1sum/shasum portability split. Read via stdin so gpg emits only
# the digest, not the filename; strip grouping spaces and lowercase.
sha1_of() {
  gpg --print-md SHA1 < "$1" 2>/dev/null | tr -cd '0-9a-fA-F' | tr 'A-F' 'a-f'
}

# Ensure $filepath holds the artifact bytes. Prefer a local Maven cache copy
# whose SHA-1 matches Artifactory's server-computed digest ($want_sha1, carried
# from the AQL listing); otherwise download. A cache file that doesn't match
# (stale, locally built, or resolved from a different remote) is ignored — the
# cache can only ever speed the run up, never change which bytes get signed or
# verified. Appends progress to $out; returns non-zero only if the download
# fails. The cache file is copied into $filepath (not signed in place) so we
# never write a .asc into the operator's ~/.m2.
resolve_artifact() {
  if [ -n "$M2_REPO" ] && [ -n "$want_sha1" ]; then
    # relpath is "<repo>/<group-path>/<artifact>/<version>/<file>"; strip the
    # leading repo segment to get the Maven layout path, which is what ~/.m2
    # is keyed by (independent of which Artifactory repo it came from).
    local cache_file="$M2_REPO/${relpath#*/}"
    if [ -f "$cache_file" ]; then
      if [ "$(sha1_of "$cache_file")" = "$want_sha1" ]; then
        if cp "$cache_file" "$filepath"; then
          out+="cache hit: $cache_file
"
          return 0
        fi
        out+="warn: cache hit but copy failed; downloading: $cache_file
"
      else
        out+="cache miss (sha1 mismatch); downloading
"
      fi
    fi
  fi
  http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
    --netrc-file "$NETRC" -o "$filepath" "$url"
}

# ---------------------------------------------------------------------------
# Per-artifact worker. Run once per path by the xargs pool (see below), so it
# handles exactly one artifact and then exits. Outcomes are recorded by
# appending the path to one of the result files rather than mutating counters,
# since each worker is a separate process. Short single-line appends to a file
# opened O_APPEND are atomic, so concurrent workers don't corrupt each other.
#
# Exit codes:
#   0   — outcome recorded (processed / skipped / deferred / failed); pool continues
#   255 — circuit breaker tripped; xargs stops launching new workers
#
# The helper functions below intentionally reference the caller's locals
# (out/relpath/iterdir) via bash dynamic scope.
# ---------------------------------------------------------------------------

# Flush this worker's buffered, per-artifact log as a single write so blocks
# from concurrent workers stay readable instead of interleaving line by line.
_flush() { printf '%s' "$out"; }

# True if $1 (a result file) has reached the circuit-breaker threshold.
breaker_tripped() {
  local n
  n="$(wc -l < "$1" 2>/dev/null | tr -d ' ')"
  [ "${n:-0}" -ge "$CIRCUIT_BREAKER" ]
}

skip_artifact() {
  echo "$relpath" >> "$skipped_file"
  [ -n "${iterdir:-}" ] && rm -rf "$iterdir"
  _flush
  exit 0
}

defer_artifact() {
  echo "$relpath" >> "$deferred_file"
  [ -n "${iterdir:-}" ] && rm -rf "$iterdir"
  _flush
  if breaker_tripped "$deferred_file"; then : > "$stop_flag"; exit 255; fi
  exit 0
}

fail_artifact() {
  echo "$relpath" >> "$failed_file"
  [ -n "${iterdir:-}" ] && rm -rf "$iterdir"
  _flush
  if breaker_tripped "$failed_file"; then : > "$stop_flag"; exit 255; fi
  exit 0
}

# $1 — non-empty when a .asc was actually uploaded (empty in dry-run). Recorded
# so the post-run check can re-fetch and verify a real upload.
done_artifact() {
  echo "$relpath" >> "$processed_file"
  [ -n "${1:-}" ] && echo "$relpath" >> "$uploaded_file"
  [ -n "${iterdir:-}" ] && rm -rf "$iterdir"
  _flush
  exit 0
}

process_artifact() {
  # Each listing line is "relpath<TAB>actual_sha1" (sha1 may be empty).
  local line="$1"
  local relpath="${line%%$'\t'*}"
  local want_sha1=""
  [ "$line" != "$relpath" ] && want_sha1="${line#*$'\t'}"
  [ -z "$relpath" ] && exit 0
  # Honor a circuit breaker tripped by a sibling worker. Exit 255 so xargs
  # halts the pool even if it hasn't seen the first tripping worker yet.
  [ -e "$stop_flag" ] && exit 255

  local url="$ARTIFACTORY_URL/$relpath"
  local out="=== $relpath ===
"

  local asc_state=0
  url_exists "$url.asc" || asc_state=$?
  if [ "$asc_state" = 2 ]; then
    out+="warn: HEAD probe unreliable; deferring (rerun to retry)
"
    defer_artifact
  fi

  local iterdir
  iterdir="$(mktemp -d -p "$WORKDIR")" || { out+="error: mktemp failed
"; fail_artifact; }
  local filename filepath asc_path
  filename="$(basename "$relpath")"
  filepath="$iterdir/$filename"
  asc_path="$filepath.asc"

  if [ "$asc_state" = 0 ]; then
    # Already signed. Verify the published signature against the published
    # artifact rather than trusting it blindly. A failed verify means a corrupt
    # .asc or one made by a key other than the configured signing key (our
    # throwaway keyring holds only that key, so a signature from any other key
    # fails here). We flag it but never overwrite — re-PUT may be blocked and
    # clobbering a signature is dangerous.
    out+="Already signed; fetching artifact + .asc to verify
"
    if ! http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
                       --netrc-file "$NETRC" -o "$asc_path" "$url.asc"; then
      out+="error: failed to fetch existing $url.asc
"; fail_artifact
    fi
    if ! resolve_artifact; then
      out+="error: failed to obtain artifact for verification: $url
"; fail_artifact
    fi
    if gpg --batch --verify "$asc_path" "$filepath" 2>"$iterdir/gpg.err"; then
      out+="Existing .asc verified OK.
"
      skip_artifact
    fi
    out+="error: existing .asc failed gpg --verify (corrupt, or signed by a different key): $url.asc
"
    out+="$(sed 's/^/    /' "$iterdir/gpg.err" 2>/dev/null)
"
    fail_artifact
  fi

  # Not signed: obtain the artifact (cache or download), sign, upload the .asc.
  if ! resolve_artifact; then
    out+="error: download failed: $url
"; fail_artifact
  fi
  if ! gpg_sign "$filepath"; then
    out+="error: sign failed: $filename
"; fail_artifact
  fi

  if [ "$APPLY" != "true" ]; then
    out+="[dry-run] would PUT $url.asc
"
    done_artifact ""
  fi

  if ! http_retry curl -fsSL -o /dev/null \
                     --max-time "$TIMEOUT_UPLOAD" \
                     --netrc-file "$NETRC" \
                     -T "$asc_path" "$url.asc"; then
    out+="error: upload failed: $filename.asc
"
    fail_artifact
  fi
  done_artifact "uploaded"
}

echo "Importing GPG key into $GNUPGHOME ..."
# Pipe rather than here-string: bash here-strings can be implemented as a
# tempfile, leaking the private key onto disk in $TMPDIR.
if ! printf '%s' "$GPG_PRIVATE_KEY" | gpg --batch --import; then
  echo "error: gpg key import failed" >&2
  exit 1
fi
sec_count="$(gpg --list-secret-keys --with-colons 2>/dev/null | grep -c '^sec:' || true)"
if [ "$sec_count" -lt 1 ]; then
  echo "error: no secret keys present after import" >&2
  exit 1
fi

# Fingerprint of the imported primary key — used to pin --local-user when
# signing and to validate against the operator's expected fingerprint.
SIGNING_KEY="$(gpg --list-secret-keys --with-colons 2>/dev/null | awk -F: '/^fpr:/{print $10; exit}')"
if [ -z "$SIGNING_KEY" ]; then
  echo "error: could not read fingerprint of imported key" >&2
  exit 1
fi
export SIGNING_KEY

if [ -n "${GPG_KEY_FINGERPRINT:-}" ]; then
  # Normalize: strip whitespace, uppercase. gpg's colon output is already
  # uppercase hex with no spaces.
  want_fpr="$(printf '%s' "$GPG_KEY_FINGERPRINT" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"
  if [ "$want_fpr" != "$SIGNING_KEY" ]; then
    echo "error: imported key fingerprint does not match GPG_KEY_FINGERPRINT" >&2
    echo "       imported: $SIGNING_KEY" >&2
    echo "       expected: $want_fpr" >&2
    exit 1
  fi
  echo "Imported $sec_count secret key(s); fingerprint matches GPG_KEY_FINGERPRINT."
else
  # Only reachable in dry-run (--apply requires the fingerprint above).
  echo "WARNING: GPG_KEY_FINGERPRINT not set — wrong-key protection DISABLED (dry-run)." >&2
  echo "Imported $sec_count secret key(s):"
fi
gpg --list-secret-keys --keyid-format=long

# Auth preflight: probe the repo config endpoint so a bad token or missing
# repo fails fast with a clear message instead of degrading into thousands of
# "deferred" HEADs. NOTE: this only proves *read* on the repo config; deploy
# permission is separate, so a read-only token still passes here. A token that
# can read but not deploy is caught at runtime by the failure circuit breaker
# (it surfaces as the first handful of uploads failing).
echo ""
echo "Validating credentials against repo(s): ${REPOS[*]}..."
for repo in "${REPOS[@]}"; do
  probe_code="$(curl -sI -o /dev/null -w '%{http_code}' \
      --max-time 30 --netrc-file "$NETRC" \
      "$ARTIFACTORY_URL/api/repositories/$repo" 2>/dev/null)" || probe_code="000"
  case "$probe_code" in
    2*)  echo "  $repo: OK" ;;
    401) echo "error: 401 Unauthorized — check ARTIFACTORY_USER / ARTIFACTORY_TOKEN" >&2; exit 1 ;;
    403) echo "error: 403 Forbidden — token lacks read permission on repo '$repo'" >&2; exit 1 ;;
    404) echo "error: 404 — repo '$repo' does not exist at $ARTIFACTORY_URL" >&2; exit 1 ;;
    *)   echo "error: auth probe returned HTTP $probe_code from $ARTIFACTORY_URL/api/repositories/$repo" >&2; exit 1 ;;
  esac
done

echo ""
echo "Listing artifacts in repo(s) '${REPOS[*]}' matching $scope_desc via AQL..."

: > "$urls_file"
total_listed=0
for repo in "${REPOS[@]}"; do
  offset=0
  while :; do
    query="items.find({\"repo\":\"$repo\",\"type\":\"file\",$path_clause}).include(\"repo\",\"path\",\"name\",\"actual_sha1\").sort({\"\$asc\":[\"path\",\"name\"]}).offset($offset).limit($PAGE_SIZE)"

    if ! response="$(http_retry curl -fsSL \
          --max-time "$TIMEOUT_AQL" \
          --netrc-file "$NETRC" \
          -H 'Content-Type: text/plain' \
          --data-binary "$query" \
          "$ARTIFACTORY_URL/api/search/aql")"; then
      echo "error: AQL search failed for repo='$repo' at offset=$offset" >&2
      exit 1
    fi

    # Parse count and filter rows with explicit error handling. Buffering jq's
    # output before appending guarantees we never leave partial rows in
    # urls_file: if jq fails mid-stream we abort with no file mutation. This is
    # the one thing a one-off can't tolerate — a truncated listing followed by a
    # green Summary means the operator declares victory on a partial backfill.
    if ! count="$(echo "$response" | jq '(.results // []) | length')" || [ -z "$count" ]; then
      echo "error: failed to parse AQL response for repo='$repo' at offset=$offset" >&2
      echo "$response" | head -c 500 >&2
      exit 1
    fi
    [ "$count" = "0" ] && break

    # Sign every published file except checksums, existing signatures, and Maven
    # metadata. This covers .pom/.jar (incl. -sources/-javadoc/-tests and omods,
    # which publish as jars) as well as .war/.zip and any other packaging.
    if ! page_paths="$(echo "$response" | jq -r '
          (.results // [])[]
          | (.repo + "/" + .path + "/" + .name) as $p
          | select(
              ($p | test("\\.(asc|md5|sha1|sha256|sha512)$") | not)
              and ($p | test("/maven-metadata\\.xml(\\.[^/]+)?$") | not)
            )
          | "\($p)\t\(.actual_sha1 // "")"
        ')"; then
      echo "error: failed to extract paths from AQL response for repo='$repo' at offset=$offset" >&2
      echo "$response" | head -c 500 >&2
      exit 1
    fi

    before_lines="$(wc -l < "$urls_file" | tr -d ' ')"
    if [ -n "$page_paths" ]; then
      printf '%s\n' "$page_paths" >> "$urls_file"
    fi
    after_lines="$(wc -l < "$urls_file" | tr -d ' ')"
    page_signable=$((after_lines - before_lines))

    # Track signable items only; raw AQL rows include filtered-out checksums/metadata.
    total_listed=$((total_listed + page_signable))
    echo "  $repo offset=$offset got=$count signable=$page_signable (cumulative=$total_listed)"
    offset=$((offset + PAGE_SIZE))

    # AQL has no continuation token; a short page means we've hit the end.
    # Offset paging is not stable if files are added/removed mid-listing, which
    # could skip an item. For this backfill that's a non-issue: we target release
    # repos whose published files are immutable. (A few SNAPSHOTs deployed during
    # a run won't perturb the release ordering; rerun catches any stragglers.)
    [ "$count" -lt "$PAGE_SIZE" ] && break
  done
done

awk '!seen[$0]++' "$urls_file" > "$urls_file.tmp" && mv "$urls_file.tmp" "$urls_file"
total="$(wc -l < "$urls_file" | tr -d ' ')"
echo ""
echo "Found $total signable paths."
[ "$total" = "0" ] && exit 0

mode_label="DRY-RUN"
[ "$APPLY" = "true" ] && mode_label="APPLY"
echo "Mode: $mode_label (concurrency=$CONCURRENCY)"

# Result files: workers append a line per outcome instead of mutating shared
# counters (each worker is its own process). uploaded_file records artifacts
# whose .asc was actually PUT so the post-run check can verify a real upload.
# stop_flag's existence signals the circuit breaker tripped.
processed_file="$WORKDIR/processed.txt"
skipped_file="$WORKDIR/skipped.txt"
deferred_file="$WORKDIR/deferred.txt"
failed_file="$WORKDIR/failed.txt"
uploaded_file="$WORKDIR/uploaded.txt"
stop_flag="$WORKDIR/STOP"
: > "$processed_file"
: > "$skipped_file"
: > "$deferred_file"
: > "$failed_file"
: > "$uploaded_file"
rm -f "$stop_flag"

# Export everything the worker subshells need (functions + the globals they
# read). GNUPGHOME / SIGNING_KEY / GPG_PASSPHRASE carry the imported key and
# its passphrase so workers sign without re-importing.
export ARTIFACTORY_URL NETRC WORKDIR APPLY GPG_PASSPHRASE GPG_PRIVATE_KEY M2_REPO
export TIMEOUT_HEAD TIMEOUT_DOWNLOAD TIMEOUT_UPLOAD CIRCUIT_BREAKER
export processed_file skipped_file deferred_file failed_file uploaded_file stop_flag
export -f http_retry url_exists gpg_sign sha1_of resolve_artifact
export -f _flush breaker_tripped skip_artifact defer_artifact fail_artifact done_artifact process_artifact

# Spread the artifacts across ~CONCURRENCY worker processes (batched, ~per
# each) rather than one process per artifact. Each worker gets its OWN
# throwaway GNUPGHOME and imports the key once: GnuPG routes secret-key
# operations through a per-GNUPGHOME gpg-agent, and many workers sharing one
# agent is what produced intermittent "gpg: signing failed: Cannot allocate
# memory". Isolated agents remove that contention while network I/O stays fully
# parallel. Importing the key per worker means up to CONCURRENCY transient
# on-disk key copies (700-perm temp dirs, removed on worker exit) — same posture
# as the single throwaway keyring, just N of them.
per=$(( (total + CONCURRENCY - 1) / CONCURRENCY ))
[ "$per" -lt 1 ] && per=1

echo ""
echo "Processing $total artifacts with $CONCURRENCY workers (~$per each)..."
# NUL-delimit so paths with spaces/tabs survive. `|| true` because xargs exits
# non-zero when a worker returns 255 (circuit breaker) — the real outcome is
# read from the result files and stop_flag below, not xargs's exit status.
# Each artifact runs in a subshell so the helpers' `exit` ends only that
# artifact, not the worker's whole batch; a 255 (breaker) does propagate out.
tr '\n' '\0' < "$urls_file" \
  | xargs -0 -n "$per" -P "$CONCURRENCY" bash -c '
      set -uo pipefail
      GNUPGHOME="$(mktemp -d)"; export GNUPGHOME; chmod 700 "$GNUPGHOME"
      trap "gpgconf --kill all >/dev/null 2>&1 || true; rm -rf \"$GNUPGHOME\"" EXIT
      if ! printf "%s" "$GPG_PRIVATE_KEY" | gpg --batch --import >/dev/null 2>&1; then
        echo "error: worker GPG key import failed" >&2
        : > "$stop_flag"
        exit 255
      fi
      for item in "$@"; do
        ( process_artifact "$item" )
        [ $? -eq 255 ] && exit 255
      done
    ' _ \
  || true

processed="$(wc -l < "$processed_file" | tr -d ' ')"
skipped="$(wc -l < "$skipped_file" | tr -d ' ')"
deferred="$(wc -l < "$deferred_file" | tr -d ' ')"
failed="$(wc -l < "$failed_file" | tr -d ' ')"

echo ""
echo "Summary [$mode_label]: signed=$processed already-verified=$skipped deferred=$deferred failed=$failed"

if [ -s "$deferred_file" ]; then
  echo ""
  echo "Deferred (HEAD probe unreliable; rerun to retry):"
  sed 's/^/  - /' "$deferred_file"
fi

if [ -s "$failed_file" ]; then
  echo ""
  echo "Failed (download/sign/upload error, or existing .asc failed verify):"
  sed 's/^/  - /' "$failed_file"
fi

if [ -e "$stop_flag" ]; then
  echo "" >&2
  echo "error: circuit breaker tripped — >= $CIRCUIT_BREAKER artifacts failed or deferred; aborted early." >&2
  echo "       Likely cause: token revoked/lacking deploy permission, Artifactory" >&2
  echo "       unavailable, or read permission scoped differently from search." >&2
  echo "       Some workers already in flight may have completed after the trip." >&2
  exit 1
fi

# Post-upload sanity check: re-fetch one .asc this run uploaded and verify it
# against its artifact. Catches truncated/corrupted uploads before the operator
# declares victory. This deliberately samples a SINGLE artifact (the last
# recorded upload) as a smoke test — it is NOT a per-artifact guarantee across
# the whole run. It also does NOT catch "wrong key imported": gpg --verify runs
# against the same throwaway keyring the signature was made with, so any
# imported key verifies its own signatures. The GPG_KEY_FINGERPRINT check at
# import is what guards key identity.
last_uploaded=""
[ -s "$uploaded_file" ] && last_uploaded="$(tail -n 1 "$uploaded_file")"

if [ "$APPLY" = "true" ] && [ -n "$last_uploaded" ]; then
  echo ""
  echo "Verifying uploaded .asc for: $last_uploaded"
  verify_dir="$(mktemp -d -p "$WORKDIR")"
  vurl="$ARTIFACTORY_URL/$last_uploaded"
  if http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" --netrc-file "$NETRC" \
        -o "$verify_dir/artifact" "$vurl" \
     && http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" --netrc-file "$NETRC" \
        -o "$verify_dir/artifact.asc" "$vurl.asc" \
     && gpg --batch --verify "$verify_dir/artifact.asc" "$verify_dir/artifact"; then
    echo "Verification OK (sampled 1 of $processed newly signed artifact(s))."
  else
    echo "error: post-upload verification FAILED for $last_uploaded.asc" >&2
    exit 1
  fi
fi

# Exit codes (failed takes precedence over deferred):
#   0 — clean run (all .asc uploaded or already present and verified)
#   1 — at least one artifact failed (download/sign/upload error or existing
#       .asc failed verify), the circuit breaker tripped, or post-upload
#       verification failed
#   2 — at least one artifact was deferred (HEAD probe unreliable); a rerun
#       may resolve it without operator intervention
if [ "$failed" -gt 0 ]; then
  exit 1
fi
if [ "$deferred" -gt 0 ]; then
  exit 2
fi
exit 0
