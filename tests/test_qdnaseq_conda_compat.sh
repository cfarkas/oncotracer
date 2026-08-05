#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
CORE_ENV="$ROOT/environments/native-core.yml"
QDNASEQ_ENV="$ROOT/environments/native-qdnaseq.yml"
ILLUMINA="$ROOT/bin/scripts/run_illumina_samurai_fastq.sh"
ONT="$ROOT/bin/scripts/run_ont_samurai_barcodes.sh"
HELPER="$ROOT/bin/scripts/prepare_qdnaseq_bin_data.sh"

for path in "$CORE_ENV" "$QDNASEQ_ENV" "$ILLUMINA" "$ONT" "$HELPER"; do
  [[ -s "$path" ]] || { echo "FAIL: missing file: $path" >&2; exit 1; }
done

bash -n "$ILLUMINA"
bash -n "$ONT"
bash -n "$HELPER"

grep -Fq 'bioconductor-qdnaseq=1.30.0' "$QDNASEQ_ENV"
grep -Fq 'bioconductor-biobase' "$QDNASEQ_ENV"
grep -Fq 'r-base=4.1' "$QDNASEQ_ENV"
grep -Fq 'qpdf' "$CORE_ENV"
grep -Fq 'pyjanitor' "$CORE_ENV"
grep -Fq 'prepare_qdnaseq_bin_data.sh' "$ILLUMINA"
grep -Fq -- '--qdnaseq_bin_data' "$ILLUMINA"
grep -Fq 'prepare_qdnaseq_bin_data.sh' "$ONT"
grep -Fq -- '--qdnaseq_bin_data' "$ONT"
grep -Fq 'QDNASEQ_RSCRIPT="$CONDA_PREFIX/bin/Rscript"' "$ILLUMINA"
grep -Fq 'QDNASEQ_RSCRIPT="$CONDA_PREFIX/bin/Rscript"' "$ONT"
grep -Fq -- '--rscript "$QDNASEQ_RSCRIPT"' "$ILLUMINA"
grep -Fq -- '--rscript "$QDNASEQ_RSCRIPT"' "$ONT"
grep -Fq 'env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE' "$ILLUMINA"
grep -Fq 'env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE' "$ONT"
grep -Fq 'cf7c07e39de0ac64a9c38cb030cba4626e2aae83' "$HELPER"

echo "PASS: split qDNAseq Conda environment and exact Rscript wiring are present"
