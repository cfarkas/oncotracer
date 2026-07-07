#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

###############################################################################
# ONT methylation pipeline from pod5_pass
#
# Supports:
#   1. Barcoded mode:
#        --barcodes barcode04,barcode05
#        Uses fastq_pass/<barcode> to extract read IDs, then Dorado --read-ids.
#
#   2. Native/no-barcode mode:
#        --no_barcode
#        Does NOT require fastq_pass or barcodes.
#        Basecalls all reads in pod5_pass and treats them as one sample.
#
# Fixed output behavior:
#   - --input_dir is the sequencing run/input folder.
#   - --outdir is the exact output directory.
#   - relative --outdir paths are resolved under LPWGS_ROOT.
###############################################################################

LPWGS_ROOT="/media/server/STORAGE/LPWGS_2025"

INPUT_DIR=""
BARCODES_CSV=""
SAMPLE_NAMES_CSV=""
OUTDIR=""

REF=""
DORADO_MODELS_DIR=""

DORADO_MODEL="hac"
MODBASES_CSV="5mCG_5hmCG"
DORADO_DEVICE=""

MM2_OPTS="-x map-ont"
THREADS=32
PIGZ_THREADS=8
MIN_COVERAGE=5

NO_BARCODE=false
FORCE_BASECALL=false

usage() {
  cat <<'EOF'
Usage, barcoded mode:
  run_ont_methylation_pod5_barcodes.sh \
    --input_dir PATH \
    --barcodes barcode04,barcode05 \
    --sample-names D4_Glioma,E5_LeucemiaM7_newborn \
    --outdir ./ONT_analyses/D4_E5_methylation \
    [options]

Usage, native/no-barcode mode:
  run_ont_methylation_pod5_barcodes.sh \
    --input_dir PATH \
    --sample-names 7783_FFPE \
    --outdir ./ONT_analyses/7783_FFPE_methylation \
    --no_barcode \
    [options]

Required:
  --input_dir PATH
      Parent folder containing pod5_pass.
      In barcoded mode, it must also contain fastq_pass.
      Alias accepted:
        --folder PATH

  --outdir PATH
      Exact output directory.
      If it does not exist, it will be created.
      Relative paths are resolved under:
        /media/server/STORAGE/LPWGS_2025

Barcoded mode:
  --barcodes LIST
      Comma-separated barcode names, e.g. barcode04,barcode05.
      Required unless --no_barcode is used.

No-barcode mode:
  --no_barcode
      Native sequencing mode. Basecall all reads from pod5_pass.
      Does not use fastq_pass or read-ID filtering.

Optional:
  --sample-names LIST
      Barcoded mode: comma-separated names matching --barcodes.
      No-barcode mode: one sample name. If omitted, input folder basename is used.

  --lpwgs-root PATH
      Default:
        /media/server/STORAGE/LPWGS_2025

  --ref FASTA
      Default:
        <lpwgs-root>/references/samurai_hg38/genome.fa

  --dorado-model fast|hac|sup|MODEL_PATH
      Default: hac.

  --modbases LIST
      Default: 5mCG_5hmCG.
      Examples:
        5mCG_5hmCG
        5mCG_5hmCG,6mA

  --dorado-device DEVICE
      Examples:
        cuda:all
        cuda:0
        cpu

  --dorado-models-dir PATH
      Default:
        <lpwgs-root>/tools/dorado_models

  --threads N
      Default: 32.

  --pigz-threads N
      Default: 8.

  --min-coverage N
      Default: 5.

  --force-basecall
      Delete and recreate all-read modBAM.

Removed:
  --run-label
  --timestamp-suffix

      Use --outdir instead.

  -h, --help
      Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input_dir|--input-dir|--folder)
      INPUT_DIR="$2"
      shift 2
      ;;
    --barcodes)
      BARCODES_CSV="$2"
      shift 2
      ;;
    --sample-names|--samples)
      SAMPLE_NAMES_CSV="$2"
      shift 2
      ;;
    --outdir|--out-dir)
      OUTDIR="$2"
      shift 2
      ;;
    --no_barcode|--no-barcode)
      NO_BARCODE=true
      shift
      ;;
    --lpwgs-root)
      LPWGS_ROOT="$2"
      shift 2
      ;;
    --ref)
      REF="$2"
      shift 2
      ;;
    --dorado-model)
      DORADO_MODEL="$2"
      shift 2
      ;;
    --modbases)
      MODBASES_CSV="$2"
      shift 2
      ;;
    --dorado-device)
      DORADO_DEVICE="$2"
      shift 2
      ;;
    --dorado-models-dir)
      DORADO_MODELS_DIR="$2"
      shift 2
      ;;
    --threads)
      THREADS="$2"
      shift 2
      ;;
    --pigz-threads)
      PIGZ_THREADS="$2"
      shift 2
      ;;
    --min-coverage)
      MIN_COVERAGE="$2"
      shift 2
      ;;
    --force-basecall)
      FORCE_BASECALL=true
      shift
      ;;
    --run-label|--timestamp-suffix)
      echo "ERROR: $1 was removed from this script." >&2
      echo "Use --outdir ./ONT_analyses/<analysis_name> instead." >&2
      exit 1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

