#!/usr/bin/env bash
set -Eeuo pipefail

# Resolve the top-level OCI digest for a published reference.  Exit 44 only
# when the registry explicitly reports that the manifest does not exist;
# authentication, transport, rate-limit, and malformed-response failures must
# remain fatal to the release workflow.

if [[ "$#" -ne 1 || -z "$1" ]]; then
  echo "usage: release_registry_digest.sh IMAGE_REFERENCE" >&2
  exit 64
fi

reference="$1"
inspect_output=""
inspect_status=0
inspect_output="$(docker buildx imagetools inspect "$reference" 2>&1)" \
  || inspect_status=$?

if [[ "$inspect_status" -ne 0 ]]; then
  # Buildx has emitted each of these forms for a missing manifest.  Keep the
  # match deliberately narrower than a bare "not found": that phrase also
  # occurs when Docker itself is unavailable and in transport/proxy errors.
  if grep -Eiq \
    '(^|[[:space:]:])(manifest unknown|manifest_unknown|no such manifest)([[:space:].,;:]|$)' \
    <<< "$inspect_output" \
    || grep -Fqi -- "$reference: not found" <<< "$inspect_output" \
    || grep -Eiq '/manifests/[^[:space:]]*.*404[[:space:]]+not found' \
      <<< "$inspect_output"; then
    printf '%s\n' "$inspect_output" >&2
    exit 44
  fi
  printf 'registry inspection failed for %s (status %s):\n%s\n' \
    "$reference" "$inspect_status" "$inspect_output" >&2
  # Exit 44 is reserved by this helper for the explicit missing-manifest
  # classification above, even if an underlying command happens to use it.
  if [[ "$inspect_status" -eq 44 ]]; then
    exit 69
  fi
  exit "$inspect_status"
fi

digest="$(awk '$1 == "Digest:" {print $2; exit}' <<< "$inspect_output")"
if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  printf 'registry returned no valid top-level digest for %s:\n%s\n' \
    "$reference" "$inspect_output" >&2
  exit 65
fi

printf '%s\n' "$digest"
