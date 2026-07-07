#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

###############################################################################
# Sturgeon CNS tumour classifier, sample-agnostic
#
# Output is controlled by:
#   --output /path/to/output_folder
#
# Input modes:
#   1) --modbams BAM1,BAM2
#   2) --modkit-extracts TXT1,TXT2
#   3) --modkit-pileups BED1,BED2
#   4) --samples SAMPLE1,SAMPLE2 with auto-search under --input-root
#
# Sturgeon requires Python >=3.7,<3.10, so this script installs it into
# a dedicated Conda Python 3.9 environment.
###############################################################################

LPWGS_ROOT="/media/server/STORAGE/LPWGS_2025"

OUTPUT=""

SAMPLES_CSV=""
MODBAMS_CSV=""
MODKIT_EXTRACTS_CSV=""
MODKIT_PILEUPS_CSV=""

INPUT_ROOT="$LPWGS_ROOT"

REFERENCE_GENOME="hg38"
THREADS=32

STURGEON_REPO_URL="https://github.com/UMCUGenetics/sturgeon"
STURGEON_ROOT="$LPWGS_ROOT/tools/sturgeon"
STURGEON_ENV="sturgeon_py39"

STURGEON_MODEL_DIR="$LPWGS_ROOT/resources/sturgeon_models"
MODEL_FILES_CSV=""
GENERAL_MODEL_URL="https://www.dropbox.com/s/yzca4exl40x9ukw/general.zip?dl=1"

MERGE_5HMC=true
FORCE=false

HARDWARE_MODE="auto"
GPU_DEVICES="all"

usage() {
  cat <<'EOF'
Usage:
  run_ont_sturgeon_samples.sh --output OUTDIR [input options]

Required:
  --output OUTDIR
      Output folder. Created if it does not exist.

Input mode A: auto-find modBAMs
  --samples SAMPLE1,SAMPLE2
      Searches under --input-root for matching modBAMs:
        <input-root>/**/modbam/<sample>.mod.sorted.bam

Input mode B: explicit modBAMs
  --modbams BAM1,BAM2
      Existing Dorado/Guppy modBAMs with MM/ML tags.
      If --samples is omitted, sample names are inferred from BAM filenames.

Input mode C: existing Modkit extract output
  --modkit-extracts TXT1,TXT2
      Files produced by:
        modkit extract full BAM OUT.txt
      or:
        modkit extract BAM OUT.txt

Input mode D: existing Modkit pileup output
  --modkit-pileups BED1,BED2
      Modkit pileup / bedMethyl files. .gz is accepted.

General:
  --samples LIST
      Comma-separated sample names matching the input list.

  --input-root DIR
      Search root for auto-finding modBAMs.
      Default: /media/server/STORAGE/LPWGS_2025

  --reference-genome hg38|t2t
      Passed to sturgeon inputtobed.
      Default: hg38

  --model-files FILE1,FILE2
      Comma-separated Sturgeon model ZIP files.
      If omitted, downloads/uses general.zip.

  --threads N
      Default: 32

  --no-merge-5hmc
      Do not convert 5hmC calls into 5mC before Sturgeon.
      Only applies when starting from modBAM.

  --force
      Re-run intermediate files even if present.

Hardware:
  --cpu
      Hide CUDA devices.

  --gpu
      Expose CUDA devices.

  --gpu-devices all|0|0,1
      CUDA devices to expose with --gpu.
      Default: all

  -h, --help
      Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT="$2"; shift 2 ;;
    --samples)
      SAMPLES_CSV="$2"; shift 2 ;;
    --modbams)
      MODBAMS_CSV="$2"; shift 2 ;;
    --modkit-extracts)
      MODKIT_EXTRACTS_CSV="$2"; shift 2 ;;
    --modkit-pileups)
      MODKIT_PILEUPS_CSV="$2"; shift 2 ;;
    --input-root)
      INPUT_ROOT="$2"; shift 2 ;;
    --reference-genome)
      REFERENCE_GENOME="$2"; shift 2 ;;
    --model-files)
      MODEL_FILES_CSV="$2"; shift 2 ;;
    --threads)
      THREADS="$2"; shift 2 ;;
    --no-merge-5hmc)
      MERGE_5HMC=false; shift ;;
    --force)
      FORCE=true; shift ;;
    --cpu)
      HARDWARE_MODE="cpu"; shift ;;
    --gpu)
      HARDWARE_MODE="gpu"; shift ;;
    --gpu-devices)
      HARDWARE_MODE="gpu"; GPU_DEVICES="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

