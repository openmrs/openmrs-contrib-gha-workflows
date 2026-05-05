#!/usr/bin/env bash
#
# Backfill GPG signatures for Maven artifacts in JFrog Artifactory.
#
# Lists artifacts under a groupId prefix in a target Artifactory repo via AQL,
# then for each artifact missing a .asc file: download, detach-sign, and PUT
# the .asc (+ .asc.md5, .asc.sha1) back to the same path.
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
#   scripts/backfill-gpg-signatures.sh --repo modules --group-prefix org.openmrs --apply
#
# Defaults to dry-run. Pass --apply to actually upload signatures.

set -euo pipefail

ARTIFACTORY_URL="${ARTIFACTORY_URL:-https://openmrs.jfrog.io/artifactory}"
GROUP_PREFIX="org.openmrs"
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
  --group-prefix PREFIX    Maven groupId prefix (default: org.openmrs)
  --artifactory-url URL    Artifactory base URL (default: \$ARTIFACTORY_URL or
                           https://openmrs.jfrog.io/artifactory)
  --apply                  Actually upload signatures (default: dry-run)
  -h, --help               Show this help

Required env: ARTIFACTORY_USER, ARTIFACTORY_TOKEN, GPG_PRIVATE_KEY, GPG_PASSPHRASE
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)             REPO="$2"; shift 2 ;;
    --group-prefix)     GROUP_PREFIX="$2"; shift 2 ;;
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

for cmd in jq gpg curl awk; do
  if ! command -v "$cmd" >/dev/null; then
    echo "error: $cmd is required" >&2
    exit 1
  fi
done

group_path="$(echo "$GROUP_PREFIX" | tr '.' '/')"
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

# HEAD probe that distinguishes "definitely absent" (4xx, no retry) from
# "unsure" (5xx / network error, retried). Returns 0 if the URL exists.
# Exhausted retries are treated as "doesn't exist" so the caller proceeds with
# an upload — Artifactory will arbitrate via its redeploy policy.
url_exists() {
  local url="$1" attempt=1 max=3 backoff=2 code
  while [ "$attempt" -le "$max" ]; do
    code="$(curl -sI -o /dev/null -w '%{http_code}' \
            --max-time "$TIMEOUT_HEAD" \
            --netrc-file "$NETRC" "$url" 2>/dev/null)" || code="000"
    case "$code" in
      2*) return 0 ;;
      4*) return 1 ;;
    esac
    if [ "$attempt" -lt "$max" ]; then
      echo "  warn: HEAD $url returned '$code' (attempt $attempt/$max); retry in ${backoff}s" >&2
      sleep "$backoff"
      backoff=$((backoff * 2))
    fi
    attempt=$((attempt + 1))
  done
  return 1
}

echo "Importing GPG key into $GNUPGHOME ..."
gpg --batch --import <<< "$GPG_PRIVATE_KEY"
gpg --list-secret-keys --keyid-format=long

echo ""
echo "Listing artifacts in repo='$REPO' under path='$group_path/*' via AQL..."

: > "$urls_file"
offset=0
total_listed=0
while :; do
  query="items.find({\"repo\":\"$REPO\",\"path\":{\"\$match\":\"$group_path/*\"},\"type\":\"file\"}).include(\"repo\",\"path\",\"name\").sort({\"\$asc\":[\"path\",\"name\"]}).offset($offset).limit($PAGE_SIZE)"

  if ! response="$(http_retry curl -fsSL \
        --max-time "$TIMEOUT_AQL" \
        --netrc-file "$NETRC" \
        -H 'Content-Type: text/plain' \
        --data-binary "$query" \
        "$ARTIFACTORY_URL/api/search/aql")"; then
    echo "error: AQL search failed at offset=$offset" >&2
    exit 1
  fi

  count="$(echo "$response" | jq '.results | length')"
  if [ "$count" = "0" ]; then break; fi

  echo "$response" | jq -r '.results[] | "\(.repo)/\(.path)/\(.name)"' \
    | grep -vE '\.(asc|md5|sha1|sha256|sha512)$' \
    | grep -vE '/maven-metadata\.xml(\.[^/]+)?$' \
    >> "$urls_file" || true

  total_listed=$((total_listed + count))
  echo "  page offset=$offset got=$count (cumulative=$total_listed)"
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
last_uploaded=""

