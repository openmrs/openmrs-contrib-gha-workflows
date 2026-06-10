#!/usr/bin/env bash
#
# Backfill GPG signatures for Maven artifacts in JFrog Artifactory.
#
# Lists artifacts under a Maven coordinate prefix in a target Artifactory repo
# via AQL, then for each artifact backfills whatever is missing: a detached GPG
# signature of the artifact (.asc, plus .asc.md5/.asc.sha1 checksums of it),
# the artifact checksums (.md5/.sha1/.sha256), and a detached signature of each
# of those checksums (.md5.asc/.sha1.asc/.sha256.asc). Only missing files are
# generated and PUT back to the same path.
#
# Required env:
#   ARTIFACTORY_USER     Artifactory username
#   ARTIFACTORY_TOKEN    Artifactory identity token / API key (sent via Basic auth)
#   GPG_PRIVATE_KEY      ASCII-armored private key block
#   GPG_PASSPHRASE       Passphrase for the key
#
# Optional env:
#   ARTIFACTORY_URL      Base URL (default: https://openmrs.jfrog.io/artifactory)
#
# Usage:
#   scripts/backfill-gpg-signatures.sh --repo modules
#   scripts/backfill-gpg-signatures.sh --repo modules --maven-prefix org.openmrs.module.xforms --apply
#
# Defaults to dry-run. Pass --apply to actually upload signatures and checksums.

set -euo pipefail

ARTIFACTORY_URL="${ARTIFACTORY_URL:-https://openmrs.jfrog.io/artifactory}"
MAVEN_PREFIX="org.openmrs"
REPO=""
APPLY="false"
PAGE_SIZE=1000

# curl timeouts (seconds)
TIMEOUT_HEAD=30
TIMEOUT_AQL=60
TIMEOUT_DOWNLOAD=600
TIMEOUT_UPLOAD=300

usage() {
  cat <<EOF
Usage: $0 --repo REPO [options]

Required:
  --repo REPO              Artifactory repo key (e.g. modules, modules-snapshots, public)

Options:
  --maven-prefix PREFIX    Maven coordinate prefix; matches anything starting
                           with this. Default: org.openmrs. Examples:
                             org.openmrs                       (all artifacts)
                             org.openmrs.module                (all modules)
                             org.openmrs.module.xforms         (one module)
                             org.openmrs.module.xforms.xforms-omod  (one artifactId)
  --artifactory-url URL    Artifactory base URL (default: \$ARTIFACTORY_URL or
                           https://openmrs.jfrog.io/artifactory)
  --apply                  Actually upload signatures and checksums
                           (default: dry-run)
  -h, --help               Show this help

Required env: ARTIFACTORY_USER, ARTIFACTORY_TOKEN, GPG_PRIVATE_KEY, GPG_PASSPHRASE
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)             REPO="$2"; shift 2 ;;
    --maven-prefix)     MAVEN_PREFIX="$2"; shift 2 ;;
    --artifactory-url)  ARTIFACTORY_URL="$2"; shift 2 ;;
    --apply)            APPLY="true"; shift ;;
    -h|--help)          usage; exit 0 ;;
    *)                  echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

[ -n "$REPO" ] || { echo "error: --repo is required" >&2; usage >&2; exit 1; }
ARTIFACTORY_URL="${ARTIFACTORY_URL%/}"

case "$ARTIFACTORY_URL" in
  https://*) ;;
  *) echo "error: artifactory URL must use https://: $ARTIFACTORY_URL" >&2; exit 1 ;;
esac

for var in ARTIFACTORY_USER ARTIFACTORY_TOKEN GPG_PRIVATE_KEY GPG_PASSPHRASE; do
  if [ -z "${!var:-}" ]; then
    echo "error: env $var must be set" >&2
    exit 1
  fi
done

for cmd in jq gpg curl awk md5sum sha1sum sha256sum; do
  if ! command -v "$cmd" >/dev/null; then
    echo "error: $cmd is required" >&2
    exit 1
  fi
