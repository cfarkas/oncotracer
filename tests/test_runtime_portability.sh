#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

# Exercise the native command surface and copied-executable isolation. The
# frozen release comparator has its own immutable source and validation gate.
python3 -m unittest -v \
  tests/test_native_cli.py \
  tests/test_beginner_runtime.py \
  tests/test_install_safety.py \
  tests/test_payload_cache.py \
  tests/test_standalone_dry_run.py

echo "PASS: native runtime portability, installer isolation, and beginner CLI tests"
