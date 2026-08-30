#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Create one permanent GHCR tag without overwriting an existing tag.
#
# GHCR does not honor If-None-Match on manifest PUT: it answers 201 and writes
# regardless of the condition, so registry-enforced atomic creation is not
# available here. The header is still sent, so a registry that does honor it
# gives the stronger guarantee for free. On GHCR the safety comes instead from
# read-after-write: every target is confirmed absent immediately before its
# PUT, and confirmed to resolve to the exact expected digest immediately
# after. A tag that appears in the short window between those two checks is
# detected by the post-write digest comparison unless it already carries the
# identical digest, which is the only case where an overwrite is harmless.
# Every other outcome fails closed and publication stops.

if [[ "$#" -lt 4 || "$#" -gt 5 ]]; then
  echo "usage: $0 SOURCE_TAG EXPECTED_DIGEST EXPECTED_MAIN_SHA ABSENT_TARGET_TAG [ABSENT_TARGET_TAG]" >&2
  exit 64
fi

SOURCE_REFERENCE="$1"
EXPECTED_DIGEST="$2"
EXPECTED_MAIN_SHA="$3"
shift 3
TARGET_REFERENCES=("$@")
REGISTRY=ghcr.io
REPOSITORY=cfarkas/oncotracer
REFERENCE_PREFIX="$REGISTRY/$REPOSITORY:"

[[ "$SOURCE_REFERENCE" =~ ^ghcr[.]io/cfarkas/oncotracer:v2[.]0[.]0-candidate-[1-9][0-9]*-[1-9][0-9]*$ ]]
[[ "$EXPECTED_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$EXPECTED_MAIN_SHA" =~ ^[0-9a-f]{40}$ ]]
for target_reference in "${TARGET_REFERENCES[@]}"; do
  [[ "$target_reference" =~ ^ghcr[.]io/cfarkas/oncotracer:(2[.]0[.]0|v2[.]0[.]0)$ ]]
  [[ "$SOURCE_REFERENCE" != "$target_reference" ]]
done
if [[ "${#TARGET_REFERENCES[@]}" -eq 2 ]]; then
  [[ "${TARGET_REFERENCES[0]}" != "${TARGET_REFERENCES[1]}" ]]
