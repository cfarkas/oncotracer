#!/usr/bin/env bash
# Complete GitHub Actions parity driver for OncoTracer native v2.
set -Eeuo pipefail
umask 022

usage() {
  echo "Usage: $0 quickstart1|quickstart2" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
SUITE="$1"
[[ "$SUITE" == quickstart1 || "$SUITE" == quickstart2 ]] || usage

: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${CANDIDATE_SHA:?CANDIDATE_SHA is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"

readonly V1_1_COMMIT="032c1268fa7fdcadc48087055066d7a9fc59bd89"
readonly V1_DOCKER_IMAGE="carlosfarkas/oncotracer@sha256:4856aed020e1102f891b91de54d6acf365d6b8a57e2283a4f7b670b0bd5b07ed"
readonly SAMURAI_COMMIT="6a901940288b008237703c6b181d447e7dee4fcf"
readonly NEXTFLOW_VERSION="26.04.6"
readonly NEXTFLOW_DIST_URL="https://github.com/nextflow-io/nextflow/releases/download/v26.04.6/nextflow-26.04.6-dist"
readonly NEXTFLOW_DIST_SHA256="182a63c74074e2dc7956ffa3c8cd59de952ed2c44394e21faf5e1736b945444c"
readonly REPO="$GITHUB_WORKSPACE/v2"
readonly V1_REPO="$GITHUB_WORKSPACE/v1"
readonly TEST_ROOT="$GITHUB_WORKSPACE/oncotracer-parity-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${SUITE}"
readonly INPUT_ROOT="$TEST_ROOT/input"
readonly V1_PROJECT_ROOT="$TEST_ROOT/frozen-v1-project"
readonly V2_PROJECT_ROOT="$TEST_ROOT/native-v2-project"
readonly REPORT_ROOT="$TEST_ROOT/reports"
readonly PARITY_SESSION_ID="$(date -u +%Y%m%dT%H%M%SZ).$$"
readonly NEXTFLOW_REPORT_ROOT="$REPORT_ROOT/frozen-v1.1-$PARITY_SESSION_ID"
readonly AUDIT_ROOT="$TEST_ROOT/audit"
readonly CONTEXT="$AUDIT_ROOT/context"
readonly JOB_ID="${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${SUITE}"
readonly V2_ENV_PREFIX="$RUNNER_TEMP/oncotracer-envs-$JOB_ID"
readonly CONDA_PACKAGE_CACHE="$RUNNER_TEMP/oncotracer-conda-pkgs-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
readonly PAYLOAD_CACHE="$RUNNER_TEMP/oncotracer-v2-payload-$JOB_ID"
readonly CONFIG_HOME="$RUNNER_TEMP/oncotracer-v2-config-$JOB_ID"
readonly PINNED_NEXTFLOW_DIR="$TEST_ROOT/.release-tools/bin"
readonly NEXTFLOW="$PINNED_NEXTFLOW_DIR/nextflow"
readonly PINS="$RUNNER_TEMP/$SUITE-samurai-container-pins.tsv"
readonly PREFLIGHT="$RUNNER_TEMP/$SUITE-samurai-container-preflight.tsv"
readonly IMAGE_OWNERSHIP="$RUNNER_TEMP/oncotracer-image-ownership-$JOB_ID.tsv"
readonly RUNTIME="$CONTEXT/nested-v1-container-runtime.tsv"
readonly SELECTION="$CONTEXT/nested-v1-trace-selection.tsv"
readonly RESOURCE_PREFLIGHT="$CONTEXT/hosted-resource-preflight.txt"
readonly RESOURCE_PHASE_ROOT="$CONTEXT/hosted-resource-phases"
RESOURCE_PHASE_INDEX=0

# Prior hosted logs record 34 GiB total swap but no peak-swap telemetry, so the
# 32 GiB job allocation cannot yet be reduced without weakening the gate.
# Frozen-v1 and native-v2 references use isolated project roots. The frozen
# reference is manifested and released before native v2 creates its own cache;
# neither runtime can adopt or mutate the other's reference tree. Native Conda
# environments are created only after authenticated frozen traces allow exact
# job-pulled image references to be released. Both sequential phase peaks are
# calculated from measured completed validation material, rounded up:
#
#   QS1 frozen: 32 swap + 16 reference + 14 images + 1 input + 1 output + 8 reserve
#   QS2 frozen: 32 swap + 16 reference +  8 images + 2 input + 6 output + 8 reserve
#   QS1 native: 32 swap + 16 reference + 1 input + 1 frozen output +
#               3 minimal envs + 8 solve/cache-or-output + 8 reserve
#   QS2 native: 32 swap + 16 reference + 2 input + 6 frozen output +
#               1 minimal env + 7 solve/cache-or-output + 8 reserve
#
# A low-memory hosted runner requires 72 GiB free at job start. A runner whose
# physical RAM already satisfies the 47-GiB addressable floor needs no swap and
# therefore requires 40 GiB. Neither model sums mutually exclusive Docker and
# Conda peaks.
readonly SHARED_REFERENCE_GIB=16
readonly FILESYSTEM_RESERVE_GIB=8
readonly MIN_PHYSICAL_GIB=15
readonly MIN_ADDRESSABLE_GIB=47
readonly HOST_MEM_TOTAL_KIB="$(awk '$1 == "MemTotal:" {print $2}' /proc/meminfo)"
[[ "$HOST_MEM_TOTAL_KIB" =~ ^[0-9]+$ ]]
read -r PARITY_SWAP_GIB SWAP_FILE < <(
  bash "$REPO/scripts/ci_select_parity_swap.sh" \
    "$HOST_MEM_TOTAL_KIB" "$MIN_ADDRESSABLE_GIB" "$RUNNER_TEMP" \
    "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT"
)
readonly PARITY_SWAP_GIB SWAP_FILE
if [[ "$SUITE" == quickstart1 ]]; then
  readonly PINNED_IMAGES_GIB=14
  readonly PUBLIC_INPUTS_GIB=1
  readonly FROZEN_OUTPUT_GIB=1
  readonly MINIMAL_ENVIRONMENTS_GIB=3
  readonly NATIVE_TRANSIENT_GIB=8
else
  readonly PINNED_IMAGES_GIB=8
  readonly PUBLIC_INPUTS_GIB=2
  readonly FROZEN_OUTPUT_GIB=6
  readonly MINIMAL_ENVIRONMENTS_GIB=1
  readonly NATIVE_TRANSIENT_GIB=7
fi
readonly FROZEN_PHASE_GIB=$((
  PARITY_SWAP_GIB + SHARED_REFERENCE_GIB + PINNED_IMAGES_GIB +
  PUBLIC_INPUTS_GIB + FROZEN_OUTPUT_GIB + FILESYSTEM_RESERVE_GIB
))
readonly NATIVE_PHASE_GIB=$((
  PARITY_SWAP_GIB + SHARED_REFERENCE_GIB + PUBLIC_INPUTS_GIB +
  FROZEN_OUTPUT_GIB + MINIMAL_ENVIRONMENTS_GIB +
  NATIVE_TRANSIENT_GIB + FILESYSTEM_RESERVE_GIB
))
if (( FROZEN_PHASE_GIB > NATIVE_PHASE_GIB )); then
  readonly MIN_FREE_GIB="$FROZEN_PHASE_GIB"
else
  readonly MIN_FREE_GIB="$NATIVE_PHASE_GIB"
fi
if (( PARITY_SWAP_GIB == 32 )); then
  [[ "$MIN_FREE_GIB" -eq 72 ]]
else
  [[ "$PARITY_SWAP_GIB" -eq 0 && "$MIN_FREE_GIB" -eq 40 ]]
fi
# The scientific task cap is 14 GB (13.04 GiB). Previous hosted evidence
# reported 15.61 GiB physical RAM, so 15 GiB is the highest evidence-backed
# whole-GiB floor that preserves operating-system headroom on that runner.
readonly STANDARD_RUNNER_CONTRACT_FREE_GIB=14

readonly ILLUMINA_PRE_INVENTORY="$RUNNER_TEMP/$SUITE-illumina-nested-traces-pre.tsv"
readonly ONT_PRE_INVENTORY="$RUNNER_TEMP/$SUITE-ont-nested-traces-pre.tsv"
readonly HCC_PRE_INVENTORY="$RUNNER_TEMP/$SUITE-hcc1143-nested-traces-pre.tsv"
export XDG_CONFIG_HOME="$CONFIG_HOME"
export ONCOTRACER_PAYLOAD_CACHE="$PAYLOAD_CACHE"
export CONDA_PKGS_DIRS="$CONDA_PACKAGE_CACHE"
export PIP_NO_CACHE_DIR=1
export CONDA_SOLVER=libmamba
export CONDA_CHANNEL_PRIORITY=strict
export NXF_ANSI_LOG=false
export NXF_DISABLE_CHECK_LATEST=true
export NXF_OPTS="-Xms256m -Xmx2g"
export NXF_GITHUB_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"

log() {
  printf '\n===== %s =====\n' "$*"
}

require_file() {
  [[ -s "$1" ]] || { echo "missing or empty file: $1" >&2; exit 1; }
}

record_phase_resources() {
  local phase="$1" output path available_kib mem_total_kib swap_total_kib
  local active_swap_size_bytes=0 active_swap_used_bytes=0 swap_required=0 swap_row=""
  [[ "$phase" =~ ^[a-z0-9][a-z0-9-]*$ ]] || {
    echo "invalid resource-evidence phase: $phase" >&2
    exit 1
  }
  available_kib="$(LC_ALL=C df -Pk "$GITHUB_WORKSPACE" "$RUNNER_TEMP" /tmp "$DOCKER_ROOT_DIR" |
    awk 'NR > 1 && $4 ~ /^[0-9]+$/ {if (minimum == "" || $4 < minimum) minimum=$4} END {print minimum}')"
  mem_total_kib="$(awk '$1 == "MemTotal:" {print $2}' /proc/meminfo)"
  swap_total_kib="$(awk '$1 == "SwapTotal:" {print $2}' /proc/meminfo)"
  if (( PARITY_SWAP_GIB > 0 )) && [[ "$phase" != preflight-passed ]]; then
    swap_required=1
    swap_row="$(swapon --show=NAME,SIZE,USED --bytes --noheadings --raw |
      awk -v expected="$SWAP_FILE" '$1 == expected {print $2, $3}')"
    [[ -n "$swap_row" ]] || {
      echo "ERROR: active job swap evidence is missing for parity phase $phase" >&2
      exit 1
    }
    read -r active_swap_size_bytes active_swap_used_bytes <<< "$swap_row"
  fi
  [[ "$available_kib" =~ ^[0-9]+$ && "$mem_total_kib" =~ ^[0-9]+$ &&
    "$swap_total_kib" =~ ^[0-9]+$ && "$active_swap_size_bytes" =~ ^[0-9]+$ &&
    "$active_swap_used_bytes" =~ ^[0-9]+$ ]]
  bash "$REPO/scripts/ci_resource_phase_guard.sh" \
    "$phase" "$available_kib" "$mem_total_kib" "$swap_total_kib" \
    "$active_swap_size_bytes" "$swap_required" "$MIN_PHYSICAL_GIB" \
    "$MIN_ADDRESSABLE_GIB" "$PARITY_SWAP_GIB" "$FILESYSTEM_RESERVE_GIB" \
    >/dev/null
  RESOURCE_PHASE_INDEX=$((RESOURCE_PHASE_INDEX + 1))
  output="$RESOURCE_PHASE_ROOT/$phase.txt"
  {
    printf 'schema\toncotracer-hosted-resource-phase-v2\n'
    printf 'run_id\t%s\n' "$GITHUB_RUN_ID"
    printf 'run_attempt\t%s\n' "$GITHUB_RUN_ATTEMPT"
    printf 'suite\t%s\n' "$SUITE"
    printf 'candidate_sha\t%s\n' "$CANDIDATE_SHA"
    printf 'phase\t%s\n' "$phase"
    printf 'phase_index\t%s\n' "$RESOURCE_PHASE_INDEX"
    printf 'minimum_free_gib\t%s\n' "$MIN_FREE_GIB"
    printf 'minimum_physical_gib\t%s\n' "$MIN_PHYSICAL_GIB"
    printf 'minimum_addressable_gib\t%s\n' "$MIN_ADDRESSABLE_GIB"
    printf 'planned_swap_gib\t%s\n' "$PARITY_SWAP_GIB"
    printf 'filesystem_reserve_gib\t%s\n' "$FILESYSTEM_RESERVE_GIB"
    printf 'runner_temp\t%s\n' "$RUNNER_TEMP"
    printf 'expected_swap_file\t%s\n' "$SWAP_FILE"
    printf 'swap_required\t%s\n' "$swap_required"
    printf 'active_swap_size_bytes\t%s\n' "$active_swap_size_bytes"
    printf 'active_swap_used_bytes\t%s\n' "$active_swap_used_bytes"
    printf 'minimum_available_kib\t%s\n' "$available_kib"
    printf 'mem_total_kib\t%s\n' "$mem_total_kib"
    printf 'swap_total_kib\t%s\n' "$swap_total_kib"
    printf 'recorded_at\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '\n[df-kibibytes]\n'
    LC_ALL=C df -Pk "$GITHUB_WORKSPACE" "$RUNNER_TEMP" /tmp "$DOCKER_ROOT_DIR"
    printf '\n[memory-kibibytes]\n'
    free -k
    printf '\n[active-swap]\n'
    if (( PARITY_SWAP_GIB == 0 )); then
      printf 'none (physical memory satisfies the addressable floor)\n'
    else
      swapon --show=NAME,SIZE,USED,PRIO --bytes
    fi
    printf '\n[docker]\n'
    docker system df
    printf '\n[path-bytes]\n'
    for path in "$TEST_ROOT" "$INPUT_ROOT" "$V1_PROJECT_ROOT/references" \
      "$V2_PROJECT_ROOT/references" \
      "$TEST_ROOT/v1" "$TEST_ROOT/v2" "$V2_ENV_PREFIX" \
      "$CONDA_PACKAGE_CACHE"; do
      if [[ -e "$path" || -L "$path" ]]; then
        du -sx -B1 -- "$path"
      else
        printf 'absent\t%s\n' "$path"
      fi
    done
  } > "$output"
}