while IFS= read -r relpath; do
  [ -z "$relpath" ] && continue
  url="$ARTIFACTORY_URL/$relpath"

  echo ""
  echo "=== $relpath ==="

  asc_exists=0; md5_exists=0; sha1_exists=0
  url_exists "$url.asc"      && asc_exists=1
  url_exists "$url.asc.md5"  && md5_exists=1
  url_exists "$url.asc.sha1" && sha1_exists=1

  if [ "$asc_exists" = 1 ] && [ "$md5_exists" = 1 ] && [ "$sha1_exists" = 1 ]; then
    echo "Signature already present; skipping."
    skipped=$((skipped + 1))
    continue
  fi

  iterdir="$(mktemp -d -p "$WORKDIR")"
  filename="$(basename "$relpath")"
  filepath="$iterdir/$filename"
  asc_path="$filepath.asc"

  if [ "$asc_exists" = 1 ]; then
    # Partial state: .asc on the server is canonical. Fetch it and rebuild the
    # missing companions from it — re-signing would produce a different .asc
    # (signatures are non-deterministic) and a re-PUT may be blocked by the
    # repo's redeploy policy.
    echo "Partial state: .asc exists, fetching it to rebuild missing companions"
    if ! http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
                       --netrc-file "$NETRC" -o "$asc_path" "$url.asc"; then
      echo "error: failed to fetch existing $url.asc" >&2
      failed=$((failed + 1))
      rm -rf "$iterdir"
      continue
    fi
  else
    if ! http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
                       --netrc-file "$NETRC" -o "$filepath" "$url"; then
      echo "error: download failed: $url" >&2
      failed=$((failed + 1))
      rm -rf "$iterdir"
      continue
    fi

    if ! gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
           --detach-sign --armor "$filepath" <<< "$GPG_PASSPHRASE"; then
      echo "error: sign failed: $filename" >&2
      failed=$((failed + 1))
      rm -rf "$iterdir"
      continue
    fi
  fi

  md5sum  "$asc_path" | awk '{print $1}' > "$asc_path.md5"
  sha1sum "$asc_path" | awk '{print $1}' > "$asc_path.sha1"

  # Build the list of missing extensions to upload.
  to_upload=()
  [ "$asc_exists"  = 0 ] && to_upload+=("asc")
  [ "$md5_exists"  = 0 ] && to_upload+=("asc.md5")
  [ "$sha1_exists" = 0 ] && to_upload+=("asc.sha1")

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
  else
    failed=$((failed + 1))
  fi

  rm -rf "$iterdir"
done < "$urls_file"

echo ""
echo "Summary [$mode_label]: processed=$processed skipped=$skipped failed=$failed"

# Post-upload sanity check: fetch the most recently signed pair back from the
# server and verify it. Catches "wrong key imported" / "uploads silently
# corrupted" before the operator declares victory.
if [ "$APPLY" = "true" ] && [ -n "$last_uploaded" ]; then
  echo ""
  echo "Verifying uploaded signature: $last_uploaded"
  verify_dir="$(mktemp -d -p "$WORKDIR")"
  url="$ARTIFACTORY_URL/$last_uploaded"
  if http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
       --netrc-file "$NETRC" -o "$verify_dir/artifact" "$url" \
  && http_retry curl -fsSL --max-time "$TIMEOUT_DOWNLOAD" \
       --netrc-file "$NETRC" -o "$verify_dir/artifact.asc" "$url.asc" \
  && gpg --verify "$verify_dir/artifact.asc" "$verify_dir/artifact"; then
    echo "Verification OK."
  else
    echo "error: post-upload verification FAILED for $last_uploaded" >&2
    exit 1
  fi
fi

[ "$failed" = "0" ]