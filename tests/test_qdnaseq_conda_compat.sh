#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
CORE_ENV="$ROOT/environments/native-core.yml"
QDNASEQ_ENV="$ROOT/environments/native-qdnaseq.yml"
HELPER="$ROOT/bin/scripts/prepare_qdnaseq_bin_data.sh"

for path in "$CORE_ENV" "$QDNASEQ_ENV" "$HELPER"; do
  [[ -s "$path" ]] || { echo "FAIL: missing file: $path" >&2; exit 1; }
done

bash -n "$HELPER"

grep -Fq 'bioconductor-qdnaseq=1.30.0' "$QDNASEQ_ENV"
grep -Fq 'bioconductor-biobase' "$QDNASEQ_ENV"
grep -Fq 'r-base=4.1' "$QDNASEQ_ENV"
grep -Fq 'qpdf' "$CORE_ENV"
grep -Fq 'pyjanitor' "$CORE_ENV"
grep -Fq 'prepare_qdnaseq_annotation' "$HELPER"
grep -Fq 'cf7c07e39de0ac64a9c38cb030cba4626e2aae83' "$HELPER"
grep -Fq '450b77a74dbba381e2f664334de90e41ec5e9eb6a5a8946d036c4b3534254d98' "$HELPER"
grep -Fq -- '--cache-dir must be empty' "$HELPER"
grep -Fq 'mv -n -- "$source" "$destination"' "$HELPER"
if grep -Fq 'mv -f' "$HELPER"; then
  echo "FAIL: qDNAseq helper must never force-overwrite cache files" >&2
  exit 1
fi

cd "$ROOT"
python3 -m unittest -v \
  tests.test_native_cli.NativeCliTests.test_prefix_probes_ignore_foreign_path_and_clean_r_environment \
  tests/test_qdnaseq_helper.py

echo "PASS: pinned qDNAseq environment, safe annotation cache, and exact native Rscript wiring"