record_image_ownership() {
  local reference="$1" image_id="$2" created_by_job="$3"
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
  [[ "$created_by_job" == 0 || "$created_by_job" == 1 ]]
  printf '%s\t%s\t%s\n' "$reference" "$image_id" "$created_by_job" \
    >> "$IMAGE_OWNERSHIP"
}

remove_owned_image_references() {
  local expected_manifest reference image_id created_by_job extra observed_id containers action
  local alias_index current_id daemon_containers index
  local preserved_id preflight_containers
  local line_number=0 expected_count=1 observed_count=0
  declare -A allowed=()
  declare -A actions=()
  declare -A checked_image_ids=()
  declare -A preexisting_image_ids=()
  declare -A removed_aliases=()
  declare -A seen=()
  declare -a created_flags=()
  declare -a image_ids=()
  declare -a references=()

  canonical_docker_reference() {
    local candidate="$1"
    case "$candidate" in
      docker.io/*)
        printf '%s\n' "${candidate#docker.io/}"
        ;;
      index.docker.io/*)
        printf '%s\n' "${candidate#index.docker.io/}"
        ;;
      *)
        printf '%s\n' "$candidate"
        ;;
    esac
  }

  verify_job_owned_image_aliases() {
    local target_image_id="$1"
    local actual_alias alias_index alias_output canonical_alias expected_alias
    local expected_reference rebound_id
    local expected_alias_count=0 actual_alias_count=0
    declare -A actual_alias_keys=()
    declare -A expected_alias_keys=()
    declare -a inventory_aliases=()

    for alias_index in "${!references[@]}"; do
      [[ "${image_ids[$alias_index]}" == "$target_image_id" &&
        "${created_flags[$alias_index]}" == 1 &&
        -z "${removed_aliases[${references[$alias_index]}]+present}" ]] || continue
      expected_reference="${references[$alias_index]}"
      expected_alias="$(canonical_docker_reference "$expected_reference")"
      [[ -n "$expected_alias" &&
        -z "${expected_alias_keys[$expected_alias]+present}" ]] || {
        echo "Tracked Docker aliases are empty or canonically duplicated for image: $target_image_id" >&2
        exit 1
      }
      expected_alias_keys["$expected_alias"]="$expected_reference"
      expected_alias_count=$((expected_alias_count + 1))
    done
    [[ "$expected_alias_count" -gt 0 ]] || {
      echo "Job-owned image has no remaining tracked Docker aliases: $target_image_id" >&2
      exit 1
    }

    alias_output="$(
      docker image inspect "$target_image_id" \
        --format '{{range .RepoTags}}{{println .}}{{end}}{{range .RepoDigests}}{{println .}}{{end}}'
    )" || {
      echo "Unable to inventory Docker aliases for job-owned image: $target_image_id" >&2
      exit 1
    }
    mapfile -t inventory_aliases < <(
      printf '%s\n' "$alias_output" | sed '/^$/d' | LC_ALL=C sort -u
    )
    [[ "${#inventory_aliases[@]}" -gt 0 ]] || {
      echo "Job-owned image has no addressable Docker aliases: $target_image_id" >&2
      exit 1
    }
    for actual_alias in "${inventory_aliases[@]}"; do
      canonical_alias="$(canonical_docker_reference "$actual_alias")"
      [[ -n "$canonical_alias" &&
        -z "${actual_alias_keys[$canonical_alias]+present}" ]] || {
        echo "Docker alias inventory is empty or canonically duplicated for image: $target_image_id" >&2
        exit 1
      }
      actual_alias_keys["$canonical_alias"]="$actual_alias"
      actual_alias_count=$((actual_alias_count + 1))
      [[ -n "${expected_alias_keys[$canonical_alias]+present}" ]] || {
        echo "Refusing cleanup of an image with an untracked alias: $actual_alias" >&2
        exit 1
      }
    done
    [[ "$actual_alias_count" -eq "$expected_alias_count" ]] || {
      echo "Docker alias inventory is missing a tracked job-owned alias for image: $target_image_id" >&2
      exit 1
    }
    for expected_alias in "${!expected_alias_keys[@]}"; do
      [[ -n "${actual_alias_keys[$expected_alias]+present}" ]] || {
        echo "Docker alias inventory is missing tracked alias: ${expected_alias_keys[$expected_alias]}" >&2
        exit 1
      }
      expected_reference="${expected_alias_keys[$expected_alias]}"
      rebound_id="$(docker image inspect "$expected_reference" --format '{{.Id}}')" || {
        echo "Tracked Docker alias disappeared after inventory: $expected_reference" >&2
        exit 1
      }
      [[ "$rebound_id" == "$target_image_id" ]] || {
        echo "Tracked Docker alias changed identity after inventory: $expected_reference" >&2
        exit 1
      }
    done
  }

  expected_manifest="$RUNNER_TEMP/oncotracer-image-ownership-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${SUITE}.tsv"
  [[ "$IMAGE_OWNERSHIP" == "$expected_manifest" ]] || {
    echo "Refusing image cleanup from unexpected ownership manifest: $IMAGE_OWNERSHIP" >&2
    exit 1
  }
  [[ -f "$IMAGE_OWNERSHIP" && ! -L "$IMAGE_OWNERSHIP" ]] || {
    echo "Image ownership manifest must be a regular non-symlink: $IMAGE_OWNERSHIP" >&2
    exit 1
  }
  [[ "${GITHUB_ACTIONS:-}" == true && -n "${RUNNER_NAME:-}" ]] || {
    echo "Image cleanup requires a GitHub Actions one-job runner context" >&2
    exit 1
  }
  daemon_containers="$(docker ps --all --quiet)"
  [[ -z "$daemon_containers" ]] || {
    echo "Refusing image cleanup on a Docker daemon with existing containers" >&2
    exit 1
  }
  printf 'reference\timage_id\taction\n' \
    > "$CONTEXT/job-image-reference-actions.tsv"

  allowed["$V1_DOCKER_IMAGE"]=1
  while IFS=$'\t' read -r reference image_id extra; do
    [[ "$reference" == container ]] && continue
    [[ -z "$extra" && "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
    allowed["$reference"]=1
    allowed["${reference%:*}@$image_id"]=1
    expected_count=$((expected_count + 2))
  done < "$PINS"

  while IFS=$'\t' read -r reference image_id created_by_job extra; do
    line_number=$((line_number + 1))
    if (( line_number == 1 )); then
      [[ "$reference" == reference && "$image_id" == image_id &&
        "$created_by_job" == created_by_job && -z "$extra" ]]
      continue
    fi
    [[ -n "$reference" && -z "$extra" ]]
    [[ -n "${allowed[$reference]+present}" ]] || {
      echo "Ownership manifest contains an unapproved image reference: $reference" >&2
      exit 1
    }
    [[ -z "${seen[$reference]+present}" ]] || {
      echo "Ownership manifest repeats image reference: $reference" >&2
      exit 1
    }
    seen["$reference"]=1
    observed_count=$((observed_count + 1))
    [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
    [[ "$created_by_job" == 0 || "$created_by_job" == 1 ]]
    observed_id="$(docker image inspect "$reference" --format '{{.Id}}')"
    [[ "$observed_id" == "$image_id" ]] || {
      echo "Refusing cleanup after image identity changed: $reference" >&2
      exit 1
    }
    if [[ "$created_by_job" == 0 ]]; then
      preexisting_image_ids["$image_id"]=1
    else
      preflight_containers="$(docker ps --all --quiet --filter "ancestor=$reference")"
      [[ -z "$preflight_containers" ]] || {
        echo "Refusing to remove an image reference used by a container: $reference" >&2
        exit 1
      }
    fi
    references+=("$reference")
    image_ids+=("$image_id")
    created_flags+=("$created_by_job")
  done < "$IMAGE_OWNERSHIP"

  [[ "$line_number" -gt 1 && "$observed_count" -eq "$expected_count" ]]
  for reference in "${!allowed[@]}"; do
    [[ -n "${seen[$reference]+present}" ]] || {
      echo "Ownership manifest omitted expected image reference: $reference" >&2
      exit 1
    }
  done

  for index in "${!references[@]}"; do
    image_id="${image_ids[$index]}"
    [[ -z "${checked_image_ids[$image_id]+present}" ]] || continue
    checked_image_ids["$image_id"]=1
    [[ -n "${preexisting_image_ids[$image_id]+present}" ]] && continue
    verify_job_owned_image_aliases "$image_id"
  done

  for index in "${!references[@]}"; do
    reference="${references[$index]}"
    image_id="${image_ids[$index]}"
    created_by_job="${created_flags[$index]}"
    action=PRESERVED_PREEXISTING
    if [[ "$created_by_job" == 1 ]]; then
      if [[ -n "${preexisting_image_ids[$image_id]+present}" ]]; then
        action=PRESERVED_JOB_CREATED_SHARED
      else
        if current_id="$(docker image inspect "$reference" --format '{{.Id}}' 2>/dev/null)"; then
          [[ "$current_id" == "$image_id" ]] || {
            echo "Refusing cleanup after image identity changed: $reference" >&2
            exit 1
          }
          containers="$(docker ps --all --quiet --filter "ancestor=$reference")"
          [[ -z "$containers" ]] || {
            echo "Refusing to remove an image reference used by a container: $reference" >&2
            exit 1
          }
          daemon_containers="$(docker ps --all --quiet)"
          [[ -z "$daemon_containers" ]] || {
            echo "Refusing image cleanup after the isolated Docker daemon gained a container" >&2
            exit 1
          }
          verify_job_owned_image_aliases "$image_id"
          docker image rm -- "$reference"
          if docker image inspect "$reference" >/dev/null 2>&1; then
            echo "Job-created image reference remains after exact removal: $reference" >&2
            exit 1
          fi
          for alias_index in "${!references[@]}"; do
            [[ "${image_ids[$alias_index]}" == "$image_id" &&
              "${created_flags[$alias_index]}" == 1 ]] || continue
            if current_id="$(docker image inspect "${references[$alias_index]}" --format '{{.Id}}' 2>/dev/null)"; then
              [[ "$current_id" == "$image_id" ]] || {
                echo "Refusing cleanup after image alias identity changed: ${references[$alias_index]}" >&2
                exit 1
              }
            else
              removed_aliases["${references[$alias_index]}"]=1
            fi
          done
        elif [[ -z "${removed_aliases[$reference]+present}" ]]; then
          echo "Job-created image reference disappeared before an owned alias was removed: $reference" >&2
          exit 1
        fi
        action=REMOVED_JOB_CREATED
      fi
    fi
    actions["$reference"]="$action"
    printf '%s\t%s\t%s\n' "$reference" "$image_id" "$action" \
      >> "$CONTEXT/job-image-reference-actions.tsv"
  done

  for index in "${!references[@]}"; do
    reference="${references[$index]}"
    image_id="${image_ids[$index]}"
    action="${actions[$reference]}"
    if [[ "$action" == REMOVED_JOB_CREATED ]]; then
      if docker image inspect "$reference" >/dev/null 2>&1; then
        echo "Job-created image reference reappeared after cleanup: $reference" >&2
        exit 1
      fi
      continue
    fi
    preserved_id="$(docker image inspect "$reference" --format '{{.Id}}')"
    [[ "$preserved_id" == "$image_id" ]] || {
      echo "Preserved image reference changed during cleanup: $reference" >&2
      exit 1
    }
  done
}

run_native_environment_probe() {
  local environment="$1" probe="$2" expected_status="$3" patterns="$4"
  local output status digest required_pattern pattern_status=0
  shift 4
  output="$CONTEXT/native-environment-probes/$environment-$probe.txt"
  set +e
  "$@" > "$output" 2>&1
  status=$?
  set -e
  while IFS= read -r required_pattern; do
    if [[ -z "$required_pattern" ]] || ! grep -Eq -- "$required_pattern" "$output"; then
      pattern_status=1
      break
    fi
  done <<< "$patterns"
  if [[ "$status" -ne "$expected_status" || "$pattern_status" -ne 0 ]]; then
    echo "Native environment probe failed: $environment/$probe (status=$status)" >&2
    cat "$output" >&2
    exit 1
  fi
  digest="$(sha256sum "$output" | awk '{print $1}')"
  printf '%s\t%s\tPASS\t%s\n' "$environment" "$probe" "$digest" \
    >> "$CONTEXT/native-environment-probes.tsv"
}

create_native_environment() {
  local environment="$1" definition="$2" prefix definition_sha explicit_sha
  prefix="$V2_ENV_PREFIX/$environment"
  [[ "$definition" == "$REPO/environments/native-"*.yml ]]
  [[ -f "$definition" && ! -L "$definition" ]]
  [[ ! -e "$prefix" && ! -L "$prefix" ]] || {
    echo "Refusing to replace pre-existing native environment prefix: $prefix" >&2
    exit 1
  }
  conda env create --yes --prefix "$prefix" --file "$definition"
  conda list --explicit --prefix "$prefix" \
    > "$RUNNER_TEMP/native-$environment.explicit.txt"
  cp "$definition" "$CONTEXT/native-environments/$environment.yml"
  definition_sha="$(sha256sum "$definition" | awk '{print $1}')"
  explicit_sha="$(sha256sum "$RUNNER_TEMP/native-$environment.explicit.txt" | awk '{print $1}')"
  printf '%s\t%s\t%s\n' "$environment" "$definition_sha" "$explicit_sha" \
    >> "$CONTEXT/native-environment-inventory.tsv"
  case "$environment" in
    core) export ONCOTRACER_CORE_PREFIX="$prefix" ;;
    qdnaseq) export ONCOTRACER_QDNASEQ_PREFIX="$prefix" ;;
    ichorcna) export ONCOTRACER_ICHORCNA_PREFIX="$prefix" ;;
    *)
      echo "Unexpected parity environment: $environment" >&2
      exit 1
      ;;
  esac
}

create_minimal_native_environments() {
  [[ ! -e "$V2_ENV_PREFIX" && ! -L "$V2_ENV_PREFIX" ]]
  [[ ! -e "$CONDA_PACKAGE_CACHE" && ! -L "$CONDA_PACKAGE_CACHE" ]]
  mkdir -p "$V2_ENV_PREFIX" "$CONDA_PACKAGE_CACHE"
  printf 'environment\tdefinition_sha256\texplicit_sha256\n' \
    > "$CONTEXT/native-environment-inventory.tsv"
  printf 'environment\tprobe\tresult\tevidence_sha256\n' \
    > "$CONTEXT/native-environment-probes.tsv"

  create_native_environment core "$REPO/environments/native-core.yml"
  create_native_environment qdnaseq "$REPO/environments/native-qdnaseq.yml"
  if [[ "$SUITE" == quickstart1 ]]; then
    create_native_environment ichorcna "$REPO/environments/native-ichorcna.yml"
  fi

  record_phase_resources native-environments-with-cache
  [[ "$CONDA_PACKAGE_CACHE" == "$RUNNER_TEMP/oncotracer-conda-pkgs-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" ]]
  [[ -d "$CONDA_PACKAGE_CACHE" && ! -L "$CONDA_PACKAGE_CACHE" ]]
  rm -rf -- "$RUNNER_TEMP/oncotracer-conda-pkgs-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  [[ ! -e "$CONDA_PACKAGE_CACHE" && ! -L "$CONDA_PACKAGE_CACHE" ]]
  record_phase_resources native-package-cache-released

  run_native_environment_probe core bwa 1 'Program:[[:space:]]+bwa' \
    "$ONCOTRACER_CORE_PREFIX/bin/bwa"
  run_native_environment_probe core samtools 0 'samtools' \
    "$ONCOTRACER_CORE_PREFIX/bin/samtools" --version
  run_native_environment_probe core minimap2 0 '(minimap2|^[0-9]+[.][0-9]+)' \
    "$ONCOTRACER_CORE_PREFIX/bin/minimap2" --version
  run_native_environment_probe core pigz 0 'pigz' \
    "$ONCOTRACER_CORE_PREFIX/bin/pigz" --version
  run_native_environment_probe core picard 1 '(Picard|USAGE|CommandLineProgram)' \
    "$ONCOTRACER_CORE_PREFIX/bin/picard" -h
  run_native_environment_probe qdnaseq rscript 0 'QDNASEQ_OK' \
    env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
    "$ONCOTRACER_QDNASEQ_PREFIX/bin/Rscript" --vanilla -e \
    'suppressPackageStartupMessages(library(Biobase)); suppressPackageStartupMessages(library(QDNAseq)); cat("QDNASEQ_OK\n")'
  if [[ "$SUITE" == quickstart1 ]]; then
    run_native_environment_probe ichorcna rscript 0 'ICHORCNA_OK' \
      env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
      "$ONCOTRACER_ICHORCNA_PREFIX/bin/Rscript" --vanilla -e \
      'suppressPackageStartupMessages(library(ichorCNA)); cat("ICHORCNA_OK\n")'
    run_native_environment_probe ichorcna readcounter 255 \
      $'^Please specify a BAM file[.]$\n^Usage: .*/readCounter \\[options\\] <BAM file>$' \
      "$ONCOTRACER_ICHORCNA_PREFIX/bin/readCounter"
  fi
}
cleanup_job_swap() {
  local status=$? expected active_swap_names cleanup_status=0
  expected="$RUNNER_TEMP/oncotracer-swap-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  set +e
  if (( PARITY_SWAP_GIB == 0 )); then
    if [[ "$SWAP_FILE" != none ]]; then
      echo "Refusing zero-swap cleanup with unexpected identity: $SWAP_FILE" >&2
      (( status != 0 )) && return "$status"
      return 1
    fi
    return "$status"
  fi
  if [[ "$SWAP_FILE" != "$expected" ]]; then
    echo "Refusing to clean unexpected swap path: $SWAP_FILE" >&2
    cleanup_status=1
  elif ! active_swap_names="$(sudo -n swapon --show=NAME --noheadings --raw 2>/dev/null)"; then
    echo "Refusing to remove $SWAP_FILE because active swap could not be established" >&2
    cleanup_status=1
  elif grep -Fx -- "$SWAP_FILE" <<< "$active_swap_names" >/dev/null; then
    if ! sudo -n swapoff -- "$SWAP_FILE"; then
      echo "Refusing to remove active swap after swapoff failed: $SWAP_FILE" >&2
      cleanup_status=1
    fi
  fi
  if (( cleanup_status != 0 )); then
    (( status != 0 )) && return "$status"
    return "$cleanup_status"
  fi
  if [[ -e "$SWAP_FILE" || -L "$SWAP_FILE" ]]; then
    if [[ ! -f "$SWAP_FILE" || -L "$SWAP_FILE" ]]; then
      echo "Refusing to remove non-regular job swap path: $SWAP_FILE" >&2
      cleanup_status=1
    elif ! sudo -n rm -f -- "$RUNNER_TEMP/oncotracer-swap-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"; then
      echo "Failed to remove inactive job-owned swap file: $SWAP_FILE" >&2
      cleanup_status=1
    fi
  fi
  (( status != 0 )) && return "$status"
  return "$cleanup_status"
}

write_ichor_asset_manifest() {
  local root="$1" output="$2" filename expected_bytes expected_sha observed_sha observed_bytes
  [[ -d "$root" && ! -L "$root" ]]
  printf 'filename\tbytes\tsha256\n' > "$output"
  while IFS=$'\t' read -r filename expected_bytes expected_sha; do
    [[ -n "$filename" ]] || continue
    require_file "$root/$filename"
    observed_bytes="$(wc -c < "$root/$filename")"
    observed_sha="$(sha256sum "$root/$filename" | awk '{print $1}')"
    [[ "$observed_bytes" == "$expected_bytes" && "$observed_sha" == "$expected_sha" ]] || {
      echo "ERROR: immutable SAMURAI ichorCNA asset mismatch: $root/$filename" >&2
      exit 1
    }
    printf '%s\t%s\t%s\n' "$filename" "$observed_bytes" "$observed_sha" >> "$output"
  done <<'EOF'
GRCh38.GCA_000001405.2_centromere_acen.txt	853	5ca2fed871adaa395773d932b94d40866690f69797694a21b057e8e1b3681e22
HD_ULP_PoN_hg38_500kb_median_normAutosome_median.rds	675953	2f2e94d529d0ef3ca74b93e0814c89fae0f6b918b38a7efec3bf4207c25452c0
Koren_repTiming_hg38_500kb.wig	59884	d7d20a549fb2a54a91dd73562ca820524a33b8bf33ab45bee881b1e031c96c8c
gc_hg38_500kb.wig	52184	4ae9c5d7f3e8260b3d192e88b21e717ac7f761946ba16e896b7d375557e85b57
map_hg38_500kb.wig	54494	18efe127d1fde052b5537d4bf0494f73710fe38eb3ec0e7e49fa483b4c647d89
EOF
}

resolve_owned_native_reference_root() {
  local kind="$1" identity root marker
  case "$kind" in
    samurai-hg38|ichorcna-hg38-500kb) ;;
    *)
      echo "ERROR: unsupported native reference-cache kind: $kind" >&2
      return 2
      ;;
  esac
  read -r identity root < <(python3 - "$REPO" "$V2_PROJECT_ROOT" "$kind" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from oncotracer_cli.engine import _reference_identity

project = Path(sys.argv[2]).resolve(strict=True)
kind = sys.argv[3]
identity = _reference_identity(kind)
root = project / ".oncotracer" / "reference-cache" / f"{kind}-{identity[:16]}"
print(identity, root)
PY
)
  [[ "$identity" =~ ^[0-9a-f]{64}$ ]]
  [[ -d "$root" && ! -L "$root" ]]
  marker="$root/.oncotracer-reference-owner.json"
  require_file "$marker"
  python3 - "$marker" "$root" "$identity" "$kind" <<'PY'
