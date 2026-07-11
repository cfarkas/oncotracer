#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-test}"
ROOT="$(readlink -m "$ROOT")"
ILLUMINA_DIR="$ROOT/public/illumina_DRR000542"
ONT_DIR="$ROOT/public/ont_DRR165691/fastq_pass/barcode01"
CONFIG_DIR="$ROOT/configs"

mkdir -p "$ILLUMINA_DIR" "$ONT_DIR" "$CONFIG_DIR" "$ROOT/runs"

download_if_missing() {
  local url="$1" out="$2"
  if [[ -s "$out" ]] && gzip -t "$out" >/dev/null 2>&1; then
    echo "Reusing validated FASTQ: $out"
  else
    if [[ -s "$out" ]]; then
      echo "Resuming incomplete FASTQ: $out"
    else
      echo "Downloading $url"
    fi
    curl -L -C - --retry 8 --retry-delay 10 --retry-all-errors -o "$out" "$url"
    gzip -t "$out"
  fi
}

download_if_missing \
  "https://ftp.sra.ebi.ac.uk/vol1/fastq/DRR000/DRR000542/DRR000542_1.fastq.gz" \
  "$ILLUMINA_DIR/DRR000542_1.fastq.gz"
download_if_missing \
  "https://ftp.sra.ebi.ac.uk/vol1/fastq/DRR000/DRR000542/DRR000542_2.fastq.gz" \
  "$ILLUMINA_DIR/DRR000542_2.fastq.gz"

printf 'sample,fastq_1,fastq_2,status\nDRR000542,%s,%s,tumor\n' \
  "$ILLUMINA_DIR/DRR000542_1.fastq.gz" \
  "$ILLUMINA_DIR/DRR000542_2.fastq.gz" \
  > "$ILLUMINA_DIR/illumina.samplesheet.csv"

download_if_missing \
  "https://ftp.sra.ebi.ac.uk/vol1/fastq/DRR165/DRR165691/DRR165691_1.fastq.gz" \
  "$ONT_DIR/DRR165691_1.fastq.gz"

cat > "$CONFIG_DIR/illumina.quickstart.yml" <<EOF
# Illumina public test: DRR000542 paired-end FASTQ.
mode: illumina
lpwgs_root: $ROOT
outdir: $ROOT/runs/illumina
illumina_samplesheet: $ILLUMINA_DIR/illumina.samplesheet.csv
illumina_samurai_outdir: $ROOT/runs/illumina/01_samurai_illumina
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: true
EOF

cat > "$CONFIG_DIR/ont.quickstart.yml" <<EOF
# ONT public test: DRR165691 FASTQ assigned to barcode01.
mode: ont
lpwgs_root: $ROOT
outdir: $ROOT/runs/ont
ont_folder: $ROOT/public/ont_DRR165691/fastq_pass
ont_barcodes: barcode01
ont_sample_names: DRR165691
ont_samurai_outdir: $ROOT/runs/ont/01_samurai_ont
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
run_cna_classifier: false
force: true
EOF

cat <<EOF
Quickstart data ready under: $ROOT
Illumina samplesheet: $ILLUMINA_DIR/illumina.samplesheet.csv
ONT fastq_pass folder: $ROOT/public/ont_DRR165691/fastq_pass
Illumina YAML: $CONFIG_DIR/illumina.quickstart.yml
ONT YAML: $CONFIG_DIR/ont.quickstart.yml
EOF
