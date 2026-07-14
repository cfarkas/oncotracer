#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-test}"
ROOT="$(readlink -m "$ROOT")"
ILLUMINA_DIR="$ROOT/public/illumina_ERR12341627"
ONT_DIR="$ROOT/public/ont_DRR165691/fastq_pass/barcode01"
CONFIG_DIR="$ROOT/configs"

mkdir -p "$ILLUMINA_DIR" "$ONT_DIR" "$CONFIG_DIR" "$ROOT/runs"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/download_helpers.sh"

download_validated_fastq \
  "https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_1.fastq.gz" \
  "$ILLUMINA_DIR/ERR12341627_1.fastq.gz" \
  "105996523" \
  "4c96d551152694b3893ea98b7781a3ae"
download_validated_fastq \
  "https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_2.fastq.gz" \
  "$ILLUMINA_DIR/ERR12341627_2.fastq.gz" \
  "23748473" \
  "1b20d9eb98f755244f6383ea1354bd40"

printf 'sample,fastq_1,fastq_2,status\nERR12341627,%s,%s,tumor\n' \
  "$ILLUMINA_DIR/ERR12341627_1.fastq.gz" \
  "$ILLUMINA_DIR/ERR12341627_2.fastq.gz" \
  > "$ILLUMINA_DIR/illumina.samplesheet.csv"

download_validated_fastq \
  "https://ftp.sra.ebi.ac.uk/vol1/fastq/DRR165/DRR165691/DRR165691_1.fastq.gz" \
  "$ONT_DIR/DRR165691_1.fastq.gz" \
  "101734666" \
  "55a3984cb0334aa4cb0a38255cb71c06"

cat > "$CONFIG_DIR/illumina.quickstart.yml" <<EOF_YAML
# Illumina public test: ERR12341627 paired-end FASTQ.
mode: illumina
lpwgs_root: $ROOT
outdir: $ROOT/runs/illumina
illumina_samplesheet: $ILLUMINA_DIR/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: true
EOF_YAML

cat > "$CONFIG_DIR/ont.quickstart.yml" <<EOF_YAML
# ONT public test: DRR165691 FASTQ assigned to barcode01.
mode: ont
lpwgs_root: $ROOT
outdir: $ROOT/runs/ont
ont_folder: $ROOT/public/ont_DRR165691/fastq_pass
ont_barcodes: barcode01
ont_sample_names: DRR165691
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
run_cna_classifier: false
force: true
EOF_YAML

cat <<EOF_MESSAGE
Quickstart data ready under: $ROOT
Illumina samplesheet: $ILLUMINA_DIR/illumina.samplesheet.csv
ONT fastq_pass folder: $ROOT/public/ont_DRR165691/fastq_pass
Illumina YAML: $CONFIG_DIR/illumina.quickstart.yml
ONT YAML: $CONFIG_DIR/ont.quickstart.yml
EOF_MESSAGE
