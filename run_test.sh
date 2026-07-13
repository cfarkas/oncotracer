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
command -v java >/dev/null || { echo "ERROR: Java 17+ is required. See docs/installation.md" >&2; exit 1; }
if ! command -v nextflow >/dev/null; then
  echo "Nextflow is missing; installing a local launcher in $ROOT_DIR/.tools"
  mkdir -p "$ROOT_DIR/.tools"
  curl -fsSL https://get.nextflow.io -o "$ROOT_DIR/.tools/nextflow"
  chmod +x "$ROOT_DIR/.tools/nextflow"
  export PATH="$ROOT_DIR/.tools:$PATH"
fi
if [[ "$RUNTIME" == "--docker" ]]; then
  command -v docker >/dev/null || { echo "ERROR: Docker is required. See docs/installation.md" >&2; exit 1; }
  echo "Pulling the current OncoTracer container (Docker reuses unchanged layers)"
  docker pull carlosfarkas/oncotracer:latest
fi

echo "[1/8] Preparing public FASTQ data and absolute-path YAML files"
nextflow run main.nf --make_test --test_root "$TEST_ROOT"
run_with_progress() {
  local label="$1" nested_log="$2"; shift 2
  local started=$SECONDS
  "$@" &
  local run_pid=$!
  while kill -0 "$run_pid" 2>/dev/null; do
    sleep 30
    kill -0 "$run_pid" 2>/dev/null || break
    echo "  $label is active ($((SECONDS - started)) seconds elapsed). If SAMURAI shows 0 of 1, its nested alignment/CNA steps are still running."
    [[ -s "$nested_log" ]] && tail -n 1 "$nested_log" || true
  done
  wait "$run_pid"
}

echo "[2/8] Validating the Illumina workflow"
nextflow run main.nf -stub-run "$RUNTIME" -params-file "$ILLUMINA_YAML"

echo "[3/8] Running the Illumina public test"
run_with_progress "Illumina workflow" "$TEST_ROOT/runs/illumina/01_samurai_illumina/.nextflow.log" nextflow run main.nf "$RUNTIME" -params-file "$ILLUMINA_YAML" -resume

echo "[4/8] Checking Illumina outputs"
test -s "$TEST_ROOT/runs/illumina/06_workflow_summary/workflow_summary.txt"
test -s "$TEST_ROOT/runs/illumina/03_cna_codification/cna_events.tsv"
test -s "$TEST_ROOT/runs/illumina/04_cna_custom_plots/cna_per_sample_pages.pdf"

echo "[5/8] Validating the ONT workflow"
nextflow run main.nf -stub-run "$RUNTIME" -params-file "$ONT_YAML"

echo "[6/8] Running the ONT public test"
run_with_progress "ONT workflow" "$TEST_ROOT/runs/ont/01_samurai_ont/.nextflow.log" nextflow run main.nf "$RUNTIME" -params-file "$ONT_YAML" -resume

echo "[7/8] Checking ONT outputs"
test -s "$TEST_ROOT/runs/ont/06_workflow_summary/workflow_summary.txt"
test -s "$TEST_ROOT/runs/ont/03_cna_codification/cna_events.tsv"
test -s "$TEST_ROOT/runs/ont/04_cna_custom_plots/cna_per_sample_pages.pdf"

echo "[8/8] All expected outputs found"
cat <<EOF
SUCCESS: both public workflows completed and produced the expected outputs.
Illumina summary: $TEST_ROOT/runs/illumina/06_workflow_summary/workflow_summary.txt
ONT summary:      $TEST_ROOT/runs/ont/06_workflow_summary/workflow_summary.txt
EOF