import json
import os
import sys
from pathlib import Path

marker, root = map(Path, sys.argv[1:3])
identity = sys.argv[3]
kind = sys.argv[4]
if marker.is_symlink() or root.is_symlink():
    raise SystemExit("owned native reference cache or marker is a symlink")
payload = json.loads(marker.read_text(encoding="utf-8"))
expected = {
    "schema": "oncotracer-reference-cache-owner-v1",
    "kind": kind,
    "identity": identity,
    "canonical_path": str(root),
}
if payload != expected or Path(os.path.realpath(root)) != root:
    raise SystemExit("native reference cache ownership mismatch")
PY
  printf '%s\n' "$root"
}

[[ ! -e "$TEST_ROOT" && ! -L "$TEST_ROOT" ]] || {
  echo "Refusing to adopt a pre-existing parity root: $TEST_ROOT" >&2
  exit 1
}
mkdir -p "$TEST_ROOT/configs" "$REPORT_ROOT" "$NEXTFLOW_REPORT_ROOT" \
  "$CONTEXT/manifests" "$CONTEXT/configs/parity" "$CONTEXT/configs/input" \
  "$CONTEXT/qdnaseq-annotation" "$CONTEXT/native-environment-probes" \
  "$CONTEXT/native-environments" "$RESOURCE_PHASE_ROOT"
