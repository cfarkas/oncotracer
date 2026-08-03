#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
ENV="$ROOT/environment.yml"
ILLUMINA="$ROOT/bin/scripts/run_illumina_samurai_fastq.sh"
ONT="$ROOT/bin/scripts/run_ont_samurai_barcodes.sh"
HELPER="$ROOT/bin/scripts/prepare_qdnaseq_bin_data.sh"

for path in "$ENV" "$ILLUMINA" "$ONT" "$HELPER"; do
  [[ -s "$path" ]] || { echo "FAIL: missing file: $path" >&2; exit 1; }
done

bash -n "$ILLUMINA"
bash -n "$ONT"
bash -n "$HELPER"

grep -Fq 'bioconductor-qdnaseq=1.30.0' "$ENV"
grep -Fq 'r-base=4.1' "$ENV"
grep -Fq 'qpdf' "$ENV"
grep -Fq 'pyjanitor' "$ENV"
grep -Fq 'prepare_qdnaseq_bin_data.sh' "$ILLUMINA"
grep -Fq -- '--qdnaseq_bin_data' "$ILLUMINA"
grep -Fq 'prepare_qdnaseq_bin_data.sh' "$ONT"
grep -Fq -- '--qdnaseq_bin_data' "$ONT"
grep -Fq 'cf7c07e39de0ac64a9c38cb030cba4626e2aae83' "$HELPER"

echo "PASS: qDNAseq Conda compatibility wiring is present"
