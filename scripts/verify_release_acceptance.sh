#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ "$#" -ne 6 ]]; then
  echo "usage: $0 RELEASE_DIR ACCEPTANCE_ROOT MAIN_SHA SOURCE_SHA256 BINARY_SHA256 IMAGE_DIGEST" >&2
  exit 2
fi

RELEASE_DIR="$1"
ACCEPTANCE_ROOT="$2"
MAIN_SHA="$3"
SOURCE_SHA256="$4"
BINARY_SHA256="$5"
IMAGE_DIGEST="$6"

[[ "$RELEASE_DIR" == /* && "$ACCEPTANCE_ROOT" == /* ]]
[[ "$MAIN_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$SOURCE_SHA256" =~ ^[0-9a-f]{64}$ ]]
[[ "$BINARY_SHA256" =~ ^[0-9a-f]{64}$ ]]
[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
test -d "$RELEASE_DIR"
test ! -L "$RELEASE_DIR"
RELEASE_DIR="$(cd -P -- "$RELEASE_DIR" && pwd -P)"
[[ "$ACCEPTANCE_ROOT" != / && "$ACCEPTANCE_ROOT" != */ ]]
ACCEPTANCE_PARENT="${ACCEPTANCE_ROOT%/*}"
ACCEPTANCE_NAME="${ACCEPTANCE_ROOT##*/}"
[[ -n "$ACCEPTANCE_NAME" && "$ACCEPTANCE_NAME" != . && "$ACCEPTANCE_NAME" != .. ]]
test -d "$ACCEPTANCE_PARENT"
test ! -L "$ACCEPTANCE_PARENT"
ACCEPTANCE_PARENT="$(cd -P -- "$ACCEPTANCE_PARENT" && pwd -P)"
ACCEPTANCE_ROOT="$ACCEPTANCE_PARENT/$ACCEPTANCE_NAME"
[[ "$ACCEPTANCE_ROOT" != "$RELEASE_DIR" ]]
[[ "$ACCEPTANCE_ROOT" != "$RELEASE_DIR/"* ]]
[[ ! -e "$ACCEPTANCE_ROOT" && ! -L "$ACCEPTANCE_ROOT" ]]

expected_assets=(
  SHA256SUMS
  oncotracer
  oncotracer-v2.0.0-parity-audit.tar.gz
  release-provenance.json
)
shopt -s dotglob nullglob
release_entries=("$RELEASE_DIR"/*)
test "${#release_entries[@]}" -eq "${#expected_assets[@]}"
for entry in "${release_entries[@]}"; do
  test -f "$entry"
  test ! -L "$entry"
  test "$(stat -c '%h' -- "$entry")" -eq 1
  case "${entry##*/}" in
    SHA256SUMS|oncotracer|oncotracer-v2.0.0-parity-audit.tar.gz|release-provenance.json)
      ;;
    *)
      echo "unexpected release entry: ${entry##*/}" >&2
      exit 1
      ;;
  esac
done
for asset in "${expected_assets[@]}"; do
  test -f "$RELEASE_DIR/$asset"
  test ! -L "$RELEASE_DIR/$asset"
done

ACCEPTANCE_RELEASE="$ACCEPTANCE_ROOT/release"
ACCEPTANCE_HOME="$ACCEPTANCE_ROOT/home"
ACCEPTANCE_TMP="$ACCEPTANCE_ROOT/tmp"
install -d -m 0700 "$ACCEPTANCE_ROOT"
install -d -m 0700 "$ACCEPTANCE_RELEASE" "$ACCEPTANCE_HOME" "$ACCEPTANCE_TMP"

physical_directory_identity() {
  local directory="$1"
  test -d "$directory"
  test ! -L "$directory"
  stat -c '%d:%i:%u:%g:%F' -- "$directory"
}
path_is_absent() {
  test ! -e "$1"
  test ! -L "$1"
}

ACCEPTANCE_ROOT_IDENTITY="$(physical_directory_identity "$ACCEPTANCE_ROOT")"
ACCEPTANCE_RELEASE_IDENTITY="$(physical_directory_identity "$ACCEPTANCE_RELEASE")"
ACCEPTANCE_HOME_IDENTITY="$(physical_directory_identity "$ACCEPTANCE_HOME")"
ACCEPTANCE_TMP_IDENTITY="$(physical_directory_identity "$ACCEPTANCE_TMP")"
for directory in \
  "$ACCEPTANCE_ROOT" "$ACCEPTANCE_RELEASE" "$ACCEPTANCE_HOME" "$ACCEPTANCE_TMP"; do
  test "$(stat -c '%a' -- "$directory")" = 700