done

prefix_path="$(echo "$MAVEN_PREFIX" | tr '.' '/')"
artifactory_host="$(echo "$ARTIFACTORY_URL" | awk -F/ '{print $3}')"

# Throwaway GNUPGHOME so we don't pollute the operator's keyring.
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
# line. Used for both the artifact and each of its checksum files.
gpg_sign() {
  local f="$1"
  printf '%s' "$GPG_PASSPHRASE" \
    | gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
          --detach-sign --armor "$f"
}

# Backfill one artifact checksum and (optionally) its detached signature.
#   $1 ext       — md5 | sha1 | sha256
#   $2 sumcmd    — md5sum | sha1sum | sha256sum
#   $3 cs_state  — url_exists state of $url.$ext      (0 present, else missing)
#   $4 sig_state — url_exists state of $url.$ext.asc  (0 present, else missing)
# Reads globals filepath/url/NETRC/TIMEOUT_DOWNLOAD and appends missing items to
# the to_upload array. Returns non-zero on any compute/fetch/sign failure so the
# caller can mark the artifact failed and skip it.
prepare_checksum() {
  local ext="$1" sumcmd="$2" cs_state="$3" sig_state="$4"
  local csfile="$filepath.$ext" expected actual

  if [ "$cs_state" != 0 ]; then
    # Absent on the server: generate our canonical bare-digest form.
    if ! "$sumcmd" "$filepath" | awk '{print $1}' > "$csfile"; then
      echo "error: failed to compute $ext checksum" >&2
      return 1
    fi
    to_upload+=("$ext")
    # Regenerating the checksum makes any pre-existing $ext.asc on the server
    # suspect: it may have signed different bytes (e.g. a "digest  filename"
    # format from other tooling). Treat it as dirty and re-sign below.
    sig_state=1
  elif [ "$sig_state" != 0 ]; then
    # Present but unsigned: sign the server's exact bytes. A regenerated copy
    # could differ in format/whitespace and fail to verify against what's
    # actually published, so fetch the canonical file first.
    if ! http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
                       --netrc-file "$NETRC" -o "$csfile" "$url.$ext"; then
      echo "error: failed to fetch existing $url.$ext to sign" >&2
      return 1
    fi
    # Never certify a checksum we haven't validated: signing a corrupt legacy
    # checksum would upgrade an integrity bug into an authenticity claim made
    # by our key. Compare against the artifact already on disk. First-token
    # parse tolerates "digest  filename" layouts; tolower tolerates uppercase
    # hex.
    expected="$("$sumcmd" "$filepath" | awk '{print $1}')" || expected=""
    actual="$(awk '{print tolower($1); exit}' "$csfile")" || actual=""
    # Reject empty values before comparing: set -e is suppressed in this
    # function (callers use `if !`), so a failed sumcmd or an empty/garbled
    # server file would otherwise yield "" == "" and certify junk.
    if [ -z "$expected" ] || [ -z "$actual" ]; then
      echo "error: could not compute/parse $ext checksum for validation (computed='$expected' server='$actual')" >&2
      return 1
    fi
    if [ "$expected" != "$actual" ]; then
      echo "error: server $ext checksum does not match artifact (server=$actual computed=$expected); refusing to sign" >&2
      return 1
    fi
  fi

  if [ "$sig_state" != 0 ]; then
    if ! gpg_sign "$csfile"; then
      echo "error: failed to sign $ext checksum" >&2
      return 1
    fi
    to_upload+=("$ext.asc")
  fi
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
echo "Imported $sec_count secret key(s):"
gpg --list-secret-keys --keyid-format=long

# Auth preflight: probe the repo config endpoint so a bad token or missing
# repo fails fast with a clear message instead of degrading into thousands of
# "deferred" HEADs.
echo ""
echo "Validating credentials against repo='$REPO'..."
probe_code="$(curl -sI -o /dev/null -w '%{http_code}' \
    --max-time 30 --netrc-file "$NETRC" \
    "$ARTIFACTORY_URL/api/repositories/$REPO" 2>/dev/null)" || probe_code="000"
case "$probe_code" in
  2*)  echo "Auth OK." ;;
  401) echo "error: 401 Unauthorized — check ARTIFACTORY_USER / ARTIFACTORY_TOKEN" >&2; exit 1 ;;
  403) echo "error: 403 Forbidden — token lacks read permission on repo '$REPO'" >&2; exit 1 ;;
  404) echo "error: 404 — repo '$REPO' does not exist at $ARTIFACTORY_URL" >&2; exit 1 ;;
  *)   echo "error: auth probe returned HTTP $probe_code from $ARTIFACTORY_URL/api/repositories/$REPO" >&2; exit 1 ;;
