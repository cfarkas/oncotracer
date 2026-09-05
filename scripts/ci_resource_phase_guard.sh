#!/usr/bin/env bash
# Fail closed before a parity phase writes evidence or starts more work.
set -Eeuo pipefail

if [[ $# -ne 10 ]]; then
  echo "Usage: $0 PHASE AVAILABLE_KIB MEM_KIB SWAP_KIB ACTIVE_SWAP_BYTES SWAP_REQUIRED MIN_PHYSICAL_GIB MIN_ADDRESSABLE_GIB PLANNED_SWAP_GIB RESERVE_GIB" >&2
  exit 2
fi

phase="$1"
available_kib="$2"
mem_total_kib="$3"
swap_total_kib="$4"
active_swap_size_bytes="$5"
swap_required="$6"
minimum_physical_gib="$7"
minimum_addressable_gib="$8"
planned_swap_gib="$9"
filesystem_reserve_gib="${10}"

[[ "$phase" =~ ^[a-z0-9][a-z0-9-]*$ ]] || exit 2
for value in "$available_kib" "$mem_total_kib" "$swap_total_kib" \
  "$active_swap_size_bytes" "$swap_required" "$minimum_physical_gib" \
  "$minimum_addressable_gib" "$planned_swap_gib" "$filesystem_reserve_gib"; do
  [[ "$value" =~ ^[0-9]+$ ]] || exit 2
done
[[ "$swap_required" == 0 || "$swap_required" == 1 ]] || exit 2

readonly KIB_PER_GIB=$((1024 * 1024))
readonly BYTES_PER_GIB=$((1024 * 1024 * 1024))
if (( available_kib < filesystem_reserve_gib * KIB_PER_GIB )); then
  printf 'ERROR: parity phase %s has only %s KiB free; the fail-closed filesystem reserve is %s GiB.\n' \
    "$phase" "$available_kib" "$filesystem_reserve_gib" >&2
  exit 1
fi
if (( mem_total_kib < minimum_physical_gib * KIB_PER_GIB )); then
  printf 'ERROR: parity phase %s has only %s KiB physical RAM; %s GiB is required.\n' \
    "$phase" "$mem_total_kib" "$minimum_physical_gib" >&2
  exit 1
fi

if (( planned_swap_gib > 0 )) && [[ "$phase" != preflight-passed ]]; then
  [[ "$swap_required" == 1 ]] || {
    printf 'ERROR: parity phase %s did not require its planned job swap.\n' "$phase" >&2
    exit 1
  }
else
  [[ "$swap_required" == 0 ]] || {
    printf 'ERROR: parity phase %s unexpectedly requires job swap.\n' "$phase" >&2
    exit 1
  }
fi

if (( swap_required == 1 )); then
  # Linux x86-64 reserves one 4-KiB page for the swap header. swapon reports
  # usable bytes, not the allocated file size; do not mistake that page for
  # missing job swap. All physical/addressable/disk floors remain unchanged.
  if (( active_swap_size_bytes < planned_swap_gib * BYTES_PER_GIB - 4096 )); then
    printf 'ERROR: parity phase %s lacks the exact planned %s-GiB job swap.\n' \
      "$phase" "$planned_swap_gib" >&2
    exit 1
  fi
  addressable_kib=$((mem_total_kib + swap_total_kib))
else
  addressable_kib=$((mem_total_kib + swap_total_kib + planned_swap_gib * KIB_PER_GIB))
fi
if (( addressable_kib < minimum_addressable_gib * KIB_PER_GIB )); then
  printf 'ERROR: parity phase %s has only %s KiB addressable memory; %s GiB is required.\n' \
    "$phase" "$addressable_kib" "$minimum_addressable_gib" >&2
  exit 1
fi

printf 'PHASE_RESOURCE_FLOOR_VERIFIED\n'