done
for asset in "${expected_assets[@]}"; do
  install -m 0600 "$RELEASE_DIR/$asset" "$ACCEPTANCE_RELEASE/$asset"
  cmp "$RELEASE_DIR/$asset" "$ACCEPTANCE_RELEASE/$asset"
done
chmod 0755 "$ACCEPTANCE_RELEASE/oncotracer"

cd "$ACCEPTANCE_RELEASE"
test "$(wc -l < SHA256SUMS)" -eq 3
for asset in oncotracer oncotracer-v2.0.0-parity-audit.tar.gz release-provenance.json; do
  awk -v expected="$asset" '
    BEGIN { count = 0 }
    NF == 2 && $1 ~ /^[0-9a-f]{64}$/ && $2 == expected { count++ }
    END { exit count == 1 ? 0 : 1 }
  ' SHA256SUMS
done
sha256sum --strict -c SHA256SUMS
test "$(sha256sum oncotracer | awk '{print $1}')" = "$BINARY_SHA256"
PARITY_SHA256="$(sha256sum oncotracer-v2.0.0-parity-audit.tar.gz | awk '{print $1}')"
[[ "$PARITY_SHA256" =~ ^[0-9a-f]{64}$ ]]

BEGINNER_CONFIG="$ACCEPTANCE_ROOT/config"
BEGINNER_DATA="$ACCEPTANCE_ROOT/data"
BEGINNER_CACHE="$ACCEPTANCE_ROOT/cache"
BEGINNER_ENVS="$ACCEPTANCE_ROOT/envs"
beginner() {
  env \
    -u ONCOTRACER_ROOT \
    -u ONCOTRACER_HOME \
    -u ONCOTRACER_PAYLOAD_CACHE \
    -u ONCOTRACER_CORE_PREFIX \
    -u ONCOTRACER_QDNASEQ_PREFIX \
    -u ONCOTRACER_ICHORCNA_PREFIX \
    -u ONCOTRACER_CLASSIFIER_PREFIX \
    -u ONCOTRACER_GISTIC_PREFIX \
    HOME="$ACCEPTANCE_HOME" \
    XDG_CONFIG_HOME="$BEGINNER_CONFIG" \
    XDG_DATA_HOME="$BEGINNER_DATA" \
    XDG_CACHE_HOME="$BEGINNER_CACHE" \
    TMPDIR="$ACCEPTANCE_TMP" \
    PYTHONNOUSERSITE=1 \
    PATH=/usr/local/bin:/usr/bin:/bin \
    "$@"
}

beginner "$ACCEPTANCE_RELEASE/oncotracer" --version \
  | grep -Fx 'OncoTracer 2.0.0'
beginner "$ACCEPTANCE_RELEASE/oncotracer" --help \
  | grep -F 'Native LP-WGS CNA analysis.'
beginner "$ACCEPTANCE_RELEASE/oncotracer" provenance --json \
  > "$ACCEPTANCE_ROOT/executable-provenance.json"
jq -e \
  --arg commit "$MAIN_SHA" \
  --arg source "$SOURCE_SHA256" \
  --arg binary "$BINARY_SHA256" '
    .source_commit == $commit and .source_sha256 == $source and
    .source_tree_dirty == false and .binary_sha256 == $binary
  ' "$ACCEPTANCE_ROOT/executable-provenance.json"