esac

echo ""
echo "Listing artifacts in repo='$REPO' under path='$prefix_path/*' via AQL..."

: > "$urls_file"
offset=0
total_listed=0
while :; do
  query="items.find({\"repo\":\"$REPO\",\"path\":{\"\$match\":\"$prefix_path/*\"},\"type\":\"file\"}).include(\"repo\",\"path\",\"name\").sort({\"\$asc\":[\"path\",\"name\"]}).offset($offset).limit($PAGE_SIZE)"

  if ! response="$(http_retry curl -fsSL \
        --max-time "$TIMEOUT_AQL" \
        --netrc-file "$NETRC" \
        -H 'Content-Type: text/plain' \
        --data-binary "$query" \
        "$ARTIFACTORY_URL/api/search/aql")"; then
    echo "error: AQL search failed at offset=$offset" >&2
    exit 1
  fi

  # Parse count and filter rows with explicit error handling. Buffering jq's
  # output before appending guarantees we never leave partial rows in
  # urls_file: if jq fails mid-stream we abort with no file mutation. This is
  # the one thing a one-off can't tolerate — a truncated listing followed by a
  # green Summary means the operator declares victory on a partial backfill.
  if ! count="$(echo "$response" | jq '(.results // []) | length')" || [ -z "$count" ]; then
    echo "error: failed to parse AQL response at offset=$offset" >&2
    echo "$response" | head -c 500 >&2
    exit 1
  fi
  [ "$count" = "0" ] && break

  if ! page_paths="$(echo "$response" | jq -r '
        (.results // [])[]
        | "\(.repo)/\(.path)/\(.name)"
        | select(
            (test("\\.(asc|md5|sha1|sha256|sha512)$") | not)
            and (test("/maven-metadata\\.xml(\\.[^/]+)?$") | not)
          )
      ')"; then
    echo "error: failed to extract paths from AQL response at offset=$offset" >&2
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
  echo "  page offset=$offset got=$count signable=$page_signable (cumulative=$total_listed)"
  offset=$((offset + PAGE_SIZE))

  # AQL has no continuation token; a short page means we've hit the end.
  [ "$count" -lt "$PAGE_SIZE" ] && break
done

awk '!seen[$0]++' "$urls_file" > "$urls_file.tmp" && mv "$urls_file.tmp" "$urls_file"
total="$(wc -l < "$urls_file" | tr -d ' ')"
echo ""
echo "Found $total signable paths."
[ "$total" = "0" ] && exit 0

mode_label="DRY-RUN"
[ "$APPLY" = "true" ] && mode_label="APPLY"
echo "Mode: $mode_label"

processed=0
skipped=0
failed=0
deferred=0
last_uploaded=""
last_uploaded_exts=""
deferred_file="$WORKDIR/deferred.txt"
failed_file="$WORKDIR/failed.txt"
: > "$deferred_file"
: > "$failed_file"

# Circuit breaker: bail out if too many artifacts in a row defer. A bad token
# or Artifactory outage manifests as every HEAD returning 401/403/5xx, and we
# don't want to grind through thousands of artifacts only to exit 2 looking
# like a transient probe glitch.
consecutive_defers=0
DEFER_CIRCUIT_BREAKER=5

while IFS= read -r relpath; do
  [ -z "$relpath" ] && continue
  url="$ARTIFACTORY_URL/$relpath"

  echo ""
  echo "=== $relpath ==="

  # Signature of the artifact, and the checksums of that signature.
  asc_state=0; ascmd5_state=0; ascsha1_state=0
  url_exists "$url.asc"      || asc_state=$?
  url_exists "$url.asc.md5"  || ascmd5_state=$?
  url_exists "$url.asc.sha1" || ascsha1_state=$?

  # Artifact checksums, and the detached signature of each.
  md5_state=0; sha1_state=0; sha256_state=0
  md5asc_state=0; sha1asc_state=0; sha256asc_state=0
  url_exists "$url.md5"        || md5_state=$?
  url_exists "$url.sha1"       || sha1_state=$?
  url_exists "$url.sha256"     || sha256_state=$?
  url_exists "$url.md5.asc"    || md5asc_state=$?
  url_exists "$url.sha1.asc"   || sha1asc_state=$?
  url_exists "$url.sha256.asc" || sha256asc_state=$?

  if [ "$asc_state" = 2 ]    || [ "$ascmd5_state" = 2 ]  || [ "$ascsha1_state" = 2 ] \
  || [ "$md5_state" = 2 ]    || [ "$sha1_state" = 2 ]    || [ "$sha256_state" = 2 ] \
  || [ "$md5asc_state" = 2 ] || [ "$sha1asc_state" = 2 ] || [ "$sha256asc_state" = 2 ]; then
    echo "warn: HEAD probe unreliable; deferring (rerun to retry)"
    echo "$relpath" >> "$deferred_file"
    deferred=$((deferred + 1))
    consecutive_defers=$((consecutive_defers + 1))
    if [ "$consecutive_defers" -ge "$DEFER_CIRCUIT_BREAKER" ]; then
      echo "error: $consecutive_defers consecutive HEAD failures — aborting." >&2
      echo "       Likely cause: token revoked mid-run, Artifactory unavailable," >&2
      echo "       or read permission scoped differently from search permission." >&2
      if [ -s "$deferred_file" ]; then
        echo "" >&2
        echo "Deferred so far:" >&2
        sed 's/^/  - /' "$deferred_file" >&2
      fi
      exit 1
    fi
    continue
  fi
  consecutive_defers=0

  if [ "$asc_state" = 0 ]    && [ "$ascmd5_state" = 0 ]  && [ "$ascsha1_state" = 0 ] \
  && [ "$md5_state" = 0 ]    && [ "$sha1_state" = 0 ]    && [ "$sha256_state" = 0 ] \
  && [ "$md5asc_state" = 0 ] && [ "$sha1asc_state" = 0 ] && [ "$sha256asc_state" = 0 ]; then
    echo "Signatures and checksums already present; skipping."
    skipped=$((skipped + 1))
    continue
  fi

  iterdir="$(mktemp -d -p "$WORKDIR")"
  filename="$(basename "$relpath")"
  filepath="$iterdir/$filename"
  asc_path="$filepath.asc"

  if [ "$asc_state" = 0 ]; then
    # Partial state: .asc on the server is canonical. Fetch it (and the
    # artifact) and verify before reusing — re-signing would produce a
    # different .asc (signatures are non-deterministic) and a re-PUT may be
    # blocked by the repo's redeploy policy. Verifying guards against trusting
    # a corrupted or wrong-key .asc that was uploaded previously.
    echo "Partial state: .asc exists, fetching .asc + artifact to verify"
    if ! http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
                       --netrc-file "$NETRC" -o "$asc_path" "$url.asc"; then
      echo "error: failed to fetch existing $url.asc" >&2
      echo "$relpath" >> "$failed_file"
      failed=$((failed + 1))
      rm -rf "$iterdir"
      continue
    fi
    if ! http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
                       --netrc-file "$NETRC" -o "$filepath" "$url"; then
      echo "error: failed to fetch artifact for verification: $url" >&2
      echo "$relpath" >> "$failed_file"
      failed=$((failed + 1))
      rm -rf "$iterdir"
      continue
    fi
    if ! gpg --batch --verify "$asc_path" "$filepath"; then
      echo "error: existing .asc on server failed gpg --verify; refusing to reuse: $url.asc" >&2
      echo "$relpath" >> "$failed_file"
      failed=$((failed + 1))
      rm -rf "$iterdir"
      continue
    fi
  else
    if ! http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
                       --netrc-file "$NETRC" -o "$filepath" "$url"; then
      echo "error: download failed: $url" >&2
      echo "$relpath" >> "$failed_file"
      failed=$((failed + 1))
      rm -rf "$iterdir"
      continue
    fi

    if ! gpg_sign "$filepath"; then
      echo "error: sign failed: $filename" >&2
      echo "$relpath" >> "$failed_file"
      failed=$((failed + 1))
      rm -rf "$iterdir"
      continue
    fi
  fi

  # A regenerated .asc has brand-new bytes (GPG signatures are
  # non-deterministic), so any .asc.md5/.asc.sha1 already on the server
  # describe a previous signature — e.g. left behind when an earlier run
  # uploaded the sidecars but the .asc PUT itself failed. Treat them as dirty
  # and regenerate + re-upload both alongside the new .asc.
  if [ "$asc_state" != 0 ]; then
    ascmd5_state=1
    ascsha1_state=1
  fi

  # Build the upload list as each sidecar is produced. Initialise it here so
  # prepare_checksum (below) can append to it.
  to_upload=()
  [ "$asc_state"     != 0 ] && to_upload+=("asc")
  [ "$ascmd5_state"  != 0 ] && to_upload+=("asc.md5")
  [ "$ascsha1_state" != 0 ] && to_upload+=("asc.sha1")

  # Checksums of the artifact's .asc signature (Maven-style bare digest).
  [ "$ascmd5_state"  != 0 ] && md5sum  "$asc_path" | awk '{print $1}' > "$asc_path.md5"
  [ "$ascsha1_state" != 0 ] && sha1sum "$asc_path" | awk '{print $1}' > "$asc_path.sha1"

  # Artifact checksums (.md5/.sha1/.sha256) and a detached signature of each.
  # Each call generates or fetches the checksum, signs it when the signature is
  # missing, and appends whatever needs uploading to to_upload.
  if ! prepare_checksum md5    md5sum    "$md5_state"    "$md5asc_state" \
    || ! prepare_checksum sha1   sha1sum   "$sha1_state"   "$sha1asc_state" \
    || ! prepare_checksum sha256 sha256sum "$sha256_state" "$sha256asc_state"; then
    echo "$relpath" >> "$failed_file"
    failed=$((failed + 1))
    rm -rf "$iterdir"
    continue
  fi

  if [ "$APPLY" != "true" ]; then
    for ext in "${to_upload[@]}"; do
      echo "[dry-run] would PUT $url.$ext"
    done
    processed=$((processed + 1))
    rm -rf "$iterdir"
    continue
  fi

  upload_failed=0
  for ext in "${to_upload[@]}"; do
    if ! http_retry curl -fsSL -o /dev/null \
                       --max-time "$TIMEOUT_UPLOAD" \
                       --netrc-file "$NETRC" \
                       -T "$filepath.$ext" "$url.$ext"; then
      echo "error: upload failed: $filename.$ext" >&2
      upload_failed=1
    fi
  done

  if [ "$upload_failed" = "0" ]; then
    processed=$((processed + 1))
    last_uploaded="$relpath"
    last_uploaded_exts="${to_upload[*]}"
  else
    echo "$relpath" >> "$failed_file"
    failed=$((failed + 1))
  fi

  rm -rf "$iterdir"
done < "$urls_file"

echo ""
echo "Summary [$mode_label]: processed=$processed skipped=$skipped deferred=$deferred failed=$failed"

if [ -s "$deferred_file" ]; then
  echo ""
  echo "Deferred (HEAD probe unreliable; rerun to retry):"
  sed 's/^/  - /' "$deferred_file"
fi

if [ -s "$failed_file" ]; then
  echo ""
  echo "Failed (download/sign/upload error after retries):"
  sed 's/^/  - /' "$failed_file"
fi

# Post-upload sanity check: re-fetch exactly the files this run uploaded for
# the most recently processed artifact and verify each one — signatures via
# gpg --verify against their subject, checksums by recomputing the subject's
# digest. Catches truncated/corrupted uploads and signature-subject mismatches
# before the operator declares victory, including runs that only uploaded
# checksums. It does NOT catch "wrong key imported": gpg --verify runs against
# the same throwaway keyring the signature was made with, so any successfully
# imported key verifies its own signatures.
if [ "$APPLY" = "true" ] && [ -n "$last_uploaded" ]; then
  echo ""
  echo "Verifying uploaded files for: $last_uploaded ($last_uploaded_exts)"
  verify_dir="$(mktemp -d -p "$WORKDIR")"
  url="$ARTIFACTORY_URL/$last_uploaded"
  verify_failures=0

  # Fetch $url$1 into the verify dir once; $1 is the suffix ("" = artifact).
  # Subjects of a signature/checksum may pre-date this run, so files outside
  # $last_uploaded_exts get fetched on demand too.
  vfetch() {
    local out="$verify_dir/artifact$1"
    [ -s "$out" ] || http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
                       --netrc-file "$NETRC" -o "$out" "$url$1"
  }
  vfail() {
    echo "error: post-upload verification failed: $1" >&2
    verify_failures=$((verify_failures + 1))
  }

  if ! vfetch ""; then
    vfail "could not fetch artifact"
  else
    for ext in $last_uploaded_exts; do
      case "$ext" in
        asc)
          { vfetch ".asc" \
            && gpg --batch --verify "$verify_dir/artifact.asc" "$verify_dir/artifact"; } \
            || vfail ".asc does not verify against the artifact" ;;
        md5|sha1|sha256)
          if vfetch ".$ext"; then
            expected="$("${ext}sum" "$verify_dir/artifact" | awk '{print $1}')"
            actual="$(awk '{print tolower($1); exit}' "$verify_dir/artifact.$ext")"
            [ "$expected" = "$actual" ] || vfail ".$ext does not match the artifact digest"
          else
            vfail "could not fetch .$ext"
          fi ;;
        md5.asc|sha1.asc|sha256.asc)
          base=".${ext%.asc}"
          { vfetch "$base" && vfetch ".$ext" \
            && gpg --batch --verify "$verify_dir/artifact.$ext" "$verify_dir/artifact$base"; } \
            || vfail ".$ext does not verify against $base" ;;
        asc.md5|asc.sha1)
          algo="${ext#asc.}"
          if vfetch ".asc" && vfetch ".$ext"; then
            expected="$("${algo}sum" "$verify_dir/artifact.asc" | awk '{print $1}')"
            actual="$(awk '{print tolower($1); exit}' "$verify_dir/artifact.$ext")"
            [ "$expected" = "$actual" ] || vfail ".$ext does not match the .asc digest"
          else
            vfail "could not fetch .asc / .$ext"
          fi ;;
        *)
          vfail "unknown extension in upload set: .$ext" ;;
      esac
    done
  fi

  if [ "$verify_failures" = 0 ]; then
    echo "Verification OK."
  else
    echo "error: post-upload verification FAILED for $last_uploaded ($verify_failures problem(s))" >&2
    exit 1
  fi
fi

# Exit codes (failed takes precedence over deferred):
#   0 — clean run (all uploads succeeded or were already present)
#   1 — at least one artifact failed (download/sign/upload error)
#   2 — at least one artifact was deferred (HEAD probe unreliable); a rerun
#       may resolve it without operator intervention
if [ "$failed" -gt 0 ]; then
  exit 1
fi
if [ "$deferred" -gt 0 ]; then
  exit 2
fi
exit 0