[[ -n "$INPUT_DIR" ]] || { echo "ERROR: --input_dir is required" >&2; usage >&2; exit 1; }
[[ -n "$OUTDIR" ]] || { echo "ERROR: --outdir is required" >&2; usage >&2; exit 1; }

if [[ "$NO_BARCODE" != "true" && -z "$BARCODES_CSV" ]]; then
  echo "ERROR: --barcodes is required unless --no_barcode is used." >&2
  usage >&2
  exit 1
fi

LPWGS_ROOT="$(readlink -m "$LPWGS_ROOT")"
cd "$LPWGS_ROOT"

if [[ -z "$REF" ]]; then
  REF="$LPWGS_ROOT/references/samurai_hg38/genome.fa"
fi

if [[ -z "$DORADO_MODELS_DIR" ]]; then
  DORADO_MODELS_DIR="$LPWGS_ROOT/tools/dorado_models"
fi

source /home/server/anaconda3/etc/profile.d/conda.sh
conda activate base

command -v dorado >/dev/null 2>&1 || { echo "ERROR: dorado not found in PATH" >&2; exit 1; }
command -v modkit >/dev/null 2>&1 || { echo "ERROR: modkit not found in PATH" >&2; exit 1; }
command -v samtools >/dev/null 2>&1 || { echo "ERROR: samtools not found in PATH" >&2; exit 1; }
command -v pigz >/dev/null 2>&1 || { echo "ERROR: pigz not found in PATH" >&2; exit 1; }
command -v tabix >/dev/null 2>&1 || { echo "ERROR: tabix not found in PATH" >&2; exit 1; }

mkdir -p "$DORADO_MODELS_DIR"

sanitize_id() {
  local s="$1"
  s="${s// /_}"
  printf '%s' "$s" | sed 's/[^A-Za-z0-9_.-]/_/g'
}