[[ -n "$OUTPUT" ]] || { echo "ERROR: --output is required" >&2; usage >&2; exit 1; }

###############################################################################
# Environment
###############################################################################

source /home/server/anaconda3/etc/profile.d/conda.sh
conda activate base

command -v git >/dev/null 2>&1 || sudo apt-get install -y git
command -v curl >/dev/null 2>&1 || sudo apt-get install -y curl
command -v samtools >/dev/null 2>&1 || { echo "ERROR: samtools not found" >&2; exit 1; }
command -v modkit >/dev/null 2>&1 || { echo "ERROR: modkit not found" >&2; exit 1; }

###############################################################################
# Hardware mode
###############################################################################

if [[ "$HARDWARE_MODE" == "cpu" ]]; then
  export CUDA_VISIBLE_DEVICES=""
  echo "Hardware mode: CPU. CUDA_VISIBLE_DEVICES is empty."
elif [[ "$HARDWARE_MODE" == "gpu" ]]; then
  if [[ "$GPU_DEVICES" == "all" ]]; then
    unset CUDA_VISIBLE_DEVICES || true
    echo "Hardware mode: GPU. All visible CUDA devices are allowed."
  else
    export CUDA_VISIBLE_DEVICES="$GPU_DEVICES"
    echo "Hardware mode: GPU. CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
  fi
else
  echo "Hardware mode: auto. CUDA visibility unchanged."
fi

###############################################################################
# Helpers
###############################################################################

