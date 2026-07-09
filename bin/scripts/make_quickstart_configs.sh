#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-test}"
ROOT="$(readlink -m "$ROOT")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(readlink -m "$SCRIPT_DIR/../..")"
PIPELINE="$REPO_ROOT/main.nf"
CONFIG_DIR="$ROOT/configs"
RUN_DIR="$ROOT/runs"
mkdir -p "$CONFIG_DIR" "$RUN_DIR"

nextflow run "$PIPELINE" \
  --make_config true \
  --config_mode illumina \
  --config_root "$ROOT" \
  --config_out "$CONFIG_DIR/illumina.quickstart.yml" \
  --config_outdir "$RUN_DIR/illumina" \
  --config_samplesheet "$ROOT/public/illumina_DRR000542/illumina.samplesheet.csv" \
  -resume

nextflow run "$PIPELINE" \
  --make_config true \
  --config_mode ont \
  --config_root "$ROOT" \
  --config_out "$CONFIG_DIR/ont.quickstart.yml" \
  --config_outdir "$RUN_DIR/ont" \
  --config_ont_folder "$ROOT/public/ont_DRR165691/fastq_pass" \
  --config_ont_barcodes barcode01 \
  --config_ont_sample_names DRR165691 \
  -resume

cat <<EOF_MSG
Quickstart configs written by the OncoTracer YAML agent:
  $CONFIG_DIR/illumina.quickstart.yml
  $CONFIG_DIR/ont.quickstart.yml
EOF_MSG