[[ -d "$TEST_ROOT" && ! -L "$TEST_ROOT" ]]
printf 'reference\timage_id\tcreated_by_job\n' > "$IMAGE_OWNERSHIP"

log "Require safe hosted-runner capacity"
DOCKER_ROOT_DIR="$(docker info --format '{{.DockerRootDir}}')"
bash "$REPO/scripts/ci_resource_preflight.sh" \
  --purpose "OncoTracer $SUITE frozen-v1.1/native-v2 parity" \
  --min-free-gib "$MIN_FREE_GIB" \
  --min-physical-gib "$MIN_PHYSICAL_GIB" \
  --min-addressable-gib "$MIN_ADDRESSABLE_GIB" \
  --planned-swap-gib "$PARITY_SWAP_GIB" \
  --standard-contract-free-gib "$STANDARD_RUNNER_CONTRACT_FREE_GIB" \
  --run-id "$GITHUB_RUN_ID" \
  --run-attempt "$GITHUB_RUN_ATTEMPT" \
  --suite "$SUITE" \
  --candidate-sha "$CANDIDATE_SHA" \
  --expected-swap-file "$SWAP_FILE" \
  --path "$GITHUB_WORKSPACE" \
  --path "$RUNNER_TEMP" \
  --path /tmp \
  --path "$DOCKER_ROOT_DIR" \
  | tee "$RESOURCE_PREFLIGHT"
