#!/usr/bin/env bash
set -Eeuo pipefail

# Classify a pair of permanent OCI tags without treating registry failures as
# absence.  Output is "missing" (both absent), "existing<TAB>sha256:..."
# (both present at the same digest), or
# "partial<TAB>present<TAB>missing<TAB>sha256:..." (one present).

if [[ "$#" -ne 2 || -z "$1" || -z "$2" || "$1" == "$2" ]]; then
  echo "usage: release_registry_pair.sh FIRST_REFERENCE SECOND_REFERENCE" >&2
  exit 64
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
resolver="$script_dir/release_registry_digest.sh"

resolve_reference() {
  local reference="$1" output_var="$2" status_var="$3"
  local digest="" status=0
  digest="$("$resolver" "$reference")" || status=$?
  printf -v "$output_var" '%s' "$digest"
  printf -v "$status_var" '%s' "$status"
}

first_digest=""
first_status=0
resolve_reference "$1" first_digest first_status
if [[ "$first_status" -ne 0 && "$first_status" -ne 44 ]]; then
  exit "$first_status"
fi

second_digest=""
second_status=0
resolve_reference "$2" second_digest second_status
if [[ "$second_status" -ne 0 && "$second_status" -ne 44 ]]; then
  exit "$second_status"
fi

if [[ "$first_status" -eq 44 && "$second_status" -eq 44 ]]; then
  printf 'missing\n'
  exit 0
fi

if [[ "$first_status" -eq 0 && "$second_status" -eq 44 ]]; then
  printf 'partial\t%s\t%s\t%s\n' "$1" "$2" "$first_digest"
  exit 0
fi

if [[ "$first_status" -eq 44 && "$second_status" -eq 0 ]]; then
  printf 'partial\t%s\t%s\t%s\n' "$2" "$1" "$second_digest"
  exit 0
fi

if [[ "$first_digest" != "$second_digest" ]]; then
  printf 'stable registry tags disagree: %s=%s, %s=%s\n' \
    "$1" "$first_digest" "$2" "$second_digest" >&2
  exit 67
fi

printf 'existing\t%s\n' "$first_digest"