resolve_path_under_lpwgs_root() {
  local p="$1"

  if [[ "$p" = /* ]]; then
    readlink -m "$p"
  else
    readlink -m "$LPWGS_ROOT/$p"
  fi
}

csv_to_array() {
  local csv="$1"
  local -n arr_ref="$2"

  csv="${csv//;/,}"
  csv="${csv// /}"

  if [[ -z "$csv" ]]; then
    arr_ref=()
  else
    IFS=',' read -r -a arr_ref <<< "$csv"
  fi
}

resolve_named_folder() {
  local base="$1"
  local folder_name="$2"

  if [[ "$(basename "$base")" == "$folder_name" ]]; then
    echo "$base"
    return 0
  fi

  if [[ -d "$base/$folder_name" ]]; then
    echo "$base/$folder_name"
    return 0
  fi

  mapfile -t hits < <(find "$base" -maxdepth 10 -type d -name "$folder_name" | sort)

  if (( ${#hits[@]} == 1 )); then
    echo "${hits[0]}"
    return 0
  fi

  if (( ${#hits[@]} == 0 )); then
    echo "ERROR: no $folder_name folder found under: $base" >&2
  else
    echo "ERROR: multiple $folder_name folders found under: $base" >&2
    printf '  %s\n' "${hits[@]}" >&2
    echo "Please pass the exact run folder containing the intended $folder_name." >&2
  fi

  return 1
}

download_samurai_hg38_reference_if_needed() {
  local ref="$1"

  local ref_dir
  ref_dir="$(dirname "$ref")"

  local fasta="$ref_dir/genome.fa"
  local fai="$ref_dir/genome.fa.fai"
  local dict="$ref_dir/genome.dict"

  local s3_base="s3://ngi-igenomes/igenomes/Homo_sapiens/UCSC/hg38/Sequence/WholeGenomeFasta"
  local https_base="https://ngi-igenomes.s3.amazonaws.com/igenomes/Homo_sapiens/UCSC/hg38/Sequence/WholeGenomeFasta"

  if [[ "$ref" != "$fasta" ]]; then
    return 0
  fi

  mkdir -p "$ref_dir"

  if [[ -s "$fasta" && -s "$fai" && -s "$dict" ]]; then
    return 0
  fi

  echo "Downloading SAMURAI/iGenomes UCSC hg38 reference to:"
  echo "  $ref_dir"

  if command -v aws >/dev/null 2>&1; then
    [[ -s "$fasta" ]] || aws s3 cp --no-sign-request "$s3_base/genome.fa" "$fasta"
    [[ -s "$fai" ]]   || aws s3 cp --no-sign-request "$s3_base/genome.fa.fai" "$fai"
    [[ -s "$dict" ]]  || aws s3 cp --no-sign-request "$s3_base/genome.dict" "$dict"
  else
    command -v wget >/dev/null 2>&1 || { echo "ERROR: wget not found and aws CLI not available" >&2; exit 1; }

    [[ -s "$fasta" ]] || wget -O "$fasta" "$https_base/genome.fa"
    [[ -s "$fai" ]]   || wget -O "$fai"   "$https_base/genome.fa.fai"
    [[ -s "$dict" ]]  || wget -O "$dict"  "$https_base/genome.dict"
  fi
}

extract_read_ids_from_barcode_fastq() {
  local barcode_dir="$1"
  local out_ids="$2"

  : > "$out_ids"

  if [[ ! -d "$barcode_dir" ]]; then
    echo "ERROR: barcode directory does not exist: $barcode_dir" >&2
    return 1
  fi

  find "$barcode_dir" -maxdepth 1 -type f \( -name '*.fastq.gz' -o -name '*.fq.gz' \) -print0 \
    | sort -z -V \
    | xargs -0 -r pigz -dc -p "$PIGZ_THREADS" -- \
    | awk 'NR % 4 == 1 {gsub(/^@/, "", $1); print $1}' \
    | sort -u > "$out_ids"

  if [[ ! -s "$out_ids" ]]; then
    echo "ERROR: no read IDs extracted from $barcode_dir" >&2
    return 1
  fi
}

check_mod_tags() {
  local bam="$1"
  local status

  set +o pipefail
  samtools view "$bam" | head -1000 | grep -Eq 'MM:Z|ML:B:C'
  status=$?
  set -o pipefail

  return "$status"
}

link_or_copy_file() {
  local src="$1"
  local dest="$2"

  rm -f "$dest"

  if ln "$src" "$dest" 2>/dev/null; then
    return 0
  fi

  if ln -s "$src" "$dest" 2>/dev/null; then
    return 0
  fi

  cp -f "$src" "$dest"
}

###############################################################################
# Resolve inputs / output
###############################################################################

INPUT_DIR="$(resolve_path_under_lpwgs_root "$INPUT_DIR")"
[[ -d "$INPUT_DIR" ]] || { echo "ERROR: --input_dir is not a directory: $INPUT_DIR" >&2; exit 1; }

POD5_PASS_ROOT="$(resolve_named_folder "$INPUT_DIR" "pod5_pass")"

if [[ "$NO_BARCODE" == "true" ]]; then
  FASTQ_PASS_ROOT="not_used_no_barcode"
else
  FASTQ_PASS_ROOT="$(resolve_named_folder "$INPUT_DIR" "fastq_pass")"
fi

REF="$(resolve_path_under_lpwgs_root "$REF")"
download_samurai_hg38_reference_if_needed "$REF"

[[ -s "$REF" ]] || { echo "ERROR: missing reference FASTA: $REF" >&2; exit 1; }
[[ -s "$REF.fai" ]] || samtools faidx "$REF"

DORADO_MODELS_DIR="$(resolve_path_under_lpwgs_root "$DORADO_MODELS_DIR")"
mkdir -p "$DORADO_MODELS_DIR"

RUN_ROOT="$(resolve_path_under_lpwgs_root "$OUTDIR")"

if [[ -e "$RUN_ROOT" && ! -d "$RUN_ROOT" ]]; then
  echo "ERROR: --outdir exists but is not a directory:" >&2
  echo "  $RUN_ROOT" >&2
  exit 1
fi

mkdir -p "$RUN_ROOT"/{read_ids,modbam,bedmethyl,bigwig,logs,summary,tmp}

declare -a BARCODES
declare -a SAMPLE_NAMES
declare -a MODBASES

csv_to_array "$MODBASES_CSV" MODBASES

if (( ${#MODBASES[@]} == 0 )); then
  echo "ERROR: no modified-base model parsed from --modbases" >&2
  exit 1
fi

if [[ "$NO_BARCODE" == "true" ]]; then
  BARCODES=("no_barcode")

  if [[ -n "$SAMPLE_NAMES_CSV" ]]; then
    csv_to_array "$SAMPLE_NAMES_CSV" SAMPLE_NAMES
    if (( ${#SAMPLE_NAMES[@]} != 1 )); then
      echo "ERROR: --no_barcode requires exactly one --sample-names entry." >&2
      echo "Sample names parsed: ${#SAMPLE_NAMES[@]}" >&2
      exit 1
    fi
  else
    SAMPLE_NAMES=("$(basename "$INPUT_DIR")")
  fi
else
  csv_to_array "$BARCODES_CSV" BARCODES

  if (( ${#BARCODES[@]} == 0 )); then
    echo "ERROR: no barcodes parsed from --barcodes" >&2
    exit 1
  fi

  if [[ -n "$SAMPLE_NAMES_CSV" ]]; then
    csv_to_array "$SAMPLE_NAMES_CSV" SAMPLE_NAMES

    if (( ${#BARCODES[@]} != ${#SAMPLE_NAMES[@]} )); then
      echo "ERROR: --barcodes and --sample-names counts differ" >&2
      echo "Barcodes: ${#BARCODES[@]}" >&2
      echo "Names:    ${#SAMPLE_NAMES[@]}" >&2
      exit 1
    fi
  else
    SAMPLE_NAMES=("${BARCODES[@]}")
  fi
fi

for i in "${!BARCODES[@]}"; do
  BARCODES[$i]="$(sanitize_id "${BARCODES[$i]}")"
  SAMPLE_NAMES[$i]="$(sanitize_id "${SAMPLE_NAMES[$i]}")"
done

if [[ "$NO_BARCODE" == "true" ]]; then
  ALL_IDS="$RUN_ROOT/read_ids/all_reads_no_barcode.no_read_id_filter.txt"
  ALL_MOD_UNSORTED="$RUN_ROOT/modbam/all_reads_no_barcode.mod.unfiltered.bam"
  ALL_MOD_SORTED="$RUN_ROOT/modbam/all_reads_no_barcode.mod.sorted.bam"
else
  ALL_IDS="$RUN_ROOT/read_ids/all_selected_read_ids.txt"
  ALL_MOD_UNSORTED="$RUN_ROOT/modbam/all_selected_reads.mod.unfiltered.bam"
  ALL_MOD_SORTED="$RUN_ROOT/modbam/all_selected_reads.mod.sorted.bam"
fi

echo
echo "============================================================================"
echo "ONT methylation from pod5_pass"
echo "============================================================================"
echo "Input dir         : $INPUT_DIR"
echo "pod5_pass         : $POD5_PASS_ROOT"
echo "fastq_pass        : $FASTQ_PASS_ROOT"
echo "No barcode mode   : $NO_BARCODE"
echo "Barcodes          : ${BARCODES[*]}"
echo "Sample names      : ${SAMPLE_NAMES[*]}"
echo "Reference         : $REF"
echo "Dorado model      : $DORADO_MODEL"
echo "Modified bases    : ${MODBASES[*]}"
echo "Dorado device     : ${DORADO_DEVICE:-default}"
echo "Minimap2 opts     : $MM2_OPTS"
echo "Threads           : $THREADS"
echo "Output dir        : $RUN_ROOT"
echo "============================================================================"
echo

###############################################################################
# Build read ID list only in barcoded mode
###############################################################################

if [[ "$NO_BARCODE" == "true" ]]; then
  echo "No-barcode mode: skipping fastq_pass read-ID extraction."
  echo "Dorado will basecall all reads in pod5_pass."
  : > "$ALL_IDS"
else
  : > "$ALL_IDS"

  for idx in "${!BARCODES[@]}"; do
    barcode="${BARCODES[$idx]}"
    sample="${SAMPLE_NAMES[$idx]}"

    barcode_dir="$FASTQ_PASS_ROOT/$barcode"
    ids="$RUN_ROOT/read_ids/${sample}.${barcode}.read_ids.txt"

    echo "Extracting read IDs for $sample / $barcode:"
    echo "  $barcode_dir"

    extract_read_ids_from_barcode_fastq "$barcode_dir" "$ids"

    echo "  $(wc -l < "$ids") reads"

    cat "$ids" >> "$ALL_IDS"
  done

  sort -u "$ALL_IDS" -o "$ALL_IDS"

  echo
  echo "Total selected read IDs:"
  wc -l "$ALL_IDS"
  echo
fi

###############################################################################
# Dorado basecalling
###############################################################################

if [[ "$FORCE_BASECALL" == "true" ]]; then
  rm -f "$ALL_MOD_UNSORTED" "$ALL_MOD_SORTED" "$ALL_MOD_SORTED.bai" "$ALL_MOD_SORTED.csi"
fi

if [[ ! -s "$ALL_MOD_SORTED" ]]; then
  if [[ "$NO_BARCODE" == "true" ]]; then
    echo "Running Dorado basecaller on all pod5_pass reads."
  else
    echo "Running Dorado basecaller on selected barcode read IDs."
  fi

  DORADO_CMD=(
    dorado basecaller
    "$DORADO_MODEL"
    "$POD5_PASS_ROOT"
    --recursive
    --models-directory "$DORADO_MODELS_DIR"
    --modified-bases "${MODBASES[@]}"
    --reference "$REF"
    --mm2-opts "$MM2_OPTS"
  )

  if [[ "$NO_BARCODE" != "true" ]]; then
    DORADO_CMD+=(--read-ids "$ALL_IDS")
  fi

  if [[ -n "$DORADO_DEVICE" ]]; then
    DORADO_CMD+=(--device "$DORADO_DEVICE")
  fi

  echo "Command:"
  printf ' %q' "${DORADO_CMD[@]}"
  echo
  echo

  "${DORADO_CMD[@]}" > "$ALL_MOD_UNSORTED" 2> "$RUN_ROOT/logs/dorado_basecaller.stderr.log"

  echo "Sorting all-read modBAM."
  samtools sort -@ "$THREADS" -o "$ALL_MOD_SORTED" "$ALL_MOD_UNSORTED"
  samtools index -@ "$THREADS" "$ALL_MOD_SORTED"

  rm -f "$ALL_MOD_UNSORTED"
else
  echo "Reusing existing sorted all-read modBAM:"
  echo "  $ALL_MOD_SORTED"
fi

echo
echo "Checking modified-base tags in all-read modBAM."
if ! check_mod_tags "$ALL_MOD_SORTED"; then
  echo "ERROR: no MM/ML modified-base tags detected in:" >&2
  echo "  $ALL_MOD_SORTED" >&2
  echo "Check Dorado model/modbase selection and pod5 input." >&2
  exit 1
fi

###############################################################################
# Per-sample BAM + Modkit pileup
###############################################################################

SUMMARY="$RUN_ROOT/summary/methylation_summary.tsv"
printf 'sample\tbarcode\ttrack\tmod_code_or_name\tn_sites_cov_ge_%s\tmean_percent_modified\tmean_valid_coverage\n' "$MIN_COVERAGE" > "$SUMMARY"

for idx in "${!BARCODES[@]}"; do
  barcode="${BARCODES[$idx]}"
  sample="${SAMPLE_NAMES[$idx]}"

  ids="$RUN_ROOT/read_ids/${sample}.${barcode}.read_ids.txt"
  sample_bam="$RUN_ROOT/modbam/${sample}.mod.sorted.bam"

  sample_bed_sep="$RUN_ROOT/bedmethyl/${sample}.CpG.separate_5mC_5hmC.bed.gz"
  sample_bed_combined="$RUN_ROOT/bedmethyl/${sample}.CpG.combined_5mC_5hmC.bed.gz"

  echo
  echo "============================================================================"
  echo "Sample : $sample"
  echo "Barcode: $barcode"
  echo "============================================================================"

  if [[ "$NO_BARCODE" == "true" ]]; then
    echo "No-barcode mode: using all-read modBAM as sample BAM."

    rm -f "$sample_bam" "$sample_bam.bai" "$sample_bam.csi"

    link_or_copy_file "$ALL_MOD_SORTED" "$sample_bam"

    if [[ -s "$ALL_MOD_SORTED.bai" ]]; then
      link_or_copy_file "$ALL_MOD_SORTED.bai" "$sample_bam.bai"
    elif [[ -s "$ALL_MOD_SORTED.csi" ]]; then
      link_or_copy_file "$ALL_MOD_SORTED.csi" "$sample_bam.csi"
    else
      samtools index -@ "$THREADS" "$sample_bam"
    fi
  else
    echo "Filtering all-read modBAM to sample read IDs."

    samtools view -@ "$THREADS" -N "$ids" -b "$ALL_MOD_SORTED" -o "$sample_bam"
    samtools index -@ "$THREADS" "$sample_bam"
  fi

  echo "Sample BAM read count:"
  samtools view -c "$sample_bam"

  echo "Checking tags:"
  modkit modbam check-tags "$sample_bam" --head 100 > "$RUN_ROOT/logs/${sample}.check_tags.txt" 2>&1 || true

  rm -f "$sample_bed_sep" "$sample_bed_sep.tbi" "$sample_bed_combined" "$sample_bed_combined.tbi"

  echo "Running modkit pileup: separate 5mC and 5hmC CpG rows."
  modkit pileup \
    "$sample_bam" \
    "$sample_bed_sep" \
    --cpg \
    --combine-strands \
    --modified-bases 5mC 5hmC \
    --reference "$REF" \
    --threads "$THREADS" \
    --bgzf \
    --log "$RUN_ROOT/logs/${sample}.modkit_pileup.separate.log"

  tabix -f -p bed "$sample_bed_sep"

  echo "Running modkit pileup: combined 5mC+5hmC CpG rows."
  modkit pileup \
    "$sample_bam" \
    "$sample_bed_combined" \
    --cpg \
    --combine-strands \
    --combine-mods \
    --modified-bases 5mC 5hmC \
    --reference "$REF" \
    --threads "$THREADS" \
    --bgzf \
    --log "$RUN_ROOT/logs/${sample}.modkit_pileup.combined.log"

  tabix -f -p bed "$sample_bed_combined"

  echo "Creating bigWig tracks."

  modkit bm tobigwig \
    "$sample_bed_sep" \
    "$RUN_ROOT/bigwig/${sample}.CpG.5mC.bw" \
    --mod-code m \
    -g "$REF.fai" \
    --log "$RUN_ROOT/logs/${sample}.5mC.bigwig.log" \
    --suppress-progress || true

  modkit bm tobigwig \
    "$sample_bed_sep" \
    "$RUN_ROOT/bigwig/${sample}.CpG.5hmC.bw" \
    --mod-code h \
    -g "$REF.fai" \
    --log "$RUN_ROOT/logs/${sample}.5hmC.bigwig.log" \
    --suppress-progress || true

  modkit bm tobigwig \
    "$sample_bed_sep" \
    "$RUN_ROOT/bigwig/${sample}.CpG.5mC_5hmC_combined.bw" \
    --mod-codes h,m \
    -g "$REF.fai" \
    --log "$RUN_ROOT/logs/${sample}.combined.bigwig.log" \
    --suppress-progress || true

  echo "Writing methylation summaries."

  gzip -dc "$sample_bed_sep" \
    | awk -v sample="$sample" -v barcode="$barcode" -v mincov="$MIN_COVERAGE" '
      BEGIN { OFS="\t" }
      $10 >= mincov {
        code=$4
        n[code]++
        meth[code]+=$11
        cov[code]+=$10
      }
      END {
        for (code in n) {
          print sample, barcode, "separate", code, n[code], meth[code]/n[code], cov[code]/n[code]
        }
      }
    ' >> "$SUMMARY"

  gzip -dc "$sample_bed_combined" \
    | awk -v sample="$sample" -v barcode="$barcode" -v mincov="$MIN_COVERAGE" '
      BEGIN { OFS="\t" }
      $10 >= mincov {
        code=$4
        n[code]++
        meth[code]+=$11
        cov[code]+=$0
      }
      END {
        for (code in n) {
          print sample, barcode, "combined", code, n[code], meth[code]/n[code], cov[code]/n[code]
        }
      }
    ' >> "$SUMMARY"

done

echo
echo "============================================================================"
echo "Done."
echo "============================================================================"
echo "Output dir:"
echo "  $RUN_ROOT"
echo
echo "Aligned modBAMs:"
echo "  $RUN_ROOT/modbam"
echo
echo "bedMethyl:"
echo "  $RUN_ROOT/bedmethyl"
echo
echo "bigWig tracks:"
echo "  $RUN_ROOT/bigwig"
echo
echo "Summary:"
echo "  $SUMMARY"
echo
echo "Quick summary:"
column -t -s $'\t' "$SUMMARY" || cat "$SUMMARY"
