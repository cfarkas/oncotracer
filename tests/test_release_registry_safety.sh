#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
DIGEST_HELPER="$ROOT/scripts/release_registry_digest.sh"
PAIR_HELPER="$ROOT/scripts/release_registry_pair.sh"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_ROOT"' EXIT
mkdir -p "$TEMP_ROOT/bin"

cat > "$TEMP_ROOT/bin/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
set -u
if [[ "$#" -ne 4 || "$1" != buildx || "$2" != imagetools || "$3" != inspect ]]; then
  echo "unexpected fake docker invocation: $*" >&2
  exit 99
fi
case "$4" in
  registry.example/oncotracer:2.0.0) prefix=FIRST ;;
  registry.example/oncotracer:v2.0.0) prefix=SECOND ;;
  *) prefix=SINGLE ;;
esac
status_name="${prefix}_STATUS"
output_name="${prefix}_OUTPUT"
printf '%s\n' "${!output_name-}"
exit "${!status_name-0}"
FAKE_DOCKER
chmod 0755 "$TEMP_ROOT/bin/docker"

DIGEST_A="sha256:$(printf 'a%.0s' {1..64})"
DIGEST_B="sha256:$(printf 'b%.0s' {1..64})"
SINGLE_REF="registry.example/oncotracer:test"
FIRST_REF="registry.example/oncotracer:2.0.0"
SECOND_REF="registry.example/oncotracer:v2.0.0"

run_digest() {
  local expected_status="$1" status=0
  shift
  output="$(env PATH="$TEMP_ROOT/bin:$PATH" "$@" \
    "$DIGEST_HELPER" "$SINGLE_REF" 2> "$TEMP_ROOT/stderr")" || status=$?
  test "$status" -eq "$expected_status" || {
    echo "digest helper returned $status; expected $expected_status" >&2
    cat "$TEMP_ROOT/stderr" >&2
    exit 1
  }
}

run_pair() {
  local expected_status="$1" status=0
  shift
  output="$(env PATH="$TEMP_ROOT/bin:$PATH" "$@" \
    "$PAIR_HELPER" "$FIRST_REF" "$SECOND_REF" \
    2> "$TEMP_ROOT/stderr")" || status=$?
  test "$status" -eq "$expected_status" || {
    echo "pair helper returned $status; expected $expected_status" >&2
    cat "$TEMP_ROOT/stderr" >&2
    exit 1
  }
}

run_digest 0 SINGLE_OUTPUT=$'Name: test\nDigest: '"$DIGEST_A" SINGLE_STATUS=0
test "$output" = "$DIGEST_A"

run_digest 44 SINGLE_OUTPUT='manifest unknown' SINGLE_STATUS=1
run_digest 44 SINGLE_OUTPUT="$SINGLE_REF: not found" SINGLE_STATUS=1
run_digest 44 \
  SINGLE_OUTPUT='unexpected status from HEAD request to https://registry.example/v2/oncotracer/manifests/test: 404 Not Found' \
  SINGLE_STATUS=1

run_digest 1 SINGLE_OUTPUT='unauthorized: authentication required' SINGLE_STATUS=1
grep -Fq 'registry inspection failed' "$TEMP_ROOT/stderr"
run_digest 7 \
  SINGLE_OUTPUT='failed to do request: dial tcp: lookup registry.example: no such host' \
  SINGLE_STATUS=7
run_digest 127 SINGLE_OUTPUT='docker: command not found' SINGLE_STATUS=127
run_digest 69 SINGLE_OUTPUT='unauthorized: authentication required' SINGLE_STATUS=44
run_digest 65 SINGLE_OUTPUT='Name: test' SINGLE_STATUS=0
grep -Fq 'no valid top-level digest' "$TEMP_ROOT/stderr"

run_pair 0 \
  FIRST_OUTPUT="$FIRST_REF: not found" FIRST_STATUS=1 \
  SECOND_OUTPUT='manifest unknown' SECOND_STATUS=1
test "$output" = missing

run_pair 0 \
  FIRST_OUTPUT=$'Name: first\nDigest: '"$DIGEST_A" FIRST_STATUS=0 \
  SECOND_OUTPUT=$'Name: second\nDigest: '"$DIGEST_A" SECOND_STATUS=0
test "$output" = $'existing\t'"$DIGEST_A"

run_pair 0 \
  FIRST_OUTPUT=$'Name: first\nDigest: '"$DIGEST_A" FIRST_STATUS=0 \
  SECOND_OUTPUT="$SECOND_REF: not found" SECOND_STATUS=1
test "$output" = $'partial\t'"$FIRST_REF"$'\t'"$SECOND_REF"$'\t'"$DIGEST_A"

run_pair 0 \
  FIRST_OUTPUT="$FIRST_REF: not found" FIRST_STATUS=1 \
  SECOND_OUTPUT=$'Name: second\nDigest: '"$DIGEST_A" SECOND_STATUS=0
test "$output" = $'partial\t'"$SECOND_REF"$'\t'"$FIRST_REF"$'\t'"$DIGEST_A"

run_pair 67 \
  FIRST_OUTPUT=$'Name: first\nDigest: '"$DIGEST_A" FIRST_STATUS=0 \
  SECOND_OUTPUT=$'Name: second\nDigest: '"$DIGEST_B" SECOND_STATUS=0
grep -Fq 'stable registry tags disagree' "$TEMP_ROOT/stderr"

run_pair 1 \
  FIRST_OUTPUT='unauthorized: authentication required' FIRST_STATUS=1 \
  SECOND_OUTPUT=$'Name: second\nDigest: '"$DIGEST_A" SECOND_STATUS=0
run_pair 65 \
  FIRST_OUTPUT='malformed success response' FIRST_STATUS=0 \
  SECOND_OUTPUT=$'Name: second\nDigest: '"$DIGEST_A" SECOND_STATUS=0

printf 'RELEASE_REGISTRY_SAFETY_OK\n'
