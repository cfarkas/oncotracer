#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

###############################################################################
# MARLIN acute leukemia methylation classifier runner for ONT methylation data
#
# Input:
#   --input  explicit BAM/modBAM, MARLIN BED, Modkit pileup, or Modkit bedMethyl
#
# Output:
#   --output output folder
#
# Main workflow for BAM input:
#   modBAM with MM/ML tags
#     -> modkit pileup restricted to MARLIN probes using --motif CG 0
#     -> Python conversion to MARLIN-format BED
#     -> MARLIN prediction with R/keras
#
# Important:
#   - This script does NOT accept folders as --input.
#   - If the input file does not exist, it stops and asks you to check the path.
###############################################################################

LPWGS_ROOT="/media/server/STORAGE/LPWGS_2025"

INPUT=""
OUTPUT=""
SAMPLE_NAMES_CSV=""

REFERENCE_BUILD="hg38"
REF="$LPWGS_ROOT/references/samurai_hg38/genome.fa"

THREADS=32
MIN_COVERED_PROBES=1000

CONDA_ENV="marlin"
CONDA_FRONTEND="conda"

MARLIN_ROOT="$LPWGS_ROOT/tools/MARLIN"
MARLIN_REPO_URL="https://github.com/hovestadt/MARLIN"

MODEL_URL="https://zenodo.org/records/15565404/files/marlin_v1.model.hdf5?download=1"
MODEL_MD5="a12d4313ef7a97aa2df9776659bde7b2"
MODEL_OVERRIDE=""

FORCE=false
SKIP_MODEL_MD5=false

HARDWARE_MODE="cpu"
GPU_DEVICES="all"

###############################################################################
# Help
###############################################################################

usage() {
  cat <<'EOF'
Usage:
  run_ont_marlin_leukemia.sh --input INPUT_FILE_OR_LIST --output OUTDIR [options]

Required:
  --input FILE_OR_LIST
      Explicit input file or comma-separated input files.

      Supported file types:
        1. BAM/modBAM with MM/ML methylation tags
        2. MARLIN-format BED:
             chrom start end beta probe_id
        3. Modkit pileup / bedMethyl-like file

      Folder input is intentionally not supported.

  --output OUTDIR
      Output folder. Created if absent.

Optional:
  --sample-names LIST
      Comma-separated sample names matching the input list.
      If omitted, sample names are inferred from filenames.

  --reference-build hg19|hg38|t2t
      MARLIN probe coordinate set to use.
      Default: hg38

  --ref FASTA
      Reference FASTA used by modkit for BAM inputs.
      Default for hg38:
        /media/server/STORAGE/LPWGS_2025/references/samurai_hg38/genome.fa

  --threads N
      Default: 32

  --min-covered-probes N
      Warning threshold. Default: 1000

  --conda-env NAME
      Default: marlin

  --marlin-root DIR
      Default:
        /media/server/STORAGE/LPWGS_2025/tools/MARLIN

  --model FILE
      Existing marlin_v1.model.hdf5. If omitted, downloaded automatically.

  --skip-model-md5
      Do not check model MD5.

  --cpu
      CPU mode. Default. Hides CUDA devices.

  --gpu
      GPU-visible mode. Exposes CUDA devices to TensorFlow.

  --gpu-devices all|0|0,1
      CUDA devices to expose with --gpu.
      Default: all

  --force
      Re-run intermediate files even if present.

  -h, --help
      Show this help.
EOF
}

###############################################################################
# Parse CLI
###############################################################################

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)
      INPUT="$2"; shift 2 ;;
    --output|--outdir)
      OUTPUT="$2"; shift 2 ;;
    --sample-names|--samples)
      SAMPLE_NAMES_CSV="$2"; shift 2 ;;
    --reference-build)
      REFERENCE_BUILD="$2"; shift 2 ;;
    --ref)
      REF="$2"; shift 2 ;;
    --threads)
      THREADS="$2"; shift 2 ;;
    --min-covered-probes)
      MIN_COVERED_PROBES="$2"; shift 2 ;;
    --conda-env)
      CONDA_ENV="$2"; shift 2 ;;
    --marlin-root)
      MARLIN_ROOT="$2"; shift 2 ;;
    --model)
      MODEL_OVERRIDE="$2"; shift 2 ;;
    --skip-model-md5)
      SKIP_MODEL_MD5=true; shift ;;
    --cpu)
      HARDWARE_MODE="cpu"; shift ;;
    --gpu)
      HARDWARE_MODE="gpu"; shift ;;
    --gpu-devices)
      HARDWARE_MODE="gpu"; GPU_DEVICES="$2"; shift 2 ;;
    --force)
      FORCE=true; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

[[ -n "$INPUT" ]] || { echo "ERROR: --input is required" >&2; usage >&2; exit 1; }
[[ -n "$OUTPUT" ]] || { echo "ERROR: --output is required" >&2; usage >&2; exit 1; }

case "$REFERENCE_BUILD" in
  hg19|hg38|t2t) ;;
  *)
    echo "ERROR: --reference-build must be hg19, hg38, or t2t" >&2
    exit 1 ;;