jq -e \
  --arg commit "$MAIN_SHA" \
  --arg source "$SOURCE_SHA256" \
  --arg binary "$BINARY_SHA256" \
  --arg digest "$IMAGE_DIGEST" \
  --arg parity "$PARITY_SHA256" '
    def positive_integer:
      type == "number" and . > 0 and . == floor;
    def sha256_digest:
      type == "string" and test("^sha256:[0-9a-f]{64}$");
    .schema == "oncotracer-v2-release-provenance-v3" and
    .version == "2.0.0" and .release_tag == "v2.0.0" and
    .source_commit == $commit and .source_sha256 == $source and
    .source_tree_dirty == false and .binary_sha256 == $binary and
    .parity_audit_bundle_sha256 == $parity and
    .native_nextflow_required == false and
    .container.reference == ("ghcr.io/cfarkas/oncotracer@" + $digest) and
    .container.digest == $digest and .container.nextflow_present == false and
    .frozen_comparator.oncotracer_commit ==
      "032c1268fa7fdcadc48087055066d7a9fc59bd89" and
    .frozen_comparator.oncotracer_image ==
      "carlosfarkas/oncotracer@sha256:4856aed020e1102f891b91de54d6acf365d6b8a57e2283a4f7b670b0bd5b07ed" and
    .frozen_comparator.samurai_commit ==
      "6a901940288b008237703c6b181d447e7dee4fcf" and
    .frozen_comparator.nextflow_version == "26.04.6" and
    .frozen_comparator.nextflow_sha256 ==
      "182a63c74074e2dc7956ffa3c8cd59de952ed2c44394e21faf5e1736b945444c" and
    .frozen_comparator.qdnaseq_source_commit ==
      "cf7c07e39de0ac64a9c38cb030cba4626e2aae83" and
    .workflows.native_v2_ci.sha == $commit and
    .workflows.quickstart1.sha == $commit and
    .workflows.quickstart2.sha == $commit and
    (.workflows.native_v2_ci.run_id | positive_integer) and
    (.workflows.native_v2_ci.run_attempt | positive_integer) and
    (.workflows.quickstart1.run_id | positive_integer) and
    (.workflows.quickstart1.run_attempt | positive_integer) and
    (.workflows.quickstart2.run_id | positive_integer) and
    (.workflows.quickstart2.run_attempt | positive_integer) and
    (.workflows.quickstart1.artifact.id | positive_integer) and
    (.workflows.quickstart2.artifact.id | positive_integer) and
    (.workflows.quickstart1.artifact.digest | sha256_digest) and
    (.workflows.quickstart2.artifact.digest | sha256_digest) and
    .workflows.native_v2_ci.url ==
      ("https://github.com/cfarkas/oncotracer/actions/runs/" +
       (.workflows.native_v2_ci.run_id | tostring)) and
    .workflows.quickstart1.url ==
      ("https://github.com/cfarkas/oncotracer/actions/runs/" +
       (.workflows.quickstart1.run_id | tostring)) and
    .workflows.quickstart2.url ==
      ("https://github.com/cfarkas/oncotracer/actions/runs/" +
       (.workflows.quickstart2.run_id | tostring)) and
    (.workflows.quickstart1.artifact.name ==
      ("native-v2-quickstart1-parity-" +
       (.workflows.quickstart1.run_id | tostring) + "-" +
       (.workflows.quickstart1.run_attempt | tostring))) and
    (.workflows.quickstart2.artifact.name ==
      ("native-v2-quickstart2-parity-" +
       (.workflows.quickstart2.run_id | tostring) + "-" +
       (.workflows.quickstart2.run_attempt | tostring)))
  ' "$ACCEPTANCE_RELEASE/release-provenance.json"

beginner "$ACCEPTANCE_RELEASE/oncotracer" install --conda \
  --prefix "$BEGINNER_ENVS" --dry-run \
  > "$ACCEPTANCE_ROOT/install-dry-run.txt"
for quickstart in 1 2; do
  beginner "$ACCEPTANCE_RELEASE/oncotracer" quickstart "$quickstart" \
    --backend conda \
    --test-root "$ACCEPTANCE_ROOT/quickstart-$quickstart" \
    --dry-run \
    > "$ACCEPTANCE_ROOT/quickstart-$quickstart-dry-run.json"
  grep -F 'dry-run completed without writing files' \
    "$ACCEPTANCE_ROOT/quickstart-$quickstart-dry-run.json"
  path_is_absent "$ACCEPTANCE_ROOT/quickstart-$quickstart"
done
path_is_absent "$BEGINNER_CONFIG"
path_is_absent "$BEGINNER_DATA"
path_is_absent "$BEGINNER_CACHE"
path_is_absent "$BEGINNER_ENVS"
test "$(physical_directory_identity "$ACCEPTANCE_ROOT")" = \
  "$ACCEPTANCE_ROOT_IDENTITY"
test "$(physical_directory_identity "$ACCEPTANCE_RELEASE")" = \
  "$ACCEPTANCE_RELEASE_IDENTITY"
test "$(physical_directory_identity "$ACCEPTANCE_HOME")" = \
  "$ACCEPTANCE_HOME_IDENTITY"
test "$(physical_directory_identity "$ACCEPTANCE_TMP")" = \
  "$ACCEPTANCE_TMP_IDENTITY"
