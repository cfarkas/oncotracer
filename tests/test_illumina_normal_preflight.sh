#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

# Normal libraries remain independent samples, never a constructed panel.
# Invalid names/layouts must fail before any alignment or reference work.
python3 -m unittest -v \
  tests.test_native_engine.NativeEngineTests.test_illumina_samplesheet_validation \
  tests.test_native_engine.NativeEngineTests.test_normal_rows_are_valid_independent_illumina_samples \
  tests.test_native_engine.NativeEngineTests.test_local_sample_panel_settings_are_rejected_even_when_false \
  tests.test_native_engine.NativeEngineTests.test_native_qdnaseq_preserves_roles_without_panel_logic \
  tests.test_native_engine.NativeEngineTests.test_illumina_duplicate_ids_and_mixed_layouts_fail_preflight \
  tests.test_native_engine.NativeEngineTests.test_ont_barcode_resolution

echo "PASS: native Illumina preflight and independent normal-sample roles"