python3 "$REPO/scripts/verify_ci_resource_preflight.py" \
  --evidence "$RESOURCE_PREFLIGHT" \
  --run-id "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT" \
  --suite "$SUITE" --candidate-sha "$CANDIDATE_SHA" \
  --min-free-gib "$MIN_FREE_GIB" \
  --min-physical-gib "$MIN_PHYSICAL_GIB" \
  --min-addressable-gib "$MIN_ADDRESSABLE_GIB" \
  --planned-swap-gib "$PARITY_SWAP_GIB" \
  --standard-contract-free-gib "$STANDARD_RUNNER_CONTRACT_FREE_GIB" \
  --expected-swap-file "$SWAP_FILE" \
  --path "$GITHUB_WORKSPACE" --path "$RUNNER_TEMP" --path /tmp \
  --path "$DOCKER_ROOT_DIR"
record_phase_resources preflight-passed

log "Install frozen-comparator prerequisites"
. "$REPO/scripts/ci_parity_prerequisites.sh"
prerequisite_prefixes=()
if [[ -n "${CONDA:-}" ]]; then
  prerequisite_prefixes+=("$CONDA/bin")
fi
if [[ -n "${CONDA_EXE:-}" ]]; then
  prerequisite_prefixes+=("$(dirname -- "$CONDA_EXE")")
fi
ensure_frozen_comparator_prerequisites "${prerequisite_prefixes[@]}"

df -h

log "Install and authenticate Nextflow $NEXTFLOW_VERSION"
mkdir -p "$PINNED_NEXTFLOW_DIR"
curl --fail --location --retry 5 --retry-all-errors \
  --output "$RUNNER_TEMP/nextflow-$NEXTFLOW_VERSION-dist" \
  "$NEXTFLOW_DIST_URL"
printf '%s  %s\n' "$NEXTFLOW_DIST_SHA256" \
  "$RUNNER_TEMP/nextflow-$NEXTFLOW_VERSION-dist" | sha256sum --strict -c -
install -m 0755 "$RUNNER_TEMP/nextflow-$NEXTFLOW_VERSION-dist" "$NEXTFLOW"
printf '%s  %s\n' "$NEXTFLOW_DIST_SHA256" "$NEXTFLOW" | sha256sum --strict -c -
"$NEXTFLOW" -version 2>&1 | tee "$RUNNER_TEMP/nextflow-version.txt"
grep -E "version[[:space:]]+$NEXTFLOW_VERSION([[:space:]]|$)" "$RUNNER_TEMP/nextflow-version.txt"
printf 'version=%s\nurl=%s\nsha256=%s\n' \
  "$NEXTFLOW_VERSION" "$NEXTFLOW_DIST_URL" "$NEXTFLOW_DIST_SHA256" \
  > "$RUNNER_TEMP/nextflow-identity.txt"

log "Authenticate frozen v1.1 container"
docker info
v1_created_by_job=1
if docker image inspect "$V1_DOCKER_IMAGE" >/dev/null 2>&1; then
  v1_created_by_job=0
fi
docker pull "$V1_DOCKER_IMAGE"
v1_image_id="$(docker image inspect "$V1_DOCKER_IMAGE" --format '{{.Id}}')"
record_image_ownership "$V1_DOCKER_IMAGE" "$v1_image_id" "$v1_created_by_job"
docker image inspect "$V1_DOCKER_IMAGE" \
  --format '{{range .RepoDigests}}{{println .}}{{end}}' \
  | tee "$RUNNER_TEMP/v1-docker-repodigests.txt"
grep -Fx "$V1_DOCKER_IMAGE" "$RUNNER_TEMP/v1-docker-repodigests.txt"
printf '%s\n' "$V1_DOCKER_IMAGE" > "$RUNNER_TEMP/v1-docker-digest.txt"

log "Add addressable-memory headroom"
if (( PARITY_SWAP_GIB > 0 )); then
  [[ ! -e "$SWAP_FILE" && ! -L "$SWAP_FILE" ]] || {
    echo "Refusing to replace pre-existing job swap path: $SWAP_FILE" >&2
    exit 1
  }
  command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1 || {
    echo "ERROR: this low-memory runner needs a 32-GiB job-owned swap file, but passwordless sudo is unavailable." >&2
    exit 1
  }
  trap cleanup_job_swap EXIT
  sudo -n fallocate -l "${PARITY_SWAP_GIB}G" "$SWAP_FILE"
  sudo -n chmod 600 "$SWAP_FILE"
  sudo -n mkswap "$SWAP_FILE"
  sudo -n swapon "$SWAP_FILE"
else
  [[ "$SWAP_FILE" == none ]]
fi
free -h
record_phase_resources swap-active

log "Build copied native v2 executable and authenticate source identities"
SOURCE_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
test "$SOURCE_COMMIT" = "$CANDIDATE_SHA"
SOURCE_SHA256="$(git -C "$REPO" -c tar.umask=0002 archive --format=tar "$SOURCE_COMMIT" | sha256sum | awk '{print $1}')"
printf '%s\n' "$SOURCE_COMMIT" > "$RUNNER_TEMP/v2-source-commit.txt"
printf '%s\n' "$SOURCE_SHA256" > "$RUNNER_TEMP/v2-source-sha256.txt"
python3 "$REPO/scripts/build_native_binary.py" \
  --output "$REPO/dist/oncotracer" \
  --source-commit "$SOURCE_COMMIT" \
  --source-sha256 "$SOURCE_SHA256"
chmod 0755 "$REPO/dist/oncotracer"
"$REPO/dist/oncotracer" --version
sha256sum "$REPO/dist/oncotracer" | tee "$RUNNER_TEMP/native-binary.sha256"
BINARY_SHA256="$(awk 'NR == 1 {print $1}' "$RUNNER_TEMP/native-binary.sha256")"
"$REPO/dist/oncotracer" provenance --json > "$RUNNER_TEMP/native-binary-provenance.json"
jq -e --arg commit "$SOURCE_COMMIT" --arg sha256 "$SOURCE_SHA256" --arg binary "$BINARY_SHA256" '
  .source_commit == $commit and .source_sha256 == $sha256 and
  .source_tree_dirty == false and .binary_sha256 == $binary
' "$RUNNER_TEMP/native-binary-provenance.json"

V1_BASELINE_COMMIT="$(git -C "$V1_REPO" rev-parse HEAD)"
test "$V1_BASELINE_COMMIT" = "$V1_1_COMMIT"
V1_TAG_COMMIT="$(git -C "$V1_REPO" rev-list -n 1 v1.1)"
test "$V1_TAG_COMMIT" = "$V1_1_COMMIT"
printf '%s\n' "$V1_BASELINE_COMMIT" > "$RUNNER_TEMP/v1-baseline-commit.txt"
printf '%s\n' "$V1_TAG_COMMIT" > "$RUNNER_TEMP/v1-tag-commit.txt"