test "$(stat -c '%a' -- "$ACCEPTANCE_HOME")" = 700
test "$(stat -c '%a' -- "$ACCEPTANCE_TMP")" = 700
test "$(stat -c '%a' -- "$ACCEPTANCE_ROOT")" = 700
test "$(stat -c '%a' -- "$ACCEPTANCE_RELEASE")" = 700
test -z "$(find "$ACCEPTANCE_HOME" -mindepth 1 -print -quit)"
test -z "$(find "$ACCEPTANCE_TMP" -mindepth 1 -print -quit)"

# Dry-run acceptance must not mutate or add anything beside the copied
# executable. Re-run the complete physical inventory and checksum contract
# after every command, rather than assuming that a zero exit status was pure.
release_entries_after=("$ACCEPTANCE_RELEASE"/*)
test "${#release_entries_after[@]}" -eq "${#expected_assets[@]}"
for entry in "${release_entries_after[@]}"; do
  test -f "$entry"
  test ! -L "$entry"
  test "$(stat -c '%h' -- "$entry")" -eq 1
  case "${entry##*/}" in
    SHA256SUMS|oncotracer|oncotracer-v2.0.0-parity-audit.tar.gz|release-provenance.json)
      ;;
    *)
      echo "acceptance command added an unexpected release entry: ${entry##*/}" >&2
      exit 1
      ;;
  esac
done
sha256sum --strict -c "$ACCEPTANCE_RELEASE/SHA256SUMS"
test "$(sha256sum "$ACCEPTANCE_RELEASE/oncotracer" | awk '{print $1}')" = \
  "$BINARY_SHA256"
for asset in "${expected_assets[@]}"; do
  cmp "$RELEASE_DIR/$asset" "$ACCEPTANCE_RELEASE/$asset"
done

expected_acceptance_entries=(
  executable-provenance.json
  home
  install-dry-run.txt
  quickstart-1-dry-run.json
  quickstart-2-dry-run.json
  release
  tmp
)
acceptance_entries=("$ACCEPTANCE_ROOT"/*)
test "${#acceptance_entries[@]}" -eq "${#expected_acceptance_entries[@]}"
for index in "${!expected_acceptance_entries[@]}"; do
  test "${acceptance_entries[$index]##*/}" = \
    "${expected_acceptance_entries[$index]}"
done
for output in \
  "$ACCEPTANCE_ROOT/executable-provenance.json" \
  "$ACCEPTANCE_ROOT/install-dry-run.txt" \
  "$ACCEPTANCE_ROOT/quickstart-1-dry-run.json" \
  "$ACCEPTANCE_ROOT/quickstart-2-dry-run.json"; do
  test -f "$output"
  test ! -L "$output"
  test "$(stat -c '%h' -- "$output")" -eq 1
done

SHA256SUMS_SHA256="$(sha256sum "$ACCEPTANCE_RELEASE/SHA256SUMS" | awk '{print $1}')"
PROVENANCE_SHA256="$(sha256sum "$ACCEPTANCE_RELEASE/release-provenance.json" | awk '{print $1}')"
for asset in "${expected_assets[@]}"; do
  chmod 0400 "$ACCEPTANCE_RELEASE/$asset"
done
chmod 0500 "$ACCEPTANCE_RELEASE"
test "$(stat -c '%a' -- "$ACCEPTANCE_ROOT")" = 700
test "$(stat -c '%a' -- "$ACCEPTANCE_RELEASE")" = 500

jq -n \
  --arg commit "$MAIN_SHA" \
  --arg source "$SOURCE_SHA256" \
  --arg binary "$BINARY_SHA256" \
  --arg digest "$IMAGE_DIGEST" \
  --arg sums "$SHA256SUMS_SHA256" \
  --arg parity "$PARITY_SHA256" \
  --arg provenance "$PROVENANCE_SHA256" '
  {
    schema: "oncotracer-v2-release-acceptance-v1",
    source_commit: $commit,
    source_sha256: $source,
    binary_sha256: $binary,
    image_digest: $digest,
    release_assets: {
      "SHA256SUMS": $sums,
      "oncotracer": $binary,
      "oncotracer-v2.0.0-parity-audit.tar.gz": $parity,
      "release-provenance.json": $provenance
    },
    checks: [
      "exact-four-assets",
      "strict-checksums",
      "isolated-version-help",
      "executable-provenance",
      "release-provenance-and-attempt-binding",
      "frozen-comparator-pins",
      "install-conda-dry-run-no-writes",
      "quickstart-1-conda-dry-run-no-writes",
      "quickstart-2-conda-dry-run-no-writes"
    ]
  }
' > "$ACCEPTANCE_ROOT/acceptance-evidence.json"
