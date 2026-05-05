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

# Isolate the imported key in a throwaway GNUPGHOME so we don't pollute the
# operator's keyring.
GNUPGHOME_TMP="$(mktemp -d)"
chmod 700 "$GNUPGHOME_TMP"
export GNUPGHOME="$GNUPGHOME_TMP"

WORKDIR="$(mktemp -d)"
urls_file="$WORKDIR/paths.txt"
trap 'rm -rf "$WORKDIR" "$GNUPGHOME_TMP"' EXIT

echo "Importing GPG key into $GNUPGHOME ..."
echo "$GPG_PRIVATE_KEY" | gpg --batch --import 2>&1
gpg --list-secret-keys --keyid-format=long

echo ""
echo "Listing artifacts in repo='$REPO' under path='$group_path/*' via AQL..."

: > "$urls_file"
offset=0
total_listed=0
while :; do
  query="items.find({\"repo\":\"$REPO\",\"path\":{\"\$match\":\"$group_path/*\"},\"type\":\"file\"}).include(\"repo\",\"path\",\"name\").sort({\"\$asc\":[\"path\",\"name\"]}).offset($offset).limit($PAGE_SIZE)"

  if ! response="$(http_retry curl -fsSL \
        -u "$ARTIFACTORY_USER:$ARTIFACTORY_TOKEN" \
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

while IFS= read -r relpath; do
  [ -z "$relpath" ] && continue
  url="$ARTIFACTORY_URL/$relpath"

  echo ""
  echo "=== $relpath ==="

  # Idempotency: skip when all three companion files already exist;
  # if any are missing, fall through and repair the partial state.
  if curl -sfI -u "$ARTIFACTORY_USER:$ARTIFACTORY_TOKEN" "$url.asc"      > /dev/null 2>&1 \
  && curl -sfI -u "$ARTIFACTORY_USER:$ARTIFACTORY_TOKEN" "$url.asc.md5"  > /dev/null 2>&1 \
  && curl -sfI -u "$ARTIFACTORY_USER:$ARTIFACTORY_TOKEN" "$url.asc.sha1" > /dev/null 2>&1; then
    echo "Signature already present; skipping."
    skipped=$((skipped + 1))
    continue
  fi

  iterdir="$(mktemp -d -p "$WORKDIR")"
  filename="$(basename "$relpath")"
  filepath="$iterdir/$filename"

  if ! curl -fsSL -u "$ARTIFACTORY_USER:$ARTIFACTORY_TOKEN" -o "$filepath" "$url"; then
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

  md5sum  "$filepath.asc" | awk '{print $1}' > "$filepath.asc.md5"
  sha1sum "$filepath.asc" | awk '{print $1}' > "$filepath.asc.sha1"

  if [ "$APPLY" != "true" ]; then
    echo "[dry-run] would PUT $url.asc (+ .md5, .sha1)"
    processed=$((processed + 1))
    rm -rf "$iterdir"
    continue
  fi

  upload_failed=0
  for ext in asc asc.md5 asc.sha1; do
    if ! curl -fsSL -o /dev/null \
              -u "$ARTIFACTORY_USER:$ARTIFACTORY_TOKEN" \
              -T "$filepath.$ext" "$url.$ext"; then
      echo "error: upload failed: $filename.$ext" >&2
      upload_failed=1
    fi
  done

  if [ "$upload_failed" = "0" ]; then
    processed=$((processed + 1))
  else
    failed=$((failed + 1))
  fi

  rm -rf "$iterdir"
done < "$urls_file"

echo ""
echo "Summary [$mode_label]: processed=$processed skipped=$skipped failed=$failed"
[ "$failed" = "0" ]