sanitize_id() {
  local s="$1"
  s="${s// /_}"
  printf '%s' "$s" | sed 's/[^A-Za-z0-9_.-]/_/g'
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

sample_from_path() {
  local p="$1"
  local b
  b="$(basename "$p")"

  b="${b%.gz}"
  b="${b%.bed}"
  b="${b%.txt}"
  b="${b%.bam}"

  b="${b%.sorted}"
  b="${b%.mod}"
  b="${b%.modkit_extract}"
  b="${b%.CpG}"
  b="${b%.combined_5mC_5hmC}"
  b="${b%.separate_5mC_5hmC}"

  printf '%s' "$b"
}

find_modbam_for_sample() {
  local sample="$1"
  local root="$2"

  find "$root" \
    -type f \
    \( -path "*/modbam/${sample}.mod.sorted.bam" -o -name "${sample}.mod.sorted.bam" -o -name "${sample}*.mod.sorted.bam" \) \
    -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1{$1=""; sub(/^ /,""); print}'
}

run_modkit_adjust() {
  local in_bam="$1"
  local out_bam="$2"
  local log_prefix="$3"

  rm -f "$out_bam" "$out_bam.bai" "$out_bam.csi"

  if modkit adjust-mods "$in_bam" "$out_bam" --convert h m \
      > "${log_prefix}.stdout.log" 2> "${log_prefix}.stderr.log"; then
    return 0
  fi

  rm -f "$out_bam" "$out_bam.bai" "$out_bam.csi"

  if modkit adjust-mods --convert h m "$in_bam" "$out_bam" \
      > "${log_prefix}.alt.stdout.log" 2> "${log_prefix}.alt.stderr.log"; then
    return 0
  fi

  return 1
}

run_modkit_extract() {
  local bam="$1"
  local out_txt="$2"
  local log_prefix="$3"

  rm -f "$out_txt"

  if modkit extract full "$bam" "$out_txt" \
      > "${log_prefix}.stdout.log" 2> "${log_prefix}.stderr.log"; then
    return 0
  fi

  rm -f "$out_txt"

  if modkit extract "$bam" "$out_txt" \
      > "${log_prefix}.legacy.stdout.log" 2> "${log_prefix}.legacy.stderr.log"; then
    return 0
  fi

  return 1
}

copy_or_link() {
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
# Resolve output
###############################################################################

OUTPUT="$(readlink -m "$OUTPUT")"

mkdir -p "$OUTPUT"/{input,adjusted_bam,modkit_extract,modkit_pileup,sturgeon_bed,predictions,logs}

###############################################################################
# Resolve input mode
###############################################################################

declare -a SAMPLES
declare -a MODBAMS
declare -a MODKIT_EXTRACTS
declare -a MODKIT_PILEUPS
declare -a MODEL_FILES

csv_to_array "$SAMPLES_CSV" SAMPLES
csv_to_array "$MODBAMS_CSV" MODBAMS
csv_to_array "$MODKIT_EXTRACTS_CSV" MODKIT_EXTRACTS
csv_to_array "$MODKIT_PILEUPS_CSV" MODKIT_PILEUPS

input_modes=0
[[ ${#MODBAMS[@]} -gt 0 ]] && input_modes=$(( input_modes + 1 ))
[[ ${#MODKIT_EXTRACTS[@]} -gt 0 ]] && input_modes=$(( input_modes + 1 ))
[[ ${#MODKIT_PILEUPS[@]} -gt 0 ]] && input_modes=$(( input_modes + 1 ))

if (( input_modes > 1 )); then
  echo "ERROR: choose only one input mode: --modbams OR --modkit-extracts OR --modkit-pileups." >&2
  exit 1
fi

INPUT_MODE=""

if (( ${#MODBAMS[@]} > 0 )); then
  INPUT_MODE="modbam"
elif (( ${#MODKIT_EXTRACTS[@]} > 0 )); then
  INPUT_MODE="modkit_extract"
elif (( ${#MODKIT_PILEUPS[@]} > 0 )); then
  INPUT_MODE="modkit_pileup"
else
  INPUT_MODE="auto_modbam"
fi

if [[ "$INPUT_MODE" == "auto_modbam" ]]; then
  if (( ${#SAMPLES[@]} == 0 )); then
    echo "ERROR: provide --samples or explicit input files." >&2
    usage >&2
    exit 1
  fi

  INPUT_ROOT="$(readlink -f "$INPUT_ROOT")"

  for i in "${!SAMPLES[@]}"; do
    SAMPLES[$i]="$(sanitize_id "${SAMPLES[$i]}")"
    bam="$(find_modbam_for_sample "${SAMPLES[$i]}" "$INPUT_ROOT")"

    if [[ -z "$bam" || ! -s "$bam" ]]; then
      echo "ERROR: could not find modBAM for sample: ${SAMPLES[$i]}" >&2
      echo "Search root: $INPUT_ROOT" >&2
      exit 1
    fi

    MODBAMS+=("$bam")
  done

  INPUT_MODE="modbam"
fi

if [[ "$INPUT_MODE" == "modbam" ]]; then
  for i in "${!MODBAMS[@]}"; do
    MODBAMS[$i]="$(readlink -f "${MODBAMS[$i]}")"
    [[ -s "${MODBAMS[$i]}" ]] || { echo "ERROR: missing modBAM: ${MODBAMS[$i]}" >&2; exit 1; }
  done

  if (( ${#SAMPLES[@]} == 0 )); then
    for p in "${MODBAMS[@]}"; do
      SAMPLES+=("$(sanitize_id "$(sample_from_path "$p")")")
    done
  fi

  input_count="${#MODBAMS[@]}"

elif [[ "$INPUT_MODE" == "modkit_extract" ]]; then
  for i in "${!MODKIT_EXTRACTS[@]}"; do
    MODKIT_EXTRACTS[$i]="$(readlink -f "${MODKIT_EXTRACTS[$i]}")"
    [[ -s "${MODKIT_EXTRACTS[$i]}" ]] || { echo "ERROR: missing modkit extract file: ${MODKIT_EXTRACTS[$i]}" >&2; exit 1; }
  done

  if (( ${#SAMPLES[@]} == 0 )); then
    for p in "${MODKIT_EXTRACTS[@]}"; do
      SAMPLES+=("$(sanitize_id "$(sample_from_path "$p")")")
    done
  fi

  input_count="${#MODKIT_EXTRACTS[@]}"

elif [[ "$INPUT_MODE" == "modkit_pileup" ]]; then
  for i in "${!MODKIT_PILEUPS[@]}"; do
    MODKIT_PILEUPS[$i]="$(readlink -f "${MODKIT_PILEUPS[$i]}")"
    [[ -s "${MODKIT_PILEUPS[$i]}" ]] || { echo "ERROR: missing modkit pileup file: ${MODKIT_PILEUPS[$i]}" >&2; exit 1; }
  done

  if (( ${#SAMPLES[@]} == 0 )); then
    for p in "${MODKIT_PILEUPS[@]}"; do
      SAMPLES+=("$(sanitize_id "$(sample_from_path "$p")")")
    done
  fi

  input_count="${#MODKIT_PILEUPS[@]}"
else
  echo "ERROR: invalid input mode: $INPUT_MODE" >&2
  exit 1
fi

if (( ${#SAMPLES[@]} != input_count )); then
  echo "ERROR: --samples count does not match input count." >&2
  echo "Samples: ${#SAMPLES[@]}" >&2
  echo "Inputs : $input_count" >&2
  exit 1
fi

for i in "${!SAMPLES[@]}"; do
  SAMPLES[$i]="$(sanitize_id "${SAMPLES[$i]}")"
done

###############################################################################
# Resolve model files
###############################################################################

if [[ -n "$MODEL_FILES_CSV" ]]; then
  csv_to_array "$MODEL_FILES_CSV" MODEL_FILES

  for i in "${!MODEL_FILES[@]}"; do
    MODEL_FILES[$i]="$(readlink -f "${MODEL_FILES[$i]}")"
    [[ -s "${MODEL_FILES[$i]}" ]] || { echo "ERROR: model file missing: ${MODEL_FILES[$i]}" >&2; exit 1; }
  done
else
  mkdir -p "$STURGEON_MODEL_DIR"
  GENERAL_MODEL="$STURGEON_MODEL_DIR/general.zip"

  if [[ ! -s "$GENERAL_MODEL" ]]; then
    echo "Downloading Sturgeon general model:"
    echo "  $GENERAL_MODEL"
    curl -L "$GENERAL_MODEL_URL" -o "$GENERAL_MODEL"
  fi

  [[ -s "$GENERAL_MODEL" ]] || { echo "ERROR: failed to download Sturgeon general model" >&2; exit 1; }

  MODEL_FILES=("$GENERAL_MODEL")
fi

###############################################################################
# Install Sturgeon in Python 3.9 Conda env
###############################################################################

if ! conda env list | awk '{print $1}' | grep -qx "$STURGEON_ENV"; then
  echo "Creating Conda environment for Sturgeon:"
  echo "  $STURGEON_ENV"
  conda create -y -n "$STURGEON_ENV" python=3.9 pip
fi

if [[ ! -d "$STURGEON_ROOT/.git" ]]; then
  echo "Cloning Sturgeon:"
  echo "  $STURGEON_ROOT"
  mkdir -p "$(dirname "$STURGEON_ROOT")"
  rm -rf "$STURGEON_ROOT"
  git clone "$STURGEON_REPO_URL" "$STURGEON_ROOT"
fi

if ! conda run -n "$STURGEON_ENV" sturgeon --help >/dev/null 2>&1; then
  echo "Installing Sturgeon into Conda env:"
  echo "  $STURGEON_ENV"
  cd "$STURGEON_ROOT"
  conda run -n "$STURGEON_ENV" python -m pip install --upgrade pip
  conda run -n "$STURGEON_ENV" python -m pip install . --no-cache-dir
fi

STURGEON_CMD=(conda run -n "$STURGEON_ENV" sturgeon)

###############################################################################
# Report
###############################################################################

echo
echo "============================================================================"
echo "Sturgeon run"
echo "============================================================================"
echo "Input mode       : $INPUT_MODE"
echo "Samples          : ${SAMPLES[*]}"
echo "Reference genome : $REFERENCE_GENOME"
echo "Model files      : ${MODEL_FILES[*]}"
echo "Output           : $OUTPUT"
echo "Merge 5hmC->5mC  : $MERGE_5HMC"
echo "Hardware mode    : $HARDWARE_MODE"
echo "GPU devices      : $GPU_DEVICES"
echo "Sturgeon env     : $STURGEON_ENV"
echo "============================================================================"
echo

###############################################################################
# Run Sturgeon per sample
###############################################################################

for idx in "${!SAMPLES[@]}"; do
  sample="${SAMPLES[$idx]}"

  sample_out="$OUTPUT/predictions/$sample"
  extract_dir="$OUTPUT/modkit_extract/$sample"
  pileup_dir="$OUTPUT/modkit_pileup/$sample"
  bed_dir="$OUTPUT/sturgeon_bed/$sample"

  mkdir -p "$sample_out" "$extract_dir" "$pileup_dir" "$bed_dir"

  echo
  echo "----------------------------------------------------------------------------"
  echo "Sample: $sample"
  echo "----------------------------------------------------------------------------"

  predict_input=""

  if [[ "$INPUT_MODE" == "modbam" ]]; then
    modbam="${MODBAMS[$idx]}"

    echo "Input modBAM: $modbam"

    [[ -s "$modbam.bai" || -s "$modbam.csi" ]] || samtools index -@ "$THREADS" "$modbam"

    use_bam="$modbam"

    if [[ "$MERGE_5HMC" == "true" ]]; then
      adjusted="$OUTPUT/adjusted_bam/${sample}.5hmC_as_5mC.mod.sorted.bam"

      if [[ "$FORCE" == "true" || ! -s "$adjusted" ]]; then
        echo "Trying to convert 5hmC calls to 5mC for Sturgeon..."

        if run_modkit_adjust "$modbam" "$adjusted" "$OUTPUT/logs/${sample}.modkit_adjust_mods"; then
          samtools index -@ "$THREADS" "$adjusted"
          use_bam="$adjusted"
        else
          echo "WARNING: modkit adjust-mods failed; using original modBAM."
          use_bam="$modbam"
        fi
      else
        use_bam="$adjusted"
      fi
    fi

    extract_txt="$extract_dir/${sample}.modkit_extract.txt"

    if [[ "$FORCE" == "true" || ! -s "$extract_txt" ]]; then
      echo "Running modkit extract..."
      run_modkit_extract "$use_bam" "$extract_txt" "$OUTPUT/logs/${sample}.modkit_extract" || {
        echo "ERROR: modkit extract failed for sample: $sample" >&2
        exit 1
      }
    fi

    [[ -s "$extract_txt" ]] || { echo "ERROR: empty extract file: $extract_txt" >&2; exit 1; }

    if [[ "$FORCE" == "true" || ! "$(find "$bed_dir" -type f 2>/dev/null | head -1)" ]]; then
      echo "Converting modkit extract output to Sturgeon BED..."
      "${STURGEON_CMD[@]}" inputtobed \
        -i "$extract_dir" \
        -o "$bed_dir" \
        -s modkit \
        --reference-genome "$REFERENCE_GENOME" \
        > "$OUTPUT/logs/${sample}.sturgeon_inputtobed.stdout.log" \
        2> "$OUTPUT/logs/${sample}.sturgeon_inputtobed.stderr.log"
    fi

    predict_input="$bed_dir"

  elif [[ "$INPUT_MODE" == "modkit_extract" ]]; then
    src="${MODKIT_EXTRACTS[$idx]}"
    dest="$extract_dir/${sample}.modkit_extract.txt"

    echo "Input modkit extract: $src"

    if [[ "$FORCE" == "true" || ! -s "$dest" ]]; then
      copy_or_link "$src" "$dest"
    fi

    if [[ "$FORCE" == "true" || ! "$(find "$bed_dir" -type f 2>/dev/null | head -1)" ]]; then
      echo "Converting existing modkit extract output to Sturgeon BED..."
      "${STURGEON_CMD[@]}" inputtobed \
        -i "$extract_dir" \
        -o "$bed_dir" \
        -s modkit \
        --reference-genome "$REFERENCE_GENOME" \
        > "$OUTPUT/logs/${sample}.sturgeon_inputtobed.stdout.log" \
        2> "$OUTPUT/logs/${sample}.sturgeon_inputtobed.stderr.log"
    fi

    predict_input="$bed_dir"

  elif [[ "$INPUT_MODE" == "modkit_pileup" ]]; then
    src="${MODKIT_PILEUPS[$idx]}"
    pileup_uncompressed="$pileup_dir/${sample}.modkit_pileup.bed"
    bed_file="$bed_dir/${sample}.sturgeon.bed"

    echo "Input modkit pileup: $src"

    if [[ "$FORCE" == "true" || ! -s "$pileup_uncompressed" ]]; then
      if [[ "$src" == *.gz ]]; then
        gzip -dc "$src" > "$pileup_uncompressed"
      else
        copy_or_link "$src" "$pileup_uncompressed"
      fi
    fi

    if [[ "$FORCE" == "true" || ! -s "$bed_file" ]]; then
      echo "Converting existing modkit pileup output to Sturgeon BED..."
      "${STURGEON_CMD[@]}" inputtobed \
        -i "$pileup_uncompressed" \
        -o "$bed_file" \
        -s modkit_pileup \
        --reference-genome "$REFERENCE_GENOME" \
        > "$OUTPUT/logs/${sample}.sturgeon_inputtobed_pileup.stdout.log" \
        2> "$OUTPUT/logs/${sample}.sturgeon_inputtobed_pileup.stderr.log"
    fi

    predict_input="$bed_file"
  fi

  echo "Running Sturgeon predict..."
  "${STURGEON_CMD[@]}" predict \
    -i "$predict_input" \
    -o "$sample_out" \
    --model-files "${MODEL_FILES[@]}" \
    --plot-results \
    > "$OUTPUT/logs/${sample}.sturgeon_predict.stdout.log" \
    2> "$OUTPUT/logs/${sample}.sturgeon_predict.stderr.log"

  echo "Prediction files for $sample:"
  find "$sample_out" -type f | sort

  for csv in "$sample_out"/*.csv; do
    [[ -s "$csv" ]] || continue
    echo
    echo "Top rows: $csv"
    head -20 "$csv"
  done
done

echo
echo "Done."
echo "Output:"
echo "  $OUTPUT"
