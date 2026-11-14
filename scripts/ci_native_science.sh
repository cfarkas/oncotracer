#!/usr/bin/env bash
# Current public scientific regression; historical v1.1 driver remains available.
set -Eeuo pipefail
suite="${1:?quickstart1 or quickstart2 required}"
[[ "$suite" == quickstart1 || "$suite" == quickstart2 ]]
REPO="$GITHUB_WORKSPACE/v2"
bash "$REPO/scripts/ci_resource_preflight.sh" \
  --purpose 'Pinned native scientific regression' --min-free-gib 64 \
  --min-physical-gib 15 --min-addressable-gib 15 --planned-swap-gib 0 \
  --standard-contract-free-gib 14 --run-id "$GITHUB_RUN_ID" \
  --run-attempt "$GITHUB_RUN_ATTEMPT" --suite "$suite" \
  --candidate-sha "$CANDIDATE_SHA" --expected-swap-file none \
  --path "$GITHUB_WORKSPACE" --path "$RUNNER_TEMP" --path /tmp
python3 "$REPO/scripts/ci_native_science.py" "$suite"
