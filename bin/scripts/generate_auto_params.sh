#!/usr/bin/env bash
set -Eeuo pipefail
usage() {
  cat <<'USAGE'
Usage: bash generate_auto_params.sh --mode illumina|ont --reads-folder PATH --sample-table FILE [--config-dir PATH] [--outdir PATH]
Illumina table: sample_name,status
ONT table: barcode,sample_name,status (or sample_name,status, mapped to sorted barcode folders)
USAGE
}
MODE= READS= TABLE= CONFIG_DIR= OUTDIR=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --reads-folder) READS="${2:-}"; shift 2 ;;
    --sample-table) TABLE="${2:-}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:-}"; shift 2 ;;
    --outdir) OUTDIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ "$MODE" == illumina || "$MODE" == ont ]] || { echo "ERROR: --mode must be illumina or ont" >&2; exit 2; }
[[ -d "$READS" ]] || { echo "ERROR: reads folder not found: $READS" >&2; exit 2; }
[[ -s "$TABLE" ]] || { echo "ERROR: sample table not found or empty: $TABLE" >&2; exit 2; }
READS="$(readlink -m "$READS")"; TABLE="$(readlink -m "$TABLE")"
CONFIG_DIR="$(readlink -m "${CONFIG_DIR:-$READS/oncotracer_config}")"
OUTDIR="$(readlink -m "${OUTDIR:-$READS/oncotracer_results}")"
ROOT="$READS"
for required_path in "$CONFIG_DIR" "$OUTDIR"; do
  while [[ "$required_path" != "$ROOT" && "$required_path/" != "$ROOT/"* ]]; do ROOT="$(dirname "$ROOT")"; done
