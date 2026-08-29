#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 5 ]]; then
  echo "Usage: $0 MEM_TOTAL_KIB MIN_ADDRESSABLE_GIB RUNNER_TEMP RUN_ID RUN_ATTEMPT" >&2
  exit 2
fi
mem_total_kib="$1"
minimum_addressable_gib="$2"
runner_temp="$3"
run_id="$4"
run_attempt="$5"
[[ "$mem_total_kib" =~ ^[0-9]+$ && "$minimum_addressable_gib" =~ ^[1-9][0-9]*$ ]]
[[ "$run_id" =~ ^[1-9][0-9]*$ && "$run_attempt" =~ ^[1-9][0-9]*$ ]]
[[ "$runner_temp" = /* && "$runner_temp" != / ]]

if (( mem_total_kib >= minimum_addressable_gib * 1024 * 1024 )); then
  printf '0\tnone\n'
else
  printf '32\t%s/oncotracer-swap-%s-%s\n' \
    "${runner_temp%/}" "$run_id" "$run_attempt"
fi
