#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-test}"
ROOT="$(readlink -m "$ROOT")"
ILLUMINA_DIR="$ROOT/public/illumina_DRR000542"
ONT_DIR="$ROOT/public/ont_DRR165691/fastq_pass/barcode01"

mkdir -p "$ILLUMINA_DIR" "$ONT_DIR"

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

cat <<EOF
Quickstart data ready under: $ROOT
Illumina samplesheet: $ILLUMINA_DIR/illumina.samplesheet.csv
ONT fastq_pass folder: $ROOT/public/ont_DRR165691/fastq_pass
EOF