done
mkdir -p "$CONFIG_DIR" "$OUTDIR"; META="$CONFIG_DIR/.auto_params_metadata.tsv"
awk 'BEGIN{FS="[,\t ]+"; OFS="\t"} {gsub(/\r/, "")} NF==0 || $1 ~ /^#/ {next} NR==1 {h=tolower($1); if(h=="sample" || h=="sample_name" || h=="barcode") next} {print $1,$2,$3}' "$TABLE" > "$META"
[[ -s "$META" ]] || { echo "ERROR: sample table contains no data rows" >&2; exit 2; }
normalize_status() { local value="${1,,}"; case "$value" in tumor|normal) printf %s "$value" ;; *) echo "ERROR: status must be TUMOR or NORMAL, found: $1" >&2; exit 2 ;; esac; }
YAML="$CONFIG_DIR/${MODE}.auto.yml"
if [[ "$MODE" == illumina ]]; then
  SHEET="$CONFIG_DIR/illumina.samplesheet.csv"; printf "sample,fastq_1,fastq_2,status\n" > "$SHEET"
  while IFS=$'\t' read -r sample status unused; do
    [[ -n "$sample" && -n "$status" ]] || { echo "ERROR: Illumina rows require sample_name,status" >&2; exit 2; }
    status="$(normalize_status "$status")"
    mapfile -t r1 < <(find "$READS" -maxdepth 1 -type f \( -name "${sample}_R1*.fastq.gz" -o -name "${sample}_R1*.fq.gz" -o -name "${sample}_1.fastq.gz" -o -name "${sample}_1.fq.gz" \) -print | sort -u)
    mapfile -t r2 < <(find "$READS" -maxdepth 1 -type f \( -name "${sample}_R2*.fastq.gz" -o -name "${sample}_R2*.fq.gz" -o -name "${sample}_2.fastq.gz" -o -name "${sample}_2.fq.gz" \) -print | sort -u)
    [[ ${#r1[@]} -eq 1 && ${#r2[@]} -eq 1 ]] || { echo "ERROR: expected exactly one R1/R2 pair for $sample in $READS; found ${#r1[@]} R1 and ${#r2[@]} R2" >&2; exit 2; }
    gzip -t "${r1[0]}" "${r2[0]}" || { echo "ERROR: corrupt or incomplete gzip FASTQ for $sample" >&2; exit 2; }
    printf "%s,%s,%s,%s\n" "$sample" "$(readlink -m "${r1[0]}")" "$(readlink -m "${r2[0]}")" "$status" >> "$SHEET"
  done < "$META"
  cat > "$YAML" <<EOF
mode: illumina
lpwgs_root: $ROOT
outdir: $OUTDIR
illumina_samplesheet: $SHEET
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
EOF
else
  mapfile -t detected_barcodes < <(for directory in "$READS"/*; do [[ -d "$directory" ]] || continue; find "$directory" -maxdepth 1 -type f \( -name "*.fastq.gz" -o -name "*.fq.gz" -o -name "*.fastq" -o -name "*.fq" \) -print -quit | grep -q . && basename "$directory"; done | sort)
  [[ ${#detected_barcodes[@]} -gt 0 ]] || { echo "ERROR: no barcode directories found below $READS" >&2; exit 2; }
  tumors=(); tumor_names=(); normals=(); normal_names=(); row=0
  while IFS=$'\t' read -r first second third; do
    if [[ -n "$third" ]]; then barcode="$first"; sample="$second"; status="$third"; else sample="$first"; status="$second"; [[ $row -lt ${#detected_barcodes[@]} ]] || { echo "ERROR: more metadata rows than barcode folders" >&2; exit 2; }; barcode="${detected_barcodes[$row]}"; fi
    [[ -d "$READS/$barcode" ]] || { echo "ERROR: barcode folder not found: $READS/$barcode" >&2; exit 2; }
    mapfile -t barcode_fastqs < <(find "$READS/$barcode" -maxdepth 1 -type f \( -name "*.fastq.gz" -o -name "*.fq.gz" -o -name "*.fastq" -o -name "*.fq" \) -print)
    [[ ${#barcode_fastqs[@]} -gt 0 ]] || { echo "ERROR: no FASTQ found in $READS/$barcode" >&2; exit 2; }
    for fastq in "${barcode_fastqs[@]}"; do
      [[ -s "$fastq" ]] || { echo "ERROR: empty FASTQ: $fastq" >&2; exit 2; }
      if [[ "$fastq" == *.gz ]]; then gzip -t "$fastq" || { echo "ERROR: corrupt or incomplete gzip FASTQ: $fastq" >&2; exit 2; }; fi
    done
    status="$(normalize_status "$status")"; if [[ "$status" == tumor ]]; then tumors+=("$barcode"); tumor_names+=("$sample"); else normals+=("$barcode"); normal_names+=("$sample"); fi; row=$((row+1))
  done < "$META"
  [[ ${#tumors[@]} -gt 0 ]] || { echo "ERROR: ONT configuration requires at least one tumor sample" >&2; exit 2; }
  join_by_comma() { local IFS=,; echo "$*"; }
  cat > "$YAML" <<EOF
mode: ont
lpwgs_root: $ROOT
outdir: $OUTDIR
ont_folder: $READS
ont_barcodes: $(join_by_comma "${tumors[@]}")
ont_sample_names: $(join_by_comma "${tumor_names[@]}")
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
run_cna_classifier: false
force: false
EOF
  if [[ ${#normals[@]} -gt 0 ]]; then
    cat >> "$YAML" <<EOF
ont_normal_folder: $READS
ont_normal_barcodes: $(join_by_comma "${normals[@]}")
ont_normal_sample_names: $(join_by_comma "${normal_names[@]}")
EOF
  fi
fi
rm -f "$META"
echo "AUTO_PARAMS SUCCESS"
echo "Generated YAML: $YAML"
[[ "$MODE" == illumina ]] && echo "Generated samplesheet: $SHEET"
echo "Inspect the generated file(s), then run:"
echo "nextflow run main.nf -stub-run --docker -params-file $YAML"
echo "nextflow run main.nf --docker -params-file $YAML -resume"