esac

###############################################################################
# Conda setup
###############################################################################

if [[ -f /home/server/anaconda3/etc/profile.d/conda.sh ]]; then
  source /home/server/anaconda3/etc/profile.d/conda.sh
else
  echo "ERROR: Cannot find conda.sh at /home/server/anaconda3/etc/profile.d/conda.sh" >&2
  exit 1
fi

conda activate base

if command -v mamba >/dev/null 2>&1; then
  CONDA_FRONTEND="mamba"
else
  CONDA_FRONTEND="conda"
fi

if ! conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV"; then
  echo "Creating Conda environment: $CONDA_ENV"

  set +e
  "$CONDA_FRONTEND" create -y -n "$CONDA_ENV" \
    -c conda-forge -c bioconda \
    python=3.10 \
    r-base=4.3 \
    r-keras=2.13 \
    r-tensorflow=2.13 \
    tensorflow=2.13 \
    r-data.table \
    r-doparallel \
    r-foreach \
    r-openxlsx \
    r-jsonlite \
    samtools \
    htslib \
    bedtools \
    ont-modkit \
    wget \
    git
  STATUS=$?
  set -e

  if [[ "$STATUS" -ne 0 ]]; then
    echo "First environment solve failed. Trying a more flexible solve..."

    "$CONDA_FRONTEND" create -y -n "$CONDA_ENV" \
      -c conda-forge -c bioconda \
      python=3.10 \
      "r-base>=4.1,<4.4" \
      r-keras \
      r-tensorflow \
      tensorflow=2.13 \
      r-data.table \
      r-doparallel \
      r-foreach \
      r-openxlsx \
      r-jsonlite \
      samtools \
      htslib \
      bedtools \
      ont-modkit \
      wget \
      git
  fi
fi

conda activate "$CONDA_ENV"

export RETICULATE_PYTHON="$(command -v python)"
export TF_CPP_MIN_LOG_LEVEL=2

###############################################################################
# Hardware mode
###############################################################################

if [[ "$HARDWARE_MODE" == "cpu" ]]; then
  export CUDA_VISIBLE_DEVICES=""
  echo "Hardware mode: CPU. CUDA_VISIBLE_DEVICES is empty."
elif [[ "$HARDWARE_MODE" == "gpu" ]]; then
  if [[ "$GPU_DEVICES" == "all" ]]; then
    unset CUDA_VISIBLE_DEVICES || true
    echo "Hardware mode: GPU visible. All visible CUDA devices are allowed."
  else
    export CUDA_VISIBLE_DEVICES="$GPU_DEVICES"
    echo "Hardware mode: GPU visible. CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
  fi
else
  echo "Hardware mode: auto."
fi

###############################################################################
# Install missing packages if env already existed but was incomplete
###############################################################################