fi
: "${GHCR_USERNAME:?GHCR_USERNAME is required}"
: "${GHCR_TOKEN:?GHCR_TOKEN is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
[[ "$RUNNER_TEMP" == /* ]]
test -d "$RUNNER_TEMP"
test ! -L "$RUNNER_TEMP"
[[ "$GHCR_USERNAME" != *:* && "$GHCR_USERNAME" != *$'\n'* ]]
[[ "$GHCR_TOKEN" != *$'\n'* ]]

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
resolver="$script_dir/release_registry_digest.sh"
TEMP_ROOT="$(mktemp -d -- "$RUNNER_TEMP/oncotracer-registry-conditional.XXXXXX")"
test -d "$TEMP_ROOT"
test ! -L "$TEMP_ROOT"
test "$(stat -c '%a' -- "$TEMP_ROOT")" = 700

resolve_exact() {
  local reference="$1" expected="$2" actual
  actual="$("$resolver" "$reference")"
  test "$actual" = "$expected" || {
    echo "$reference resolved to $actual; expected $expected" >&2
    exit 1
  }
}

header_value() {
  local headers="$1" name="$2"
  awk -v expected="$name" '
    BEGIN { IGNORECASE = 1; value = "" }
    {
      line = $0
      sub(/\r$/, "", line)
      prefix = expected ":"
      if (tolower(substr(line, 1, length(prefix))) == tolower(prefix)) {
        value = substr(line, length(prefix) + 1)
        sub(/^[[:space:]]+/, "", value)
        sub(/[[:space:]]+$/, "", value)
      }
    }
    END { if (value == "") exit 1; print value }
  ' "$headers"
}

basic_authorization="$(printf '%s:%s' "$GHCR_USERNAME" "$GHCR_TOKEN" | base64 -w0)"
[[ "$basic_authorization" =~ ^[A-Za-z0-9+/]+={0,2}$ ]]
token_response="$(
  printf 'header = "Authorization: Basic %s"\n' "$basic_authorization" \
    | curl --config - --fail --silent --show-error --location --get \
        --data-urlencode service=ghcr.io \
        --data-urlencode "scope=repository:$REPOSITORY:pull,push" \
        https://ghcr.io/token
)"
REGISTRY_BEARER="$(jq -er '
  (.token // .access_token) |
  select(type == "string" and length > 0)
' <<< "$token_response")"
[[ "$REGISTRY_BEARER" =~ ^[A-Za-z0-9._~+/=-]+$ ]]
unset GHCR_TOKEN basic_authorization token_response

registry_curl() {
  printf 'header = "Authorization: Bearer %s"\n' "$REGISTRY_BEARER" \
    | curl --config - "$@"
}

SOURCE_TAG="${SOURCE_REFERENCE#"$REFERENCE_PREFIX"}"
resolve_exact "$SOURCE_REFERENCE" "$EXPECTED_DIGEST"

ACCEPT_MANIFESTS='application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json'
registry_curl --fail --silent --show-error \
  --header "Accept: $ACCEPT_MANIFESTS" \
  --dump-header "$TEMP_ROOT/source.headers" \
  --output "$TEMP_ROOT/source.manifest" \
  "https://$REGISTRY/v2/$REPOSITORY/manifests/$EXPECTED_DIGEST"
test -s "$TEMP_ROOT/source.manifest"
test "sha256:$(sha256sum "$TEMP_ROOT/source.manifest" | awk '{print $1}')" = \
  "$EXPECTED_DIGEST"
SOURCE_CONTENT_TYPE="$(header_value "$TEMP_ROOT/source.headers" Content-Type)"
case "$SOURCE_CONTENT_TYPE" in
  application/vnd.oci.image.index.v1+json|\
  application/vnd.oci.image.manifest.v1+json|\
  application/vnd.docker.distribution.manifest.list.v2+json|\
  application/vnd.docker.distribution.manifest.v2+json)
    ;;
  *)
    echo "unsupported source manifest content type: $SOURCE_CONTENT_TYPE" >&2
    exit 1
    ;;
esac
test "$(header_value "$TEMP_ROOT/source.headers" Docker-Content-Digest)" = \
  "$EXPECTED_DIGEST"

resolve_exact "$SOURCE_REFERENCE" "$EXPECTED_DIGEST"

for target_reference in "${TARGET_REFERENCES[@]}"; do
  target_status=0
  target_before="$("$resolver" "$target_reference")" || target_status=$?
  test "$target_status" -eq 44 || {
    echo "$target_reference was not absent during conditional preflight" >&2
    exit 1
  }
  test -z "$target_before"
done
test "$(gh api /repos/cfarkas/oncotracer/commits/main --jq .sha)" = \
  "$EXPECTED_MAIN_SHA"

for index in "${!TARGET_REFERENCES[@]}"; do
  target_reference="${TARGET_REFERENCES[$index]}"
  target_tag="${target_reference#"$REFERENCE_PREFIX"}"
  immediate_status=0
  "$resolver" "$target_reference" >/dev/null || immediate_status=$?
  test "$immediate_status" -eq 44 || {
    echo "$target_reference appeared before its conditional write" >&2
    exit 1
  }
  CREATE_STATUS="$(registry_curl --silent --show-error \
    --request PUT \
    --header "Content-Type: $SOURCE_CONTENT_TYPE" \
    --header 'If-None-Match: *' \
    --data-binary "@$TEMP_ROOT/source.manifest" \
    --dump-header "$TEMP_ROOT/create-$index.headers" \
    --output "$TEMP_ROOT/create-$index.response" \
    --write-out '%{http_code}' \
    "https://$REGISTRY/v2/$REPOSITORY/manifests/$target_tag")"
  if [[ "$CREATE_STATUS" == 201 ]]; then
    test "$(header_value "$TEMP_ROOT/create-$index.headers" Docker-Content-Digest)" = \
      "$EXPECTED_DIGEST"
  elif [[ "$CREATE_STATUS" == 412 ]]; then
    # A concurrent creator won the no-overwrite race. Accept only the exact
    # already-authenticated digest; a different digest remains untouched.
    resolve_exact "$target_reference" "$EXPECTED_DIGEST"
  else
    echo "conditional creation of $target_reference failed closed (HTTP $CREATE_STATUS)" >&2
    exit 1
  fi
  resolve_exact "$target_reference" "$EXPECTED_DIGEST"
  printf 'conditionally created or adopted %s at %s\n' \
    "$target_reference" "$EXPECTED_DIGEST"
done
resolve_exact "$SOURCE_REFERENCE" "$EXPECTED_DIGEST"
