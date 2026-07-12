#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "Usage: bash run_test.sh [--docker|--singularity|--conda]"
}

RUNTIME="${1:---docker}"
case "$RUNTIME" in
  --docker|--singularity|--conda) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="${TEST_ROOT:-$ROOT_DIR/test}"
ILLUMINA_YAML="$TEST_ROOT/configs/illumina.quickstart.yml"
ONT_YAML="$TEST_ROOT/configs/ont.quickstart.yml"

cd "$ROOT_DIR"
command -v nextflow >/dev/null || { echo "ERROR: nextflow is not installed" >&2; exit 1; }
if [[ "$RUNTIME" == "--docker" ]]; then
  command -v docker >/dev/null || { echo "ERROR: docker is not installed" >&2; exit 1; }
fi

echo "[1/7] Preparing public FASTQ data and absolute-path YAML files"
nextflow run main.nf --make_test --test_root "$TEST_ROOT"

echo "[2/7] Validating the Illumina workflow"
nextflow run main.nf -stub-run "$RUNTIME" -params-file "$ILLUMINA_YAML"

echo "[3/7] Running the Illumina public test"
nextflow run main.nf "$RUNTIME" -params-file "$ILLUMINA_YAML" -resume

echo "[4/7] Checking Illumina outputs"
test -s "$TEST_ROOT/runs/illumina/06_workflow_summary/workflow_summary.txt"
test -s "$TEST_ROOT/runs/illumina/03_cna_codification/cna_events.tsv"
test -s "$TEST_ROOT/runs/illumina/04_cna_custom_plots/cna_per_sample_pages.pdf"

echo "[5/7] Validating the ONT workflow"
nextflow run main.nf -stub-run "$RUNTIME" -params-file "$ONT_YAML"

echo "[6/7] Running the ONT public test"
nextflow run main.nf "$RUNTIME" -params-file "$ONT_YAML" -resume

echo "[7/7] Checking ONT outputs"
test -s "$TEST_ROOT/runs/ont/06_workflow_summary/workflow_summary.txt"
test -s "$TEST_ROOT/runs/ont/03_cna_codification/cna_events.tsv"
test -s "$TEST_ROOT/runs/ont/04_cna_custom_plots/cna_per_sample_pages.pdf"

cat <<EOF
SUCCESS: both public workflows completed and produced the expected outputs.
Illumina summary: $TEST_ROOT/runs/illumina/06_workflow_summary/workflow_summary.txt
ONT summary:      $TEST_ROOT/runs/ont/06_workflow_summary/workflow_summary.txt
EOF