log "Prepare public inputs and isolated v1/v2 configs"
if [[ "$SUITE" == quickstart1 ]]; then
  "$REPO/dist/oncotracer" quickstart 1 --test-root "$INPUT_ROOT" --download-only
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/illumina.quickstart.yml" \
    --destination "$TEST_ROOT/configs/v1-illumina.yml" \
    --lpwgs-root "$V1_PROJECT_ROOT" --outdir "$TEST_ROOT/v1/illumina"
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/illumina.quickstart.yml" \
    --destination "$TEST_ROOT/configs/v2-illumina.yml" \
    --lpwgs-root "$V2_PROJECT_ROOT" --outdir "$TEST_ROOT/v2/illumina"
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/ont.quickstart.yml" \
    --destination "$TEST_ROOT/configs/v1-ont.yml" \
    --lpwgs-root "$V1_PROJECT_ROOT" --outdir "$TEST_ROOT/v1/ont"
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/ont.quickstart.yml" \
    --destination "$TEST_ROOT/configs/v2-ont.yml" \
    --lpwgs-root "$V2_PROJECT_ROOT" --outdir "$TEST_ROOT/v2/ont"
else
  "$REPO/dist/oncotracer" quickstart 2 --test-root "$INPUT_ROOT" --download-only
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml" \
    --destination "$TEST_ROOT/configs/v1-hcc1143.yml" \
    --lpwgs-root "$V1_PROJECT_ROOT" --outdir "$TEST_ROOT/v1/hcc1143"
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml" \
    --destination "$TEST_ROOT/configs/v2-hcc1143.yml" \
    --lpwgs-root "$V2_PROJECT_ROOT" --outdir "$TEST_ROOT/v2/hcc1143"
fi
record_phase_resources public-inputs-ready

log "Pin every nested SAMURAI runtime image by immutable digest"
if [[ "$SUITE" == quickstart1 ]]; then
  cat > "$PINS" <<'EOF'
container	manifest_digest
quay.io/biocontainers/samtools:1.22.1--h96c455f_0	sha256:23dc2c29f457a448a0d341fb97b2632a2c8004925214cb6420562a5b12adf8a2
community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6	sha256:e269216786463d44f9d83a0d6e877b34bca2c7b4d35211b4b369fe98e39ef1a5
quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1	sha256:fb6135876beca3059ed1414d5082833d5bbf1fb3f0f64e51ca8b29fb47adaa75
docker.io/t0shy/qpdf-docker:11.3.0	sha256:744f00189f4b0f3f1273073212102b32e0505fea528c9516e4252b9345e482d3
community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf	sha256:677f4c8e38cfd741926e5bd1e80d96b756540bc6a9e9c5ed520aa7a98358d11d
community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2	sha256:209b7aeca568155a099873da6e830427bf0a9d5418426b39f913db736d53e20b
community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4	sha256:c6240b1bcc57de07d9a92373f6fad080870bba0075be6cd25c6d37179d928c72
quay.io/einar_rainhart/pandas-pandera:1.5.3	sha256:39fae3f3a2edb8cb174b3ffade1741b6b1ec850a323b4f7a0dca6908f2e49cf8
community.wave.seqera.io/library/polars_procps-ng_typer:d1a53d7945a021e3	sha256:3b7464a65a9b23f0969b19767303b8727ab9f7dce83b1885cd8f6334d75ed59e
community.wave.seqera.io/library/procps-ng_r-argparser_r-dplyr_r-ggplot2_pruned:10da72fa04bcba1a	sha256:28626b999449abe6ddc2167228023ec93e90d109a540be97d0a789d2093b4e8b
EOF
else
  cat > "$PINS" <<'EOF'
container	manifest_digest
quay.io/biocontainers/samtools:1.22.1--h96c455f_0	sha256:23dc2c29f457a448a0d341fb97b2632a2c8004925214cb6420562a5b12adf8a2
community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6	sha256:e269216786463d44f9d83a0d6e877b34bca2c7b4d35211b4b369fe98e39ef1a5
quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1	sha256:fb6135876beca3059ed1414d5082833d5bbf1fb3f0f64e51ca8b29fb47adaa75
docker.io/t0shy/qpdf-docker:11.3.0	sha256:744f00189f4b0f3f1273073212102b32e0505fea528c9516e4252b9345e482d3
community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf	sha256:677f4c8e38cfd741926e5bd1e80d96b756540bc6a9e9c5ed520aa7a98358d11d
EOF
fi
printf 'container\tmanifest_digest\timage_id\n' > "$PREFLIGHT"
while IFS=$'\t' read -r container digest; do
  [[ "$container" == container ]] && continue
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
  repository="${container%:*}"
  immutable="$repository@$digest"
  immutable_created_by_job=1
  if docker image inspect "$immutable" >/dev/null 2>&1; then
    immutable_created_by_job=0
  fi
  docker pull "$immutable"
  expected_id="$(docker image inspect "$immutable" --format '{{.Id}}')"
  record_image_ownership "$immutable" "$expected_id" "$immutable_created_by_job"
  mutable_created_by_job=1
  if docker image inspect "$container" >/dev/null 2>&1; then
    mutable_created_by_job=0
    existing_id="$(docker image inspect "$container" --format '{{.Id}}')"
    test "$existing_id" = "$expected_id" || {
      echo "mutable SAMURAI tag $container resolves to unexpected $existing_id" >&2
      exit 1
    }
  else
    docker tag "$immutable" "$container"
  fi
  observed_id="$(docker image inspect "$container" --format '{{.Id}}')"
  test "$observed_id" = "$expected_id"
  record_image_ownership "$container" "$observed_id" "$mutable_created_by_job"
  docker image inspect "$container" --format '{{range .RepoDigests}}{{println .}}{{end}}' \
    | grep -E "@${digest}$"
  printf '%s\t%s\t%s\n' "$container" "$digest" "$observed_id" >> "$PREFLIGHT"
done < "$PINS"
record_phase_resources frozen-images-ready

log "Configure nested SAMURAI tracing and comparator resources"
cat > "$TEST_ROOT/configs/v1-pinned-nextflow.config" <<EOF
process {
  withName: RUN_ILLUMINA_SAMURAI {
    env.PATH = '$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
  }
  withName: RUN_ONT_SAMURAI {
    env.PATH = '$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
  }
}
EOF

nested_config() {
  local home="$1"
  mkdir -p "$home"
  cat > "$home/config" <<'EOF'
params.oncotracer_nested_audit_policy_sha256 = '__ONCOTRACER_AUDIT_POLICY_SHA256__'
executor.queueSize = 4
process {
  resourceLimits = [cpus: 4, memory: '14.GB', time: '6.h']
}
conda { useMamba = false }
trace.fields = 'task_id,hash,name,status,exit,container'
EOF
}

seal_nested_config() {
  local config="$1" source digest policy_sha
  shift
  policy_sha="$(
    {
      printf 'config-template\0'
      cat "$config"
      for source in "$@"; do
        digest="$(sha256sum "$source" | awk '{print $1}')"
        printf 'source\0%s\0%s\0' "$(basename "$source")" "$digest"
      done
    } | sha256sum | awk '{print $1}'
  )"
  [[ "$policy_sha" =~ ^[0-9a-f]{64}$ ]]
  grep -Fxq \
    "params.oncotracer_nested_audit_policy_sha256 = '__ONCOTRACER_AUDIT_POLICY_SHA256__'" \
    "$config"
  sed -i "s/__ONCOTRACER_AUDIT_POLICY_SHA256__/$policy_sha/" "$config"
  grep -Fxq \
    "params.oncotracer_nested_audit_policy_sha256 = '$policy_sha'" "$config"
}

if [[ "$SUITE" == quickstart1 ]]; then
  nested_config "$TEST_ROOT/v1/illumina/01_samurai_illumina/.nextflow"
  seal_nested_config "$TEST_ROOT/v1/illumina/01_samurai_illumina/.nextflow/config"
  nested_config "$TEST_ROOT/v1/ont/01_samurai_ont/.nextflow"
  cat >> "$TEST_ROOT/v1/ont/01_samurai_ont/.nextflow/config" <<EOF
process {
  withName: ICHORCNA_RUN {
    cache = false
    containerOptions = '-v $REPO/bin/scripts:/opt/oncotracer/scripts:ro -v $REPO/bin/scripts/v1_ichorcna_profile.R:/.Rprofile:ro'
  }
}
EOF
  seal_nested_config "$TEST_ROOT/v1/ont/01_samurai_ont/.nextflow/config" \
    "$REPO/bin/scripts/ichorcna_plot_compat.R" \
    "$REPO/bin/scripts/v1_ichorcna_profile.R"
else
  nested_config "$TEST_ROOT/v1/hcc1143/01_samurai_illumina/.nextflow"
  seal_nested_config "$TEST_ROOT/v1/hcc1143/01_samurai_illumina/.nextflow/config"
fi

