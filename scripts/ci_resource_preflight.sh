#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ci_resource_preflight.sh --purpose TEXT --min-free-gib N
       --min-physical-gib N --min-addressable-gib N --planned-swap-gib N
       --standard-contract-free-gib N --run-id N --run-attempt N --suite NAME
       --candidate-sha SHA --expected-swap-file PATH|none
       --path PATH [--path PATH ...]
EOF
  exit 2
}

PURPOSE=""
MIN_FREE_GIB=""
MIN_PHYSICAL_GIB=""
MIN_ADDRESSABLE_GIB=""
PLANNED_SWAP_GIB=""
STANDARD_CONTRACT_FREE_GIB=""
RUN_ID=""
RUN_ATTEMPT=""
SUITE=""
CANDIDATE_SHA=""
EXPECTED_SWAP_FILE=""
declare -a CHECK_PATHS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purpose) PURPOSE="${2:-}"; shift 2 ;;
    --min-free-gib) MIN_FREE_GIB="${2:-}"; shift 2 ;;
    --min-physical-gib) MIN_PHYSICAL_GIB="${2:-}"; shift 2 ;;
    --min-addressable-gib) MIN_ADDRESSABLE_GIB="${2:-}"; shift 2 ;;
    --planned-swap-gib) PLANNED_SWAP_GIB="${2:-}"; shift 2 ;;
    --standard-contract-free-gib) STANDARD_CONTRACT_FREE_GIB="${2:-}"; shift 2 ;;
    --run-id) RUN_ID="${2:-}"; shift 2 ;;
    --run-attempt) RUN_ATTEMPT="${2:-}"; shift 2 ;;
    --suite) SUITE="${2:-}"; shift 2 ;;
    --candidate-sha) CANDIDATE_SHA="${2:-}"; shift 2 ;;
    --expected-swap-file) EXPECTED_SWAP_FILE="${2:-}"; shift 2 ;;
    --path) CHECK_PATHS+=("${2:-}"); shift 2 ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