install_missing_tool_packages() {
  local missing_pkgs=()

  command -v git >/dev/null 2>&1 || missing_pkgs+=(git)
  command -v wget >/dev/null 2>&1 || missing_pkgs+=(wget)
  command -v samtools >/dev/null 2>&1 || missing_pkgs+=(samtools)
  command -v bedtools >/dev/null 2>&1 || missing_pkgs+=(bedtools)
  command -v modkit >/dev/null 2>&1 || missing_pkgs+=(ont-modkit)

  if (( ${#missing_pkgs[@]} > 0 )); then
    echo "Installing missing command-line packages into $CONDA_ENV:"
    printf '  %s\n' "${missing_pkgs[@]}"
    "$CONDA_FRONTEND" install -y -n "$CONDA_ENV" -c conda-forge -c bioconda "${missing_pkgs[@]}"
    conda activate "$CONDA_ENV"
  fi

  if ! Rscript -e 'library(data.table); library(openxlsx); library(keras); library(doParallel); library(foreach)' >/dev/null 2>&1; then
    echo "Installing missing R packages into $CONDA_ENV..."
    "$CONDA_FRONTEND" install -y -n "$CONDA_ENV" -c conda-forge -c bioconda \
      r-data.table r-openxlsx r-keras r-tensorflow r-doparallel r-foreach tensorflow=2.13
    conda activate "$CONDA_ENV"
  fi
}

install_missing_tool_packages

###############################################################################
# Required tools
###############################################################################

command -v git >/dev/null 2>&1 || { echo "ERROR: git not found in MARLIN env" >&2; exit 1; }
command -v wget >/dev/null 2>&1 || { echo "ERROR: wget not found in MARLIN env" >&2; exit 1; }
command -v Rscript >/dev/null 2>&1 || { echo "ERROR: Rscript not found in MARLIN env" >&2; exit 1; }
command -v samtools >/dev/null 2>&1 || { echo "ERROR: samtools not found in MARLIN env" >&2; exit 1; }
command -v modkit >/dev/null 2>&1 || { echo "ERROR: modkit not found in MARLIN env" >&2; exit 1; }
command -v bedtools >/dev/null 2>&1 || { echo "ERROR: bedtools not found in MARLIN env" >&2; exit 1; }
command -v python >/dev/null 2>&1 || { echo "ERROR: python not found in MARLIN env" >&2; exit 1; }

###############################################################################
# Helpers
###############################################################################

sanitize_id() {
  local s="$1"
  s="${s// /_}"
  printf '%s' "$s" | sed 's/[^A-Za-z0-9_.-]/_/g'
}

trim_string() {
  local s="$1"
  s="${s//$'\r'/}"
  s="${s//$'\n'/}"
  s="$(echo "$s" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  printf '%s' "$s"
}

csv_to_array() {
  local csv="$1"
  local -n arr_ref="$2"

  csv="${csv//;/,}"

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
  b="${b%.pileup}"
  b="${b%.txt}"
  b="${b%.tsv}"
  b="${b%.bam}"

  b="${b%.sorted}"
  b="${b%.mod}"
  b="${b%.markdup}"
  b="${b%.marlin_input}"

  sanitize_id "$b"
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

  if [[ -s "$fasta" && -s "$fai" ]]; then
    return 0
  fi

  echo "Downloading SAMURAI/iGenomes UCSC hg38 reference to:"
  echo "  $ref_dir"

  if command -v aws >/dev/null 2>&1; then
    [[ -s "$fasta" ]] || aws s3 cp --no-sign-request "$s3_base/genome.fa" "$fasta"
    [[ -s "$fai" ]]   || aws s3 cp --no-sign-request "$s3_base/genome.fa.fai" "$fai"
    [[ -s "$dict" ]]  || aws s3 cp --no-sign-request "$s3_base/genome.dict" "$dict" || true
  else
    [[ -s "$fasta" ]] || wget -O "$fasta" "$https_base/genome.fa"
    [[ -s "$fai" ]]   || wget -O "$fai" "$https_base/genome.fa.fai"
    [[ -s "$dict" ]]  || wget -O "$dict" "$https_base/genome.dict" || true
  fi
}

input_type() {
  local f="$1"
  local lower
  lower="$(echo "$f" | tr '[:upper:]' '[:lower:]')"

  if [[ "$lower" == *.bam ]]; then
    echo "bam"
    return 0
  fi

  if [[ "$lower" == *.pileup ]]; then
    echo "pileup"
    return 0
  fi

  if [[ "$lower" == *.bed || "$lower" == *.bed.gz || "$lower" == *.txt || "$lower" == *.tsv ]]; then
    local first
    if [[ "$lower" == *.gz ]]; then
      first="$(gzip -dc "$f" | awk 'NF>0 && $0 !~ /^#/ {print; exit}')"
    else
      first="$(awk 'NF>0 && $0 !~ /^#/ {print; exit}' "$f")"
    fi

    local ncol col5
    ncol="$(awk -F'\t' '{print NF}' <<< "$first")"
    col5="$(awk -F'\t' '{print $5}' <<< "$first")"

    # MARLIN BED: chrom start end beta probe_id
    if [[ "$ncol" -eq 5 && "$col5" =~ ^cg[0-9A-Za-z_:-]+$ ]]; then
      echo "marlin_bed"
      return 0
    fi

    # Modkit bedMethyl / pileup: usually >= 11 columns.
    if [[ "$ncol" -ge 11 ]]; then
      echo "bedmethyl"
      return 0
    fi
  fi

  echo "unknown"
}

check_bam_has_mod_tags() {
  local bam="$1"
  local status

  set +o pipefail
  samtools view "$bam" | head -1000 | grep -Eq 'MM:Z|ML:B:C'
  status=$?
  set -o pipefail

  return "$status"
}

run_modkit_pileup() {
  local bam="$1"
  local probes_bed="$2"
  local ref="$3"
  local out_pileup="$4"

  local logdir
  local base
  logdir="$(dirname "$out_pileup")/../logs"
  base="$(basename "$out_pileup")"
  mkdir -p "$logdir"

  rm -f "$out_pileup"

  echo "Running modkit pileup:"
  echo "  BAM   : $bam"
  echo "  probes: $probes_bed"
  echo "  out   : $out_pileup"

  try_pileup() {
    local label="$1"
    shift

    echo
    echo "Trying modkit pileup mode: $label"
    echo "Command:"
    printf ' %q' "$@"
    echo

    rm -f "$out_pileup"

    if "$@" > "$logdir/${base}.${label}.stdout.log" 2> "$logdir/${base}.${label}.stderr.log"; then
      if [[ -s "$out_pileup" ]]; then
        echo "modkit pileup succeeded with mode: $label"
        return 0
      fi

      echo "WARNING: modkit command exited successfully but output is empty: $out_pileup" >&2
      return 1
    fi

    echo "modkit pileup failed with mode: $label" >&2
    echo "Last stderr lines:" >&2
    tail -n 30 "$logdir/${base}.${label}.stderr.log" >&2 || true
    return 1
  }

  # Your local modkit requires --motif or --modified-bases when --include-bed is used.
  # MARLIN probes are CpG methylation probes, so motif CG offset 0 is the primary mode.
  if try_pileup "motif_CG0_reference" \
      modkit pileup \
        --reference "$ref" \
        --include-bed "$probes_bed" \
        --motif CG 0 \
        --threads "$THREADS" \
        --combine-mods \
        "$bam" \
        "$out_pileup"; then
    return 0
  fi

  if try_pileup "cpg_reference" \
      modkit pileup \
        --reference "$ref" \
        --include-bed "$probes_bed" \
        --cpg \
        --threads "$THREADS" \
        --combine-mods \
        "$bam" \
        "$out_pileup"; then
    return 0
  fi

  if try_pileup "modified_bases_m_reference" \
      modkit pileup \
        --reference "$ref" \
        --include-bed "$probes_bed" \
        --modified-bases m \
        --threads "$THREADS" \
        --combine-mods \
        "$bam" \
        "$out_pileup"; then
    return 0
  fi

  echo
  echo "ERROR: all modkit pileup attempts failed." >&2
  echo "Check logs:" >&2
  echo "  $logdir/${base}.*.stderr.log" >&2
  exit 1
}

###############################################################################
# Output folders
###############################################################################

OUTPUT="$(readlink -m "$OUTPUT")"

mkdir -p "$OUTPUT"/{input,pileup,marlin_bed,predictions,plots,logs,tmp,resources,scripts}

###############################################################################
# Clone/update MARLIN
###############################################################################

if [[ ! -d "$MARLIN_ROOT/.git" ]]; then
  echo "Cloning MARLIN:"
  echo "  $MARLIN_ROOT"

  mkdir -p "$(dirname "$MARLIN_ROOT")"
  rm -rf "$MARLIN_ROOT"
  git clone "$MARLIN_REPO_URL" "$MARLIN_ROOT"
else
  echo "Using existing MARLIN repository:"
  echo "  $MARLIN_ROOT"
fi

MARLIN_FILES="$MARLIN_ROOT/MARLIN_realtime/files"

[[ -d "$MARLIN_FILES" ]] || {
  echo "ERROR: MARLIN files folder not found:"
  echo "  $MARLIN_FILES"
  exit 1
}

FEATURES="$MARLIN_FILES/marlin_v1.features.RData"
CLASS_ANNO="$MARLIN_FILES/marlin_v1.class_annotations.xlsx"
PROBES_GZ="$MARLIN_FILES/marlin_v1.probes_${REFERENCE_BUILD}.bed.gz"
PROBES_BED="$OUTPUT/resources/marlin_v1.probes_${REFERENCE_BUILD}.bed"

[[ -s "$FEATURES" ]] || { echo "ERROR: missing MARLIN features: $FEATURES" >&2; exit 1; }
[[ -s "$CLASS_ANNO" ]] || { echo "ERROR: missing MARLIN class annotations: $CLASS_ANNO" >&2; exit 1; }
[[ -s "$PROBES_GZ" ]] || { echo "ERROR: missing MARLIN probes for $REFERENCE_BUILD: $PROBES_GZ" >&2; exit 1; }

if [[ "$FORCE" == "true" || ! -s "$PROBES_BED" ]]; then
  gzip -dc "$PROBES_GZ" > "$PROBES_BED"
fi

###############################################################################
# Model download/check
###############################################################################

if [[ -n "$MODEL_OVERRIDE" ]]; then
  MODEL="$(readlink -m "$MODEL_OVERRIDE")"
else
  MODEL="$MARLIN_FILES/marlin_v1.model.hdf5"
fi

if [[ ! -s "$MODEL" ]]; then
  echo "Downloading MARLIN model to:"
  echo "  $MODEL"

  wget -O "$MODEL" "$MODEL_URL"
fi

if [[ "$SKIP_MODEL_MD5" != "true" && -s "$MODEL" && -n "$MODEL_MD5" ]]; then
  ACTUAL_MD5="$(md5sum "$MODEL" | awk '{print $1}')"

  if [[ "$ACTUAL_MD5" != "$MODEL_MD5" ]]; then
    echo "ERROR: MARLIN model MD5 mismatch." >&2
    echo "Expected: $MODEL_MD5" >&2
    echo "Observed: $ACTUAL_MD5" >&2
    echo "File    : $MODEL" >&2
    echo "" >&2
    echo "If you are intentionally using a newer model, rerun with --skip-model-md5." >&2
    exit 1
  fi
fi

###############################################################################
# Reference
###############################################################################

if [[ "$REFERENCE_BUILD" == "hg38" ]]; then
  REF="$(readlink -m "$REF")"
  download_samurai_hg38_reference_if_needed "$REF"
fi

if [[ "$REFERENCE_BUILD" != "hg38" && "$REF" == "$LPWGS_ROOT/references/samurai_hg38/genome.fa" ]]; then
  echo "ERROR: for --reference-build $REFERENCE_BUILD, provide matching --ref FASTA." >&2
  exit 1
fi

[[ -s "$REF" ]] || { echo "ERROR: reference FASTA missing: $REF" >&2; exit 1; }
[[ -s "$REF.fai" ]] || samtools faidx "$REF"

###############################################################################
# Resolve explicit input list
###############################################################################

declare -a INPUTS
declare -a SAMPLES

if [[ -d "$INPUT" ]]; then
  echo "ERROR: --input points to a directory, but folder input is intentionally disabled." >&2
  echo "Please pass the exact modBAM file path, for example:" >&2
  echo "  --input /path/to/E5_Leukemia_M7_NB.mod.sorted.bam" >&2
  exit 1
fi

csv_to_array "$INPUT" INPUTS

if (( ${#INPUTS[@]} == 0 )); then
  echo "ERROR: no input files resolved from --input." >&2
  exit 1
fi

for i in "${!INPUTS[@]}"; do
  raw_input="$(trim_string "${INPUTS[$i]}")"

  if [[ -z "$raw_input" ]]; then
    echo "ERROR: empty input entry at position $i" >&2
    exit 1
  fi

  if [[ -d "$raw_input" ]]; then
    echo "ERROR: input entry is a directory, not a file:" >&2
    echo "  $raw_input" >&2
    echo "Please pass the exact modBAM file path." >&2
    exit 1
  fi

  resolved_input="$(readlink -m "$raw_input")"

  if [[ ! -e "$resolved_input" ]]; then
    echo "ERROR: input file does not exist:" >&2
    echo "  raw     : $raw_input" >&2
    echo "  resolved: $resolved_input" >&2
    echo "" >&2
    echo "Please check the path to the modBAM and rerun." >&2
    echo "Example:" >&2
    echo "  ls -lh \"$resolved_input\"" >&2
    exit 1
  fi

  if [[ ! -s "$resolved_input" ]]; then
    echo "ERROR: input file exists but is empty:" >&2
    echo "  $resolved_input" >&2
    exit 1
  fi

  INPUTS[$i]="$resolved_input"
done

if [[ -n "$SAMPLE_NAMES_CSV" ]]; then
  csv_to_array "$SAMPLE_NAMES_CSV" SAMPLES

  if (( ${#SAMPLES[@]} != ${#INPUTS[@]} )); then
    echo "ERROR: --sample-names count does not match --input count." >&2
    echo "Inputs : ${#INPUTS[@]}" >&2
    echo "Samples: ${#SAMPLES[@]}" >&2
    exit 1
  fi

  for i in "${!SAMPLES[@]}"; do
    SAMPLES[$i]="$(sanitize_id "$(trim_string "${SAMPLES[$i]}")")"
  done
else
  for f in "${INPUTS[@]}"; do
    SAMPLES+=("$(sample_from_path "$f")")
  done
fi

###############################################################################
# Create Python converter and R prediction script
###############################################################################

CONVERT_PY="$OUTPUT/scripts/make_marlin_bed_from_input.py"
PREDICT_R="$OUTPUT/scripts/predict_marlin_from_beds.R"

cat > "$CONVERT_PY" <<'PYTHON'
#!/usr/bin/env python3

import argparse
import bisect
import gzip
import math
from collections import defaultdict


def open_text(path):
    path = str(path)
    if path.endswith(".gz"):
        return gzip.open(path, "rt")
    return open(path, "rt")


def parse_float(x):
    try:
        if x in ("", ".", "NA", "NaN", "nan"):
            return None
        v = float(x)
        if math.isnan(v):
            return None
        return v
    except Exception:
        return None


def normalize_chrom(chrom):
    chrom = str(chrom).strip()
    if chrom.startswith("chr"):
        chrom = chrom[3:]
    if chrom == "M":
        chrom = "MT"
    return chrom


def read_probes(probes_bed):
    probes = []
    by_chrom = defaultdict(list)

    with open_text(probes_bed) as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 4:
                continue

            chrom = f[0]
            start = int(float(f[1]))
            end = int(float(f[2]))
            probe_id = f[3]

            idx = len(probes)
            probes.append({
                "chrom": chrom,                "chrom_norm": normalize_chrom(chrom),
                "start": start,
                "end": end,
                "probe_id": probe_id,
                "sum_mod": 0.0,
                "sum_cov": 0.0,
            })

            by_chrom[normalize_chrom(chrom)].append((start, end, idx))

    for chrom in by_chrom:
        by_chrom[chrom].sort(key=lambda x: (x[0], x[1], x[2]))

    starts_by_chrom = {chrom: [x[0] for x in intervals] for chrom, intervals in by_chrom.items()}
    return probes, by_chrom, starts_by_chrom


def find_probe_indices(chrom_norm, pos0, end0, by_chrom, starts_by_chrom):
    intervals = by_chrom.get(chrom_norm)
    if not intervals:
        return []

    starts = starts_by_chrom[chrom_norm]
    right = bisect.bisect_right(starts, end0)

    hits = []
    checked = 0
    for j in range(right - 1, -1, -1):
        s, e, idx = intervals[j]
        if e <= pos0:
            break
        if s <= end0 and e > pos0:
            hits.append(idx)
        checked += 1
        if checked > 5000:
            break
    return hits


def read_modkit_to_probes(input_file, probes, by_chrom, starts_by_chrom):
    n_rows = 0
    n_matched = 0

    with open_text(input_file) as fh:
        for line in fh:
            if not line.strip() or line.startswith("#") or line.startswith("track") or line.startswith("browser"):
                continue

            f = line.rstrip("\n").split("\t")
            if len(f) < 11:
                continue

            chrom = f[0]
            chrom_norm = normalize_chrom(chrom)

            try:
                pos0 = int(float(f[1]))
                end0 = int(float(f[2]))
            except Exception:
                continue

            valid_cov = parse_float(f[9])
            percent_or_frac = parse_float(f[10])

            if valid_cov is None or valid_cov <= 0:
                continue

            n_mod = parse_float(f[11]) if len(f) >= 12 else None

            if n_mod is None or n_mod < 0 or n_mod > valid_cov * 1.5:
                if percent_or_frac is None:
                    continue
                frac = percent_or_frac / 100.0 if percent_or_frac > 1 else percent_or_frac
                if frac < 0:
                    continue
                if frac > 1:
                    frac = 1.0
                n_mod = valid_cov * frac

            hits = find_probe_indices(chrom_norm, pos0, end0, by_chrom, starts_by_chrom)
            n_rows += 1

            if not hits:
                continue

            n_matched += 1
            for idx in hits:
                probes[idx]["sum_cov"] += valid_cov
                probes[idx]["sum_mod"] += n_mod

    return n_rows, n_matched


def copy_marlin_bed(input_file, output_bed, probes):
    beta_by_probe = {}
    with open_text(input_file) as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 5:
                continue
            beta_by_probe[f[4]] = f[3]

    with open(output_bed, "w") as out:
        for p in probes:
            beta = beta_by_probe.get(p["probe_id"], "NA")
            out.write(f"{p['chrom']}\t{p['start']}\t{p['end']}\t{beta}\t{p['probe_id']}\n")


def write_marlin_bed(probes, output_bed):
    covered = 0
    with open(output_bed, "w") as out:
        for p in probes:
            if p["sum_cov"] > 0:
                beta = p["sum_mod"] / p["sum_cov"]
                beta = max(0.0, min(1.0, beta))
                beta_s = f"{beta:.6f}"
                covered += 1
            else:
                beta_s = "NA"
            out.write(f"{p['chrom']}\t{p['start']}\t{p['end']}\t{beta_s}\t{p['probe_id']}\n")
    return covered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["pileup", "bedmethyl", "marlin_bed"])
    ap.add_argument("--input", required=True)
    ap.add_argument("--probes", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    probes, by_chrom, starts_by_chrom = read_probes(args.probes)
    if not probes:
        raise SystemExit(f"ERROR: no probes read from {args.probes}")

    if args.mode == "marlin_bed":
        copy_marlin_bed(args.input, args.output, probes)
        covered = 0
        with open(args.output) as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 4 and parts[3] not in ("NA", "", "."):
                    covered += 1
        print(f"Copied MARLIN BED with covered probes: {covered}")
        return

    n_rows, n_matched = read_modkit_to_probes(args.input, probes, by_chrom, starts_by_chrom)
    covered = write_marlin_bed(probes, args.output)

    print(f"Input rows read       : {n_rows}")
    print(f"Input rows overlapping: {n_matched}")
    print(f"Covered MARLIN probes : {covered}")
    print(f"Total MARLIN probes   : {len(probes)}")

    if covered == 0:
        raise SystemExit("ERROR: zero MARLIN probes covered. Check genome build/contig naming and whether the pileup overlaps MARLIN probes.")


if __name__ == "__main__":
    main()
PYTHON

chmod +x "$CONVERT_PY"

cat > "$PREDICT_R" <<'RSCRIPT'
options(max.print = 1000)
options(stringsAsFactors = FALSE)
options(scipen = 999)

library(data.table)
library(openxlsx)
library(keras)

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 6) {
  stop("Usage: Rscript predict_marlin_from_beds.R BEDS_CSV SAMPLES_CSV FEATURES_RDATA MODEL_HDF5 CLASS_ANNOTATION_XLSX OUTDIR")
}

beds_csv <- args[1]
samples_csv <- args[2]
features_rdata <- args[3]
model_hdf5 <- args[4]
class_annotation_xlsx <- args[5]
outdir <- args[6]

dir.create(outdir, recursive = TRUE, showWarnings = FALSE)

beds <- strsplit(beds_csv, ",", fixed = TRUE)[[1]]
samples <- strsplit(samples_csv, ",", fixed = TRUE)[[1]]

if (length(beds) != length(samples)) {
  stop("BEDS and SAMPLES count mismatch")
}

message("Loading MARLIN features: ", features_rdata)
load(features_rdata)

if (!exists("betas_sub_names")) {
  stop("The features RData did not contain betas_sub_names.")
}

message("Loading MARLIN model: ", model_hdf5)
model <- load_model_hdf5(model_hdf5)

message("Loading class annotations: ", class_annotation_xlsx)
class_anno <- read.xlsx(class_annotation_xlsx)

if ("model_id" %in% colnames(class_anno)) {
  class_anno <- class_anno[order(class_anno$model_id), ]
}

if (!"class_name_current" %in% colnames(class_anno)) {
  candidate_cols <- grep("class", colnames(class_anno), value = TRUE, ignore.case = TRUE)
  if (length(candidate_cols) == 0) {
    stop("class annotation XLSX lacks class_name_current or any class-like column")
  }
  class_anno$class_name_current <- class_anno[[candidate_cols[1]]]
}

x_list <- list()
covered <- integer(length(samples))

for (i in seq_along(beds)) {
  message("Reading MARLIN BED: ", beds[i])
  dt <- fread(beds[i], header = FALSE, fill = TRUE)

  if (ncol(dt) < 5) {
    stop("MARLIN BED must have at least 5 columns: ", beds[i])
  }

  names(dt)[1:5] <- c("chrom", "start", "end", "beta", "probe_id")

  beta_vec <- dt$beta[match(betas_sub_names, dt$probe_id)]
  beta_num <- suppressWarnings(as.numeric(beta_vec))

  covered[i] <- sum(!is.na(beta_num))

  pred_vec <- ifelse(beta_num >= 0.5, 1, -1)
  pred_vec[is.na(pred_vec)] <- 0
  x_list[[samples[i]]] <- pred_vec
}

x <- do.call(rbind, x_list)
rownames(x) <- samples

message("Predicting...")
pred <- predict(model, x)

if (ncol(pred) == nrow(class_anno)) {
  colnames(pred) <- class_anno$class_name_current
} else {
  warning("Model output columns do not match class annotation rows. Using generic class labels.")
  colnames(pred) <- paste0("class_", seq_len(ncol(pred)))
}
rownames(pred) <- samples

save(pred, covered, file = file.path(outdir, "predictions.RData"))

full <- data.table(sample = samples, covered_probes = covered, pred)
fwrite(full, file.path(outdir, "predictions_full_matrix.tsv"), sep = "\t")

ranked <- rbindlist(lapply(seq_len(nrow(pred)), function(i) {
  scores <- pred[i, ]
  o <- order(scores, decreasing = TRUE)
  data.table(
    sample = rownames(pred)[i],
    rank = seq_along(scores),
    class = colnames(pred)[o],
    score = as.numeric(scores[o]),
    covered_probes = covered[i]
  )
}))

fwrite(ranked, file.path(outdir, "predictions_ranked.tsv"), sep = "\t")
top <- ranked[rank <= 10]
fwrite(top, file.path(outdir, "top10_predictions.tsv"), sep = "\t")
fwrite(ranked[rank == 1], file.path(outdir, "top_prediction.tsv"), sep = "\t")

for (s in samples) {
  sub <- ranked[sample == s][rank <= 20]
  safe <- gsub("[^A-Za-z0-9_.-]", "_", s)

  pdf(file.path(outdir, paste0(safe, ".top20_predictions.pdf")), width = 12, height = 7)
  par(mar = c(10, 5, 4, 2))
  barplot(
    height = rev(sub$score),
    names.arg = rev(sub$class),
    horiz = FALSE,
    las = 2,
    cex.names = 0.65,
    ylim = c(0, 1),
    ylab = "MARLIN score",
    main = paste0(s, " - MARLIN top 20 classes")
  )
  abline(h = c(0.8, 0.95), lty = 2, col = "gray40")
  dev.off()

  png(file.path(outdir, paste0(safe, ".top20_predictions.png")), width = 1800, height = 1100, res = 180)
  par(mar = c(10, 5, 4, 2))
  barplot(
    height = rev(sub$score),
    names.arg = rev(sub$class),
    horiz = FALSE,
    las = 2,
    cex.names = 0.65,
    ylim = c(0, 1),
    ylab = "MARLIN score",
    main = paste0(s, " - MARLIN top 20 classes")
  )
  abline(h = c(0.8, 0.95), lty = 2, col = "gray40")
  dev.off()
}

message("Done. Top predictions:")
print(ranked[rank <= 5])
RSCRIPT

###############################################################################
# Report
###############################################################################

echo
echo "============================================================================"
echo "MARLIN leukemia methylation classification"
echo "============================================================================"
echo "Input            : $INPUT"
echo "Output           : $OUTPUT"
echo "Reference build  : $REFERENCE_BUILD"
echo "Reference FASTA  : $REF"
echo "MARLIN root      : $MARLIN_ROOT"
echo "MARLIN features  : $FEATURES"
echo "MARLIN probes    : $PROBES_BED"
echo "MARLIN model     : $MODEL"
echo "Conda env        : $CONDA_ENV"
echo "Threads          : $THREADS"
echo "Hardware mode    : $HARDWARE_MODE"
echo "============================================================================"
echo

###############################################################################
# Process each input to MARLIN BED
###############################################################################

declare -a MARLIN_BEDS

for idx in "${!INPUTS[@]}"; do
  infile="${INPUTS[$idx]}"
  sample="${SAMPLES[$idx]}"
  typ="$(input_type "$infile")"

  echo
  echo "----------------------------------------------------------------------------"
  echo "Sample: $sample"
  echo "Input : $infile"
  echo "Type  : $typ"
  echo "----------------------------------------------------------------------------"

  marlin_bed="$OUTPUT/marlin_bed/${sample}.marlin_input.bed"
  pileup="$OUTPUT/pileup/${sample}.modkit_marlin_probes.pileup"

  if [[ "$FORCE" == "true" ]]; then
    rm -f "$marlin_bed" "$pileup"
  fi

  case "$typ" in
    bam)
      samtools quickcheck "$infile" || {
        echo "ERROR: BAM failed samtools quickcheck: $infile" >&2
        exit 1
      }

      [[ -s "$infile.bai" || -s "$infile.csi" ]] || samtools index -@ "$THREADS" "$infile"

      if ! check_bam_has_mod_tags "$infile"; then
        echo "ERROR: BAM does not appear to contain MM/ML modified-base tags:" >&2
        echo "  $infile" >&2
        echo "Run Dorado with modified-base calling first, or pass an existing MARLIN BED." >&2
        exit 1
      fi

      if [[ ! -s "$pileup" ]]; then
        run_modkit_pileup "$infile" "$PROBES_BED" "$REF" "$pileup"
      else
        echo "Reusing existing pileup: $pileup"
      fi

      if [[ ! -s "$marlin_bed" ]]; then
        python "$CONVERT_PY" \
          --mode pileup \
          --input "$pileup" \
          --probes "$PROBES_BED" \
          --output "$marlin_bed"
      else
        echo "Reusing existing MARLIN BED: $marlin_bed"
      fi
      ;;

    pileup)
      if [[ ! -s "$marlin_bed" ]]; then
        python "$CONVERT_PY" \
          --mode pileup \
          --input "$infile" \
          --probes "$PROBES_BED" \
          --output "$marlin_bed"
      else
        echo "Reusing existing MARLIN BED: $marlin_bed"
      fi
      ;;

    bedmethyl)
      tmp_input="$infile"
      if [[ "$infile" == *.gz ]]; then
        tmp_input="$OUTPUT/tmp/${sample}.bedmethyl.uncompressed.bed"
        if [[ "$FORCE" == "true" || ! -s "$tmp_input" ]]; then
          gzip -dc "$infile" > "$tmp_input"
        fi
      fi

      if [[ ! -s "$marlin_bed" ]]; then
        python "$CONVERT_PY" \
          --mode bedmethyl \
          --input "$tmp_input" \
          --probes "$PROBES_BED" \
          --output "$marlin_bed"
      else
        echo "Reusing existing MARLIN BED: $marlin_bed"
      fi
      ;;

    marlin_bed)
      if [[ ! -s "$marlin_bed" || "$FORCE" == "true" ]]; then
        if [[ "$infile" == *.gz ]]; then
          gzip -dc "$infile" > "$marlin_bed"
        else
          python "$CONVERT_PY" \
            --mode marlin_bed \
            --input "$infile" \
            --probes "$PROBES_BED" \
            --output "$marlin_bed"
        fi
      else
        echo "Reusing existing MARLIN BED: $marlin_bed"
      fi
      ;;

    *)
      echo "ERROR: unsupported input type for file:" >&2
      echo "  $infile" >&2
      echo "Supported: BAM/modBAM, MARLIN BED, Modkit pileup, Modkit bedMethyl." >&2
      exit 1
      ;;
  esac

  [[ -s "$marlin_bed" ]] || {
    echo "ERROR: MARLIN BED was not created for $sample" >&2
    exit 1
  }

  covered_count="$(awk '$4 != "NA" && $4 != "" && $4 != "." {n++} END{print n+0}' "$marlin_bed")"
  echo "MARLIN BED covered probes: $covered_count"

  if (( covered_count < MIN_COVERED_PROBES )); then
    echo "WARNING: covered probes for $sample are below threshold ($covered_count < $MIN_COVERED_PROBES)." >&2
    echo "Prediction may be unstable; consider more sequencing depth." >&2
  fi

  MARLIN_BEDS+=("$marlin_bed")
done

###############################################################################
# Run prediction
###############################################################################

BEDS_CSV="$(IFS=','; echo "${MARLIN_BEDS[*]}")"
SAMPLES_CSV_FINAL="$(IFS=','; echo "${SAMPLES[*]}")"

Rscript "$PREDICT_R" \
  "$BEDS_CSV" \
  "$SAMPLES_CSV_FINAL" \
  "$FEATURES" \
  "$MODEL" \
  "$CLASS_ANNO" \
  "$OUTPUT/predictions"

###############################################################################
# Final summary
###############################################################################

echo
echo "============================================================================"
echo "Done."
echo "============================================================================"
echo "Output folder:"
echo "  $OUTPUT"
echo
echo "MARLIN input BEDs:"
echo "  $OUTPUT/marlin_bed"
echo
echo "Predictions:"
echo "  $OUTPUT/predictions/top_prediction.tsv"
echo "  $OUTPUT/predictions/top10_predictions.tsv"
echo "  $OUTPUT/predictions/predictions_ranked.tsv"
echo "  $OUTPUT/predictions/predictions_full_matrix.tsv"
echo
echo "Quick top prediction:"
column -t -s $'\t' "$OUTPUT/predictions/top_prediction.tsv" || cat "$OUTPUT/predictions/top_prediction.tsv"