log "Run complete frozen v1.1 baseline"
if [[ "$SUITE" == quickstart1 ]]; then
  python3 "$REPO/tests/verify_nested_samurai.py" \
    --snapshot-root "$TEST_ROOT/v1/illumina/01_samurai_illumina" \
    --snapshot-out "$ILLUMINA_PRE_INVENTORY"
  env PATH="$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$NEXTFLOW" -log "$NEXTFLOW_REPORT_ROOT/v1-illumina.nextflow.log" \
    run "$V1_REPO/main.nf" \
    -c "$TEST_ROOT/configs/v1-pinned-nextflow.config" --docker \
    --docker_image "$V1_DOCKER_IMAGE" \
    -params-file "$TEST_ROOT/configs/v1-illumina.yml" \
    -work-dir "$TEST_ROOT/work/v1-illumina" \
    -with-report "$NEXTFLOW_REPORT_ROOT/v1-illumina.html" \
    -with-trace "$NEXTFLOW_REPORT_ROOT/v1-illumina.tsv" \
    2>&1 | tee "$NEXTFLOW_REPORT_ROOT/v1-illumina.command.log"
  python3 "$REPO/tests/verify_nested_samurai.py" \
    --snapshot-root "$TEST_ROOT/v1/ont/01_samurai_ont" \
    --snapshot-out "$ONT_PRE_INVENTORY"
  env PATH="$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$NEXTFLOW" -log "$NEXTFLOW_REPORT_ROOT/v1-ont.nextflow.log" \
    run "$V1_REPO/main.nf" \
    -c "$TEST_ROOT/configs/v1-pinned-nextflow.config" --docker \
    --docker_image "$V1_DOCKER_IMAGE" \
    -params-file "$TEST_ROOT/configs/v1-ont.yml" \
    -work-dir "$TEST_ROOT/work/v1-ont" \
    -with-report "$NEXTFLOW_REPORT_ROOT/v1-ont.html" \
    -with-trace "$NEXTFLOW_REPORT_ROOT/v1-ont.tsv" \
    2>&1 | tee "$NEXTFLOW_REPORT_ROOT/v1-ont.command.log"
else
  python3 "$REPO/tests/verify_nested_samurai.py" \
    --snapshot-root "$TEST_ROOT/v1/hcc1143/01_samurai_illumina" \
    --snapshot-out "$HCC_PRE_INVENTORY"
  env PATH="$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$NEXTFLOW" -log "$NEXTFLOW_REPORT_ROOT/v1-hcc1143.nextflow.log" \
    run "$V1_REPO/main.nf" \
    -c "$TEST_ROOT/configs/v1-pinned-nextflow.config" --docker \
    --docker_image "$V1_DOCKER_IMAGE" \
    -params-file "$TEST_ROOT/configs/v1-hcc1143.yml" \
    -work-dir "$TEST_ROOT/work/v1-hcc1143" \
    -with-report "$NEXTFLOW_REPORT_ROOT/v1-hcc1143.html" \
    -with-trace "$NEXTFLOW_REPORT_ROOT/v1-hcc1143.tsv" \
    2>&1 | tee "$NEXTFLOW_REPORT_ROOT/v1-hcc1143.command.log"
fi

SAMURAI_SOURCE="$V1_PROJECT_ROOT/.oncotracer/samurai/v1.4.0"
require_file "$SAMURAI_SOURCE/.oncotracer-source"
test "$(git -C "$SAMURAI_SOURCE" rev-parse HEAD)" = "$SAMURAI_COMMIT"
grep -Fx 'revision=v1.4.0' "$SAMURAI_SOURCE/.oncotracer-source"
grep -Fx "commit=$SAMURAI_COMMIT" "$SAMURAI_SOURCE/.oncotracer-source"
cp "$SAMURAI_SOURCE/.oncotracer-source" "$RUNNER_TEMP/samurai.oncotracer-source"
if [[ "$SUITE" == quickstart1 ]]; then
  write_ichor_asset_manifest "$SAMURAI_SOURCE/assets/ichorcna" \
    "$CONTEXT/manifests/frozen-ichorcna-assets-manifest.tsv"
fi

log "Select and authenticate completed nested SAMURAI traces"
if [[ "$SUITE" == quickstart1 ]]; then
  python3 "$REPO/tests/verify_nested_samurai.py" \
    --suite quickstart1 --pins "$PINS" --runtime-out "$RUNTIME" \
    --selected-dir "$CONTEXT" --selection-out "$SELECTION" \
    --illumina-pre-inventory "$ILLUMINA_PRE_INVENTORY" \
    --ont-pre-inventory "$ONT_PRE_INVENTORY" \
    --illumina-root "$TEST_ROOT/v1/illumina/01_samurai_illumina" \
    --ont-root "$TEST_ROOT/v1/ont/01_samurai_ont"
else
  python3 "$REPO/tests/verify_nested_samurai.py" \
    --suite quickstart2 --pins "$PINS" --runtime-out "$RUNTIME" \
    --selected-dir "$CONTEXT" --selection-out "$SELECTION" \
    --hcc-pre-inventory "$HCC_PRE_INVENTORY" \
    --hcc-root "$TEST_ROOT/v1/hcc1143/01_samurai_illumina"
fi
record_phase_resources frozen-traces-authenticated

log "Seal the frozen reference manifest, then release only its job-owned copy"
python3 "$REPO/tests/parity_audit.py" manifest \
  "$V1_PROJECT_ROOT/references/samurai_hg38" \
  "$CONTEXT/manifests/shared-reference-manifest.tsv"
[[ -d "$V1_PROJECT_ROOT" && ! -L "$V1_PROJECT_ROOT" ]]
[[ -d "$V1_PROJECT_ROOT/references" && ! -L "$V1_PROJECT_ROOT/references" ]]
if [[ "$SUITE" == quickstart1 ]]; then
  rm -rf -- "$GITHUB_WORKSPACE/oncotracer-parity-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-quickstart1/frozen-v1-project/references"
else
  rm -rf -- "$GITHUB_WORKSPACE/oncotracer-parity-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-quickstart2/frozen-v1-project/references"
fi
[[ ! -e "$V1_PROJECT_ROOT/references" && ! -L "$V1_PROJECT_ROOT/references" ]]
mkdir -p "$V2_PROJECT_ROOT"
[[ ! -e "$V2_PROJECT_ROOT/references" && ! -L "$V2_PROJECT_ROOT/references" ]]
record_phase_resources frozen-reference-released

log "Release only image references proven to have been created by this job"
cp "$IMAGE_OWNERSHIP" "$CONTEXT/job-image-reference-ownership.tsv"
cp "$PINS" "$CONTEXT/nested-v1-container-pins.tsv"
cp "$PREFLIGHT" "$CONTEXT/nested-v1-container-preflight.tsv"
remove_owned_image_references
record_phase_resources frozen-images-released

log "Create and probe only the native environments exercised by $SUITE"
create_minimal_native_environments

log "Run complete copied native v2 executable"
if [[ "$SUITE" == quickstart1 ]]; then
  "$REPO/dist/oncotracer" run --backend host \
    --config "$TEST_ROOT/configs/v2-illumina.yml" --threads 4 \
    2>&1 | tee "$REPORT_ROOT/v2-illumina.log"
  "$REPO/dist/oncotracer" run --backend host \
    --config "$TEST_ROOT/configs/v2-ont.yml" --threads 4 \
    2>&1 | tee "$REPORT_ROOT/v2-ont.log"
else
  "$REPO/dist/oncotracer" run --backend host \
    --config "$TEST_ROOT/configs/v2-hcc1143.yml" --threads 4 \
    2>&1 | tee "$REPORT_ROOT/v2-hcc1143.log"
fi
record_phase_resources native-runs-complete

if [[ "$SUITE" == quickstart1 ]]; then
  native_ichor_root="$(resolve_owned_native_reference_root ichorcna-hg38-500kb)"
  write_ichor_asset_manifest \
    "$native_ichor_root" \
    "$CONTEXT/manifests/native-ichorcna-assets-manifest.tsv"
  cmp --silent \
    "$CONTEXT/manifests/frozen-ichorcna-assets-manifest.tsv" \
    "$CONTEXT/manifests/native-ichorcna-assets-manifest.tsv" || {
      echo 'ERROR: frozen-v1/native-v2 ichorCNA asset manifests differ' >&2
      exit 1
    }
fi

log "Produce semantic parity reports"
if [[ "$SUITE" == quickstart1 ]]; then
  python3 "$REPO/tests/compare_native_parity.py" \
    --v1 "$TEST_ROOT/v1/illumina" --v2 "$TEST_ROOT/v2/illumina" \
    --outdir "$AUDIT_ROOT/illumina" --label "QuickStart 1 / Illumina" \
    --expected-samples ERR12341627
  python3 "$REPO/tests/compare_native_parity.py" \
    --v1 "$TEST_ROOT/v1/ont" --v2 "$TEST_ROOT/v2/ont" \
    --outdir "$AUDIT_ROOT/ont" --label "QuickStart 1 / ONT" \
    --expected-samples DRR165691
  python3 - "$AUDIT_ROOT" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
reports = [json.loads((root / name / 'parity_report.json').read_text()) for name in ('illumina', 'ont')]
overall = {
    'schema': 'oncotracer-quickstart1-parity-v1',
    'passed': all(report.get('passed') is True for report in reports),
    'reports': reports,
}
(root / 'quickstart1_parity.json').write_text(json.dumps(overall, indent=2, sort_keys=True) + '\n')
if not overall['passed']:
    raise SystemExit('QuickStart 1 parity failed')
