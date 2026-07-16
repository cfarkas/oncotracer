#!/usr/bin/env bash
set -Eeuo pipefail
usage() {
  cat <<'USAGE'
Usage: bash generate_auto_params.sh --mode illumina|ont --reads-folder PATH --sample-table FILE [--config-dir PATH] [--outdir PATH] [classifier options]
Illumina table: sample_name,status. Each sample must have one exact
<sample>.fastq.gz/.fq.gz file, or one supported R1/R2 pair.
ONT table: barcode,sample_name,status (or sample_name,status, mapped to sorted barcode folders)

Classifier options:
  --run-cna-classifier true|false
  --cna-classifier-sample-set NAME
  --cna-classifier-profile NAME
  --pathology-use-biomed-models true|false
  --pathology-biomed-local-files-only true|false
USAGE
}
MODE= READS= TABLE= CONFIG_DIR= OUTDIR=
RUN_CNA_CLASSIFIER=false
CNA_CLASSIFIER_SAMPLE_SET=broad_cancer
CNA_CLASSIFIER_PROFILE=conda
PATHOLOGY_USE_BIOMED_MODELS=true
PATHOLOGY_BIOMED_LOCAL_FILES_ONLY=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --reads-folder) READS="${2:-}"; shift 2 ;;
    --sample-table) TABLE="${2:-}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:-}"; shift 2 ;;
    --outdir) OUTDIR="${2:-}"; shift 2 ;;
    --run-cna-classifier) RUN_CNA_CLASSIFIER="${2:-}"; shift 2 ;;
    --cna-classifier-sample-set) CNA_CLASSIFIER_SAMPLE_SET="${2:-}"; shift 2 ;;
    --cna-classifier-profile) CNA_CLASSIFIER_PROFILE="${2:-}"; shift 2 ;;
    --pathology-use-biomed-models) PATHOLOGY_USE_BIOMED_MODELS="${2:-}"; shift 2 ;;
    --pathology-biomed-local-files-only) PATHOLOGY_BIOMED_LOCAL_FILES_ONLY="${2:-}"; shift 2 ;;
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
path_is_within_root() {
  local path="$1" root="$2"
  [[ "$path" == "$root" ]] ||
    [[ "$root" == "/" && "$path" == /* ]] ||
    [[ "$path" == "$root/"* ]]
}
for required_path in "$CONFIG_DIR" "$OUTDIR"; do
  while ! path_is_within_root "$required_path" "$ROOT"; do
    parent="$(dirname "$ROOT")"
    [[ "$parent" != "$ROOT" ]] || {
      echo "ERROR: could not determine a common root for automatic setup paths" >&2
      exit 2
    }
    ROOT="$parent"
  done
done
[[ "$ROOT" != "/" ]] || {
  echo "ERROR: reads, configuration, and output paths must share a project directory below /" >&2
  exit 2
}
mkdir -p "$CONFIG_DIR" "$OUTDIR"; META="$CONFIG_DIR/.auto_params_metadata.tsv"
awk 'BEGIN{FS="[,\t ]+"; OFS="\t"} {gsub(/\r/, "")} NF==0 || $1 ~ /^#/ {next} NR==1 {h=tolower($1); if(h=="sample" || h=="sample_name" || h=="barcode") next} {print $1,$2,$3}' "$TABLE" > "$META"
[[ -s "$META" ]] || { echo "ERROR: sample table contains no data rows" >&2; exit 2; }
normalize_status() { local value="${1,,}"; case "$value" in tumor|normal) printf %s "$value" ;; *) echo "ERROR: status must be TUMOR or NORMAL, found: $1" >&2; exit 2 ;; esac; }
normalize_bool() { local value="${1,,}"; case "$value" in true|t|1|yes|y|on) printf true ;; false|f|0|no|n|off) printf false ;; *) echo "ERROR: expected true or false, found: $1" >&2; exit 2 ;; esac; }
RUN_CNA_CLASSIFIER="$(normalize_bool "$RUN_CNA_CLASSIFIER")"
PATHOLOGY_USE_BIOMED_MODELS="$(normalize_bool "$PATHOLOGY_USE_BIOMED_MODELS")"
PATHOLOGY_BIOMED_LOCAL_FILES_ONLY="$(normalize_bool "$PATHOLOGY_BIOMED_LOCAL_FILES_ONLY")"
[[ -n "$CNA_CLASSIFIER_SAMPLE_SET" ]] || { echo "ERROR: classifier sample set cannot be empty" >&2; exit 2; }
[[ -n "$CNA_CLASSIFIER_PROFILE" ]] || { echo "ERROR: classifier profile cannot be empty" >&2; exit 2; }
YAML="$CONFIG_DIR/${MODE}.auto.yml"
if [[ "$MODE" == illumina ]]; then
  SHEET="$CONFIG_DIR/illumina.samplesheet.csv"; printf "sample,fastq_1,fastq_2,status\n" > "$SHEET"
  detected_layout=""
  while IFS=$'\t' read -r sample status unused; do
    [[ -n "$sample" && -n "$status" ]] || { echo "ERROR: Illumina rows require sample_name,status" >&2; exit 2; }
    status="$(normalize_status "$status")"
    mapfile -t r1 < <(find "$READS" -maxdepth 1 -type f \( -name "${sample}_R1*.fastq.gz" -o -name "${sample}_R1*.fq.gz" -o -name "${sample}_1.fastq.gz" -o -name "${sample}_1.fq.gz" \) -print | sort -u)
    mapfile -t r2 < <(find "$READS" -maxdepth 1 -type f \( -name "${sample}_R2*.fastq.gz" -o -name "${sample}_R2*.fq.gz" -o -name "${sample}_2.fastq.gz" -o -name "${sample}_2.fq.gz" \) -print | sort -u)
    mapfile -t single < <(find "$READS" -maxdepth 1 -type f \( -name "${sample}.fastq.gz" -o -name "${sample}.fq.gz" \) -print | sort -u)
    if [[ ${#r1[@]} -eq 1 && ${#r2[@]} -eq 1 && ${#single[@]} -eq 0 ]]; then
      sample_layout="paired"
      fastq_1="${r1[0]}"
      fastq_2="${r2[0]}"
      gzip -t "$fastq_1" "$fastq_2" || { echo "ERROR: corrupt or incomplete gzip FASTQ for $sample" >&2; exit 2; }
    elif [[ ${#r1[@]} -eq 0 && ${#r2[@]} -eq 0 && ${#single[@]} -eq 1 ]]; then
      sample_layout="single"
      fastq_1="${single[0]}"
      fastq_2=""
      gzip -t "$fastq_1" || { echo "ERROR: corrupt or incomplete gzip FASTQ for $sample" >&2; exit 2; }
    else
      echo "ERROR: expected either one exact single-end file or one R1/R2 pair for $sample in $READS; found ${#single[@]} single, ${#r1[@]} R1, and ${#r2[@]} R2" >&2
      exit 2
    fi
    if [[ -n "$detected_layout" && "$detected_layout" != "$sample_layout" ]]; then
      echo "ERROR: a single automatic Illumina setup cannot mix single-end and paired-end libraries" >&2
      exit 2
    fi
    detected_layout="$sample_layout"
    fastq_1="$(readlink -m "$fastq_1")"
    [[ -z "$fastq_2" ]] || fastq_2="$(readlink -m "$fastq_2")"
    printf "%s,%s,%s,%s\n" "$sample" "$fastq_1" "$fastq_2" "$status" >> "$SHEET"
  done < "$META"
  echo "Detected Illumina layout: ${detected_layout}-end"
  cat > "$YAML" <<EOF
mode: illumina
lpwgs_root: $ROOT
outdir: $OUTDIR
illumina_samplesheet: $SHEET
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
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
EOF
  if [[ ${#normals[@]} -gt 0 ]]; then
    cat >> "$YAML" <<EOF
ont_normal_folder: $READS
ont_normal_barcodes: $(join_by_comma "${normals[@]}")
ont_normal_sample_names: $(join_by_comma "${normal_names[@]}")
EOF
  fi
fi
cat >> "$YAML" <<EOF
run_cna_classifier: $RUN_CNA_CLASSIFIER
cna_classifier_sample_set: $CNA_CLASSIFIER_SAMPLE_SET
cna_classifier_profile: $CNA_CLASSIFIER_PROFILE
pathology_csv: null
pathology_use_biomed_models: $PATHOLOGY_USE_BIOMED_MODELS
pathology_biomed_local_files_only: $PATHOLOGY_BIOMED_LOCAL_FILES_ONLY
force: false
EOF
rm -f "$META"
echo "AUTO_PARAMS SUCCESS"
echo "Generated YAML: $YAML"
[[ "$MODE" == illumina ]] && echo "Generated samplesheet: $SHEET"
echo "Inspect the generated file(s), then run:"
echo "nextflow run main.nf -stub-run --docker -params-file $YAML"
echo "nextflow run main.nf --docker -params-file $YAML -resume"