[[ -n "$PURPOSE" && ${#CHECK_PATHS[@]} -gt 0 ]] || usage
[[ "$RUN_ID" =~ ^[1-9][0-9]*$ && "$RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]] || usage
[[ "$SUITE" =~ ^[a-z0-9][a-z0-9-]*$ ]] || usage
[[ "$CANDIDATE_SHA" =~ ^[0-9a-f]{40}$ ]] || usage
for value in \
  "$MIN_FREE_GIB" "$MIN_PHYSICAL_GIB" "$MIN_ADDRESSABLE_GIB" \
  "$PLANNED_SWAP_GIB" \
  "$STANDARD_CONTRACT_FREE_GIB"; do
  [[ "$value" =~ ^[0-9]+$ ]] || usage
done
(( MIN_FREE_GIB > 0 && MIN_PHYSICAL_GIB > 0 && MIN_ADDRESSABLE_GIB > 0 )) || usage
if (( PLANNED_SWAP_GIB > 0 )); then
  [[ -n "${RUNNER_TEMP:-}" ]] || usage
  expected="$RUNNER_TEMP/oncotracer-swap-${RUN_ID}-${RUN_ATTEMPT}"
  [[ "$EXPECTED_SWAP_FILE" == "$expected" ]] || usage
else
  [[ "$EXPECTED_SWAP_FILE" == none ]] || usage
fi

available_kib=""
declare -a FILESYSTEM_PATHS=()
declare -a FILESYSTEM_DEVICES=()
declare -a FILESYSTEM_AVAILABLE_KIB=()
declare -A SEEN_PATHS=()
declare -A SEEN_DEVICES=()
for path in "${CHECK_PATHS[@]}"; do
  [[ "$path" != *$'\n'* && "$path" != *$'\r'* ]] || {
    printf 'ERROR: resource-preflight path contains a line break\n' >&2
    exit 1
  }
  [[ -z "${SEEN_PATHS[$path]+present}" ]] || {
    printf 'ERROR: duplicate resource-preflight path: %s\n' "$path" >&2
    exit 1
  }
  SEEN_PATHS["$path"]=1
  [[ -e "$path" ]] || {
    printf 'ERROR: resource-preflight path does not exist: %s\n' "$path" >&2
    exit 1
  }
  read -r device path_available_kib < <(
    LC_ALL=C df -Pk -- "$path" | awk 'NR == 2 {print $1, $4}'
  )
  [[ -n "$device" && "$path_available_kib" =~ ^[0-9]+$ ]] || {
    printf 'ERROR: could not establish free storage for %s\n' "$path" >&2
    exit 1
  }
  FILESYSTEM_PATHS+=("$path")
  FILESYSTEM_DEVICES+=("$device")
  FILESYSTEM_AVAILABLE_KIB+=("$path_available_kib")
  SEEN_DEVICES["$device"]=1
  if [[ -z "$available_kib" ]]; then
    available_kib="$path_available_kib"
  elif (( path_available_kib < available_kib )); then
    available_kib="$path_available_kib"
  fi
done

mem_total_kib="$(awk '$1 == "MemTotal:" {print $2}' /proc/meminfo)"
swap_total_kib="$(awk '$1 == "SwapTotal:" {print $2}' /proc/meminfo)"
[[ "$mem_total_kib" =~ ^[0-9]+$ && "$swap_total_kib" =~ ^[0-9]+$ ]] || {
  echo 'ERROR: could not establish physical and swap memory from /proc/meminfo' >&2
  exit 1
}

readonly KIB_PER_GIB=$((1024 * 1024))
required_free_kib=$((MIN_FREE_GIB * KIB_PER_GIB))
required_physical_kib=$((MIN_PHYSICAL_GIB * KIB_PER_GIB))
planned_swap_kib=$((PLANNED_SWAP_GIB * KIB_PER_GIB))
required_addressable_kib=$((MIN_ADDRESSABLE_GIB * KIB_PER_GIB))
addressable_kib=$((mem_total_kib + swap_total_kib + planned_swap_kib))

for index in "${!FILESYSTEM_PATHS[@]}"; do
  if (( FILESYSTEM_AVAILABLE_KIB[index] < required_free_kib )); then
    printf 'ERROR: %s requires at least %s GiB free on every checked filesystem; path %s on %s has only %s KiB.\n' \
      "$PURPOSE" "$MIN_FREE_GIB" "${FILESYSTEM_PATHS[index]}" \
      "${FILESYSTEM_DEVICES[index]}" "${FILESYSTEM_AVAILABLE_KIB[index]}" >&2
    printf 'The standard runner contract guarantees only %s GiB. Configure each checked filesystem with at least %s GiB free.\n' \
      "$STANDARD_CONTRACT_FREE_GIB" "$MIN_FREE_GIB" >&2
    echo 'Broad host cleanup is not an accepted remedy; capacities are never summed across filesystems.' >&2
    exit 1
  fi
done

if (( mem_total_kib < required_physical_kib )); then
  printf 'ERROR: %s requires at least %s GiB physical memory; only %s KiB is available.\n' \
    "$PURPOSE" "$MIN_PHYSICAL_GIB" "$mem_total_kib" >&2
  echo 'Configure an explicitly sized runner; swap is not a substitute for required physical memory.' >&2
  exit 1
fi

if (( addressable_kib < required_addressable_kib )); then
  printf 'ERROR: %s requires at least %s GiB addressable memory after planned swap; only %s KiB is available.\n' \
    "$PURPOSE" "$MIN_ADDRESSABLE_GIB" "$addressable_kib" >&2
  echo 'Configure an explicitly sized runner; do not weaken scientific resource limits.' >&2
  exit 1
fi

if (( MIN_FREE_GIB > STANDARD_CONTRACT_FREE_GIB )); then
  printf 'NOTICE: observed capacity passes, but the %s GiB standard-runner storage contract does not guarantee this %s GiB workload.\n' \
    "$STANDARD_CONTRACT_FREE_GIB" "$MIN_FREE_GIB" >&2
fi

printf 'resource_preflight_schema=oncotracer-hosted-resource-preflight-v3\n'
printf 'resource_preflight_status=PASS\n'
printf 'resource_preflight_run_id=%s\n' "$RUN_ID"
printf 'resource_preflight_run_attempt=%s\n' "$RUN_ATTEMPT"
printf 'resource_preflight_suite=%s\n' "$SUITE"
printf 'resource_preflight_candidate_sha=%s\n' "$CANDIDATE_SHA"
printf 'resource_preflight_purpose=%s\n' "$PURPOSE"
printf 'resource_preflight_minimum_available_kib=%s\n' "$available_kib"
printf 'resource_preflight_checked_path_count=%s\n' "${#FILESYSTEM_PATHS[@]}"
printf 'resource_preflight_unique_device_count=%s\n' "${#SEEN_DEVICES[@]}"
for index in "${!FILESYSTEM_PATHS[@]}"; do
  printf 'resource_preflight_checked_path_%03d_path=%s\n' \
    "$index" "${FILESYSTEM_PATHS[index]}"
  printf 'resource_preflight_checked_path_%03d_device=%s\n' \
    "$index" "${FILESYSTEM_DEVICES[index]}"
  printf 'resource_preflight_checked_path_%03d_available_kib=%s\n' \
    "$index" "${FILESYSTEM_AVAILABLE_KIB[index]}"
done
printf 'resource_preflight_required_free_gib=%s\n' "$MIN_FREE_GIB"
printf 'resource_preflight_mem_total_kib=%s\n' "$mem_total_kib"
printf 'resource_preflight_required_physical_gib=%s\n' "$MIN_PHYSICAL_GIB"
printf 'resource_preflight_swap_total_kib=%s\n' "$swap_total_kib"
printf 'resource_preflight_planned_swap_gib=%s\n' "$PLANNED_SWAP_GIB"
printf 'resource_preflight_expected_swap_file=%s\n' "$EXPECTED_SWAP_FILE"
printf 'resource_preflight_required_addressable_gib=%s\n' "$MIN_ADDRESSABLE_GIB"
printf 'resource_preflight_standard_contract_free_gib=%s\n' \
  "$STANDARD_CONTRACT_FREE_GIB"