PY
  # The native ichorCNA stage publishes every per-sample file to the caller
  # results root, so the single ONT compatibility marker is present both in the
  # sample directory and in the published root. Bind the evidence to marker
  # content instead of the publication layout: at least one marker must exist
  # and every copy must be byte-identical.
  mapfile -t native_markers < <(find "$TEST_ROOT/v2/ont" -type f \
    -name '*.ichorcna_plot_compat.tsv' -print | sort)
  if [[ "${#native_markers[@]}" -eq 0 ]]; then
    echo "missing native ichorCNA plot-compat marker: $TEST_ROOT/v2/ont" >&2
    exit 1
  fi
  mapfile -t native_marker_digests < <(
    sha256sum -- "${native_markers[@]}" | awk '{print $1}' | sort -u)
  if [[ "${#native_marker_digests[@]}" -ne 1 ]]; then
    echo "divergent native ichorCNA plot-compat markers:" >&2
    printf '  %s\n' "${native_markers[@]}" >&2
    exit 1
  fi
  cp "${native_markers[0]}" "$CONTEXT/v2-ont-ichorcna-plot-compat.tsv"
else
  python3 "$REPO/tests/compare_native_parity.py" \
    --v1 "$TEST_ROOT/v1/hcc1143" --v2 "$TEST_ROOT/v2/hcc1143" \
    --outdir "$AUDIT_ROOT" --label "QuickStart 2 / HCC1143" \
    --expected-samples HCC1143_DMSO,HCC1143_BEZ235,HCC1143_TRAMETINIB
fi

log "Collect immutable audit context and complete tree manifests"
printf '%s\n' "$CANDIDATE_SHA" > "$CONTEXT/workflow-event-sha.txt"
printf '%s\n' "$CANDIDATE_SHA" > "$CONTEXT/validated-candidate-sha.txt"
cp "$RUNNER_TEMP/v2-source-commit.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v2-source-sha256.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v1-baseline-commit.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v1-tag-commit.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v1-docker-digest.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v1-docker-repodigests.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/nextflow-version.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/nextflow-identity.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/samurai.oncotracer-source" "$CONTEXT/"
cp "$RUNNER_TEMP/native-binary.sha256" "$CONTEXT/"
cp "$RUNNER_TEMP/native-binary-provenance.json" "$CONTEXT/"
cp "$RUNNER_TEMP"/native-*.explicit.txt "$CONTEXT/"
cp "$IMAGE_OWNERSHIP" "$CONTEXT/job-image-reference-ownership.tsv"
cp "$PINS" "$CONTEXT/nested-v1-container-pins.tsv"
cp "$PREFLIGHT" "$CONTEXT/nested-v1-container-preflight.tsv"
cp "$TEST_ROOT/configs/v1-pinned-nextflow.config" "$CONTEXT/"

cp "$TEST_ROOT/configs"/*.yml "$CONTEXT/configs/parity/"
if [[ "$SUITE" == quickstart1 ]]; then
  cp "$INPUT_ROOT/configs"/*.yml "$CONTEXT/configs/input/"
  cp "$TEST_ROOT/v1/illumina/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v1-illumina-summary.txt"
  cp "$TEST_ROOT/v2/illumina/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v2-illumina-summary.txt"
  cp "$TEST_ROOT/v1/ont/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v1-ont-summary.txt"
  cp "$TEST_ROOT/v2/ont/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v2-ont-summary.txt"
  cp "$NEXTFLOW_REPORT_ROOT/v1-illumina.tsv" "$CONTEXT/v1-illumina-trace.tsv"
  cp "$NEXTFLOW_REPORT_ROOT/v1-ont.tsv" "$CONTEXT/v1-ont-trace.tsv"
  cp "$TEST_ROOT/v2/illumina/.oncotracer-native/trace.tsv" "$CONTEXT/v2-illumina-trace.tsv"
  cp "$TEST_ROOT/v2/ont/.oncotracer-native/trace.tsv" "$CONTEXT/v2-ont-trace.tsv"
  cp "$TEST_ROOT/v1/illumina/01_samurai_illumina/.nextflow/config" "$CONTEXT/nested-v1-illumina-nextflow.config"
  cp "$TEST_ROOT/v1/ont/01_samurai_ont/.nextflow/config" "$CONTEXT/nested-v1-ont-nextflow.config"
  cp "$TEST_ROOT/v1/illumina/01_samurai_illumina/nextflow_launch/.nextflow.log" "$CONTEXT/nested-v1-illumina-nextflow.log"
  cp "$TEST_ROOT/v1/ont/01_samurai_ont/nextflow_launch/.nextflow.log" "$CONTEXT/nested-v1-ont-nextflow.log"
  sha256sum "$REPO/bin/scripts/v1_ichorcna_profile.R" > "$CONTEXT/v1-ichorcna-profile.sha256"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$INPUT_ROOT/public" "$CONTEXT/manifests/public-input-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v1/illumina" "$CONTEXT/manifests/v1-illumina-output-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v2/illumina" "$CONTEXT/manifests/v2-illumina-output-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v1/ont" "$CONTEXT/manifests/v1-ont-output-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v2/ont" "$CONTEXT/manifests/v2-ont-output-manifest.tsv"
else
  cp "$INPUT_ROOT/configs/hcc1143_lpwgs"/* "$CONTEXT/configs/input/"
  cp "$INPUT_ROOT/public/hcc1143_lpwgs/samples.csv" "$CONTEXT/"
  cp "$REPO/examples/hcc1143_lpwgs/manifest.tsv" "$CONTEXT/"
  cp "$TEST_ROOT/v1/hcc1143/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v1-summary.txt"
  cp "$TEST_ROOT/v2/hcc1143/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v2-summary.txt"
  cp "$NEXTFLOW_REPORT_ROOT/v1-hcc1143.tsv" "$CONTEXT/v1-trace.tsv"
  cp "$TEST_ROOT/v2/hcc1143/.oncotracer-native/trace.tsv" "$CONTEXT/v2-trace.tsv"
  cp "$TEST_ROOT/v1/hcc1143/01_samurai_illumina/.nextflow/config" "$CONTEXT/nested-v1-hcc1143-nextflow.config"
  cp "$TEST_ROOT/v1/hcc1143/01_samurai_illumina/nextflow_launch/.nextflow.log" "$CONTEXT/nested-v1-hcc1143-nextflow.log"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$INPUT_ROOT/public/hcc1143_lpwgs" "$CONTEXT/manifests/public-input-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v1/hcc1143" "$CONTEXT/manifests/v1-hcc1143-output-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v2/hcc1143" "$CONTEXT/manifests/v2-hcc1143-output-manifest.tsv"
fi

mapfile -t qdnaseq_caches < <(
  find "$V2_PROJECT_ROOT/.oncotracer/reference-cache" \
    -mindepth 1 -maxdepth 1 -type d -name 'qdnaseq-hg38-100kb-*' -print
)
test "${#qdnaseq_caches[@]}" -eq 1
QDNASEQ_CACHE="${qdnaseq_caches[0]}"
QDNASEQ_GENERATION="$(
  PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}" \
    python3 - "$QDNASEQ_CACHE" <<'PY'
import sys
from pathlib import Path

from oncotracer_cli.engine import (
    _qdnaseq_generation_from_pointer,
    _reference_identity,
)

cache = Path(sys.argv[1])
generation = _qdnaseq_generation_from_pointer(
    cache, 100, _reference_identity("qdnaseq-hg38-100kb")
)
if generation is None:
    raise SystemExit(f"qDNAseq generation failed exact pointer/bundle validation: {cache}")
print(generation)
PY
)"
cp -a "$QDNASEQ_GENERATION/." "$CONTEXT/qdnaseq-annotation/"
python3 "$REPO/tests/parity_audit.py" manifest \
  "$CONTEXT/qdnaseq-annotation" "$CONTEXT/manifests/qdnaseq-annotation-manifest.tsv"
native_hg38_root="$(resolve_owned_native_reference_root samurai-hg38)"
python3 "$REPO/tests/parity_audit.py" manifest \
  "$native_hg38_root" \
  "$CONTEXT/manifests/native-reference-manifest.tsv"

record_phase_resources final
cp "$RESOURCE_PHASE_ROOT/final.txt" "$CONTEXT/hosted-resource-final.txt"

log "Enforce audit contract, finalize checksums, and verify from artifact shape"
python3 "$REPO/tests/parity_audit.py" verify \
  --suite "$SUITE" --audit "$AUDIT_ROOT" \
  --candidate-sha "$CANDIDATE_SHA" --source-sha256 "$SOURCE_SHA256" \
  --binary-sha256 "$BINARY_SHA256" --skip-checksums --write-summary
python3 "$REPO/tests/parity_audit.py" checksums "$AUDIT_ROOT"
python3 "$REPO/tests/parity_audit.py" verify \
  --suite "$SUITE" --audit "$AUDIT_ROOT" \
  --candidate-sha "$CANDIDATE_SHA" --source-sha256 "$SOURCE_SHA256" \
  --binary-sha256 "$BINARY_SHA256"

log "$SUITE parity and audit completed"
