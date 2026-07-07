#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; echo "ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2; exit "$rc"' ERR

###############################################################################
# ONT modBAM -> ALMA-compatible 5mC CpG bedMethyl -> ALMA predictions
#
# Two explicitly separated model generations are supported:
#   publication : alma-classifier v0.1.4, the version released with the paper.
#                 Outputs ALMA Subtype, AML Epigenomic Risk, P(Death) at 5 y,
#                 and the 38-CpG AML Signature.
#   current     : alma-classifier v0.2.2. Outputs ALMA Subtype v2 and the
#                 current 38-CpG implementation; it does not include the full
#                 AML Epigenomic Risk model from v0.1.4.
#
# The wrapper deliberately regenerates an ALMA-specific bedMethyl from each
# modBAM rather than using the pre-existing combined 5mC+5hmC bedMethyl. ALMA
# expects 5mC CpG fractions. The primary Modkit invocation follows the ALMA
# recommendation: CpG only, strand-combined, 5hmC ignored, and no filtering.
###############################################################################

LPWGS_ROOT="/media/server/STORAGE/LPWGS_2025"
MANIFEST=""
OUTPUT=""
REF=""
THREADS=16
MODE="both"
CONFIDENCE="0.50"
FORCE=false
ALL_PROBS=true

PUBLICATION_ENV="alma_publication_v014_py39"
CURRENT_ENV="alma_current_v022_py311"
CURRENT_MODELS_DIR=""

usage() {
  cat <<'EOF'
Usage:
  run_ont_alma_epigenetic_predictions.sh \
    --manifest /path/alma_samples.tsv \
    --output /path/ALMA_output \
    [options]

Required:
  --manifest TSV
      Tab-delimited manifest with this header:
        sample<TAB>group<TAB>modbam<TAB>notes

      group is normally leukemia or normal.
      notes may be blank, but retain the fourth column.

  --output DIR
      Exact output folder.

Options:
  --lpwgs-root DIR
      Default: /media/server/STORAGE/LPWGS_2025

  --ref FASTA
      Default: <lpwgs-root>/references/samurai_hg38/genome.fa

  --threads N
      Modkit/samtools threads. Default: 16.

  --mode publication|current|both
      publication = paper release v0.1.4, including AML Epigenomic Risk.
      current     = current release v0.2.2, including ALMA Subtype v2.
      both        = run both and create a side-by-side table. Default: both.

  --confidence FLOAT
      Confidence threshold passed to publication v0.1.4. Default: 0.50.

  --publication-env NAME
      Default: alma_publication_v014_py39

  --current-env NAME
      Default: alma_current_v022_py311

  --current-models-dir DIR
      Default: <lpwgs-root>/resources/alma_classifier/v0.2.2/models

  --no-all-probs
      Do not include every subtype probability in the v0.2.2 output.

  --force
      Recreate ALMA bedMethyl files and prediction outputs.

  -h, --help
      Show help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="$2"; shift 2 ;;
    --output|--outdir) OUTPUT="$2"; shift 2 ;;
    --lpwgs-root) LPWGS_ROOT="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --threads) THREADS="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --confidence) CONFIDENCE="$2"; shift 2 ;;
    --publication-env) PUBLICATION_ENV="$2"; shift 2 ;;
    --current-env) CURRENT_ENV="$2"; shift 2 ;;
    --current-models-dir) CURRENT_MODELS_DIR="$2"; shift 2 ;;
    --no-all-probs) ALL_PROBS=false; shift ;;
    --force) FORCE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

[[ -n "$MANIFEST" ]] || { echo "ERROR: --manifest is required" >&2; usage >&2; exit 1; }
[[ -n "$OUTPUT" ]] || { echo "ERROR: --output is required" >&2; usage >&2; exit 1; }
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: --threads must be a positive integer" >&2; exit 1; }
[[ "$CONFIDENCE" =~ ^(0([.][0-9]+)?|1([.]0+)?)$ ]] || { echo "ERROR: --confidence must be between 0 and 1" >&2; exit 1; }
case "$MODE" in publication|current|both) ;; *) echo "ERROR: --mode must be publication, current, or both" >&2; exit 1 ;; esac

LPWGS_ROOT="$(readlink -m "$LPWGS_ROOT")"
MANIFEST="$(readlink -m "$MANIFEST")"
OUTPUT="$(readlink -m "$OUTPUT")"
[[ -n "$REF" ]] || REF="$LPWGS_ROOT/references/samurai_hg38/genome.fa"
REF="$(readlink -m "$REF")"
[[ -n "$CURRENT_MODELS_DIR" ]] || CURRENT_MODELS_DIR="$LPWGS_ROOT/resources/alma_classifier/v0.2.2/models"
CURRENT_MODELS_DIR="$(readlink -m "$CURRENT_MODELS_DIR")"

[[ -s "$MANIFEST" ]] || { echo "ERROR: missing or empty manifest: $MANIFEST" >&2; exit 1; }
[[ -s "$REF" ]] || { echo "ERROR: missing reference FASTA: $REF" >&2; exit 1; }

mkdir -p "$OUTPUT"/{input,bedmethyl,predictions/publication_v0.1.4,predictions/current_v0.2.2,qc,logs,summary,scripts,tmp,environment}
mkdir -p "$CURRENT_MODELS_DIR"
cp -f "$MANIFEST" "$OUTPUT/input/alma_samples.tsv"

###############################################################################
# Conda and required command-line tools
###############################################################################

CONDA_SH="/home/server/anaconda3/etc/profile.d/conda.sh"
[[ -f "$CONDA_SH" ]] || { echo "ERROR: conda.sh not found: $CONDA_SH" >&2; exit 1; }
# shellcheck source=/dev/null
source "$CONDA_SH"
conda activate base

if command -v mamba >/dev/null 2>&1; then
  CONDA_FRONTEND="mamba"
else
  CONDA_FRONTEND="conda"
fi

for tool in samtools modkit gzip awk; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "ERROR: required tool '$tool' is not available in the active base environment." >&2
    echo "Install with: conda install -y -c conda-forge -c bioconda samtools htslib ont-modkit" >&2
    exit 1
  }
done

[[ -s "$REF.fai" ]] || samtools faidx "$REF"
FIRST_REF_CONTIG="$(awk 'NR==1{print $1}' "$REF.fai")"
if [[ "$FIRST_REF_CONTIG" != chr* ]]; then
  echo "ERROR: ALMA hg38 coordinates are expected in UCSC chr* style, but the reference starts with: $FIRST_REF_CONTIG" >&2
  echo "Use the same UCSC hg38 reference used for the existing modBAMs." >&2
  exit 1
fi

###############################################################################
# Helpers
###############################################################################

sanitize_id() {
  local s="$1"
  s="${s//$'\r'/}"
  s="${s// /_}"
  printf '%s' "$s" | sed 's/[^A-Za-z0-9_.-]/_/g'
}

env_exists() {
  local env_name="$1"
  conda env list | awk 'NF>0 && $1 !~ /^#/ {print $1}' | grep -Fxq "$env_name"
}

installed_alma_version() {
  local env_name="$1"
  conda run -n "$env_name" python -c \
    'import importlib.metadata as m; print(m.version("alma-classifier"))' 2>/dev/null | tail -n 1 | tr -d '\r'
}

setup_publication_env() {
  if ! env_exists "$PUBLICATION_ENV"; then
    echo "Creating publication ALMA environment: $PUBLICATION_ENV"
    "$CONDA_FRONTEND" create -y -n "$PUBLICATION_ENV" -c conda-forge python=3.9 pip libgcc-ng
  fi

  local observed=""
  observed="$(installed_alma_version "$PUBLICATION_ENV" || true)"
  if [[ "$observed" != "0.1.4" ]]; then
    echo "Installing alma-classifier publication release v0.1.4..."
    conda run -n "$PUBLICATION_ENV" python -m pip install --upgrade pip wheel "setuptools==80.9.0"
    conda run -n "$PUBLICATION_ENV" python -m pip install --no-cache-dir \
      "numpy==1.24.4" \
      "pandas==2.0.3" \
      "scikit-learn==1.2.2" \
      "lightgbm==4.6.0" \
      "joblib==1.3.2" \
      "openpyxl>=3.0" \
      "pacmap==0.7.0" \
      "alma-classifier==0.1.4"
  fi

  # PaCMAP 0.7.0 imports the legacy pkg_resources module.
  # setuptools 82 removed pkg_resources, so enforce the final compatible release
  # on every invocation, including pre-existing ALMA environments.
  echo "Checking PaCMAP/pkg_resources compatibility..."
  if ! conda run -n "$PUBLICATION_ENV" python -c 'import pkg_resources, pacmap' >/dev/null 2>&1; then
    echo "Repairing legacy pkg_resources compatibility with setuptools 80.9.0..."
    conda run --no-capture-output -n "$PUBLICATION_ENV" \
      python -m pip install --no-cache-dir --force-reinstall "setuptools==80.9.0"
  fi

  conda run --no-capture-output -n "$PUBLICATION_ENV" python - <<'PY'
import importlib.metadata as metadata
import pkg_resources  # required by pacmap==0.7.0
import pacmap
print("setuptools:", metadata.version("setuptools"))
print("PaCMAP/pkg_resources compatibility: OK")
PY

  echo "Checking/downloading v0.1.4 model files..."
  conda run --no-capture-output -n "$PUBLICATION_ENV" python -m alma_classifier.download_models
  conda run -n "$PUBLICATION_ENV" python - <<'PY'
from alma_classifier.models import validate_models
ok, msg = validate_models()
if not ok:
    raise SystemExit(msg)
print("Publication v0.1.4 model validation: OK")
PY
}

setup_current_env() {
  if ! env_exists "$CURRENT_ENV"; then
    echo "Creating current ALMA environment: $CURRENT_ENV"
    "$CONDA_FRONTEND" create -y -n "$CURRENT_ENV" -c conda-forge python=3.11 pip libgcc-ng
  fi

  local observed=""
  observed="$(installed_alma_version "$CURRENT_ENV" || true)"
  if [[ "$observed" != "0.2.2" ]]; then
    echo "Installing alma-classifier v0.2.2 and CPU PyTorch..."
    conda run -n "$CURRENT_ENV" python -m pip install --upgrade pip wheel setuptools
    if ! conda run -n "$CURRENT_ENV" python -m pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch==2.8.0+cpu"; then
      conda run -n "$CURRENT_ENV" python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.8.0"
    fi
    conda run -n "$CURRENT_ENV" python -m pip install --no-cache-dir "alma-classifier==0.2.2"
  fi

  echo "Checking/downloading v0.2.2 model files..."
  ALMA_MODELS_DIR="$CURRENT_MODELS_DIR" \
    conda run --no-capture-output -n "$CURRENT_ENV" alma-classifier --download-models

  ALMA_MODELS_DIR="$CURRENT_MODELS_DIR" \
    conda run -n "$CURRENT_ENV" python - <<'PY'
from alma_classifier.download import is_models_downloaded, get_models_dir
if not is_models_downloaded():
    raise SystemExit(f"Current ALMA models failed validation under {get_models_dir()}")
print(f"Current v0.2.2 model validation: OK ({get_models_dir()})")
PY
}

check_bam_mod_tags() {
  local bam="$1"
  local rc
  set +o pipefail
  samtools view "$bam" | head -1000 | grep -Eq 'MM:Z|ML:B:C'
  rc=$?
  set -o pipefail
  return "$rc"
}

MODKIT_HELP="$(modkit pileup --help 2>&1 || true)"
modkit_supports() {
  local option="$1"
  grep -Eq -- "(^|[[:space:]])${option}([[:space:]=<]|$)" <<< "$MODKIT_HELP"
}

if modkit_supports "--reference"; then
  MODKIT_REF_OPT="--reference"
elif modkit_supports "--ref"; then
  MODKIT_REF_OPT="--ref"
else
  echo "ERROR: could not identify Modkit reference option (--reference/--ref)." >&2
  exit 1
fi

if modkit_supports "--threads"; then
  MODKIT_THREADS_OPT="--threads"
else
  MODKIT_THREADS_OPT="-t"
fi

BED_COMPRESSED=false
if modkit_supports "--bgzf"; then
  BED_COMPRESSED=true
fi

create_alma_bedmethyl() {
  local sample="$1"
  local bam="$2"
  local outbed="$3"
  local log="$4"
  local -a cmd

  if [[ "$FORCE" == "true" ]]; then
    rm -f "$outbed" "$outbed.tbi" "$outbed.csi"
  fi

  if [[ -s "$outbed" ]]; then
    if [[ "$outbed" == *.gz ]] && ! gzip -t "$outbed" >/dev/null 2>&1; then
      echo "WARNING: existing compressed bedMethyl is corrupt; recreating: $outbed" >&2
      rm -f "$outbed" "$outbed.tbi" "$outbed.csi"
    else
      echo "Reusing ALMA-specific bedMethyl: $outbed"
      return 0
    fi
  fi

  cmd=(
    modkit pileup
    "$bam"
    "$outbed"
    --cpg
    --combine-strands
    "$MODKIT_REF_OPT" "$REF"
    "$MODKIT_THREADS_OPT" "$THREADS"
  )

  # Prefer the exact ALMA documentation behavior. On newer Modkit versions,
  # --modified-bases 5mC is the compatible replacement for excluding 5hmC.
  if modkit_supports "--ignore"; then
    cmd+=(--ignore h)
    MOD_SELECTION="--ignore h"
  elif modkit_supports "--modified-bases"; then
    cmd+=(--modified-bases 5mC)
    MOD_SELECTION="--modified-bases 5mC"
  else
    echo "ERROR: this Modkit version supports neither --ignore nor --modified-bases." >&2
    exit 1
  fi

  if modkit_supports "--no-filtering"; then
    cmd+=(--no-filtering)
    FILTER_MODE="--no-filtering"
  else
    FILTER_MODE="Modkit default filtering (--no-filtering unavailable)"
    echo "WARNING: this Modkit version does not expose --no-filtering; default Modkit filtering will be used." >&2
  fi

  if [[ "$BED_COMPRESSED" == "true" ]]; then
    cmd+=(--bgzf)
  fi

  if modkit_supports "--log"; then
    cmd+=(--log "$log")
  elif modkit_supports "--log-filepath"; then
    cmd+=(--log-filepath "$log")
  fi

  echo "Creating ALMA 5mC CpG bedMethyl for $sample"
  printf ' %q' "${cmd[@]}"
  echo
  "${cmd[@]}"

  [[ -s "$outbed" ]] || { echo "ERROR: Modkit output is empty: $outbed" >&2; exit 1; }
}

bed_stream() {
  local bed="$1"
  if [[ "$bed" == *.gz ]]; then
    gzip -dc "$bed"
  else
    cat "$bed"
  fi
}

write_bed_qc() {
  local sample="$1"
  local group="$2"
  local bam="$3"
  local bed="$4"
  local notes="$5"
  local out="$6"

  bed_stream "$bed" | awk -v sample="$sample" -v group="$group" -v bam="$bam" -v bed="$bed" -v notes="$notes" '
    BEGIN { OFS="\t" }
    $0 !~ /^#/ && NF >= 11 {
      n++
      cov=$10+0
      pct=$11+0
      sumcov+=cov
      sumpct+=pct
      if (cov >= 1) n1++
      if (cov >= 5) n5++
      if (cov >= 10) n10++
      if ($4 ~ /^m([,]|$)/ || $4 == "m") nm++
      if ($4 ~ /^h([,]|$)/ || $4 == "h") nh++
      if ($1 ~ /^chr/) chrrows++
    }
    END {
      print "sample","group","modbam","bedmethyl","bed_rows","m_code_rows","h_code_rows","chr_style_rows","rows_cov_ge_1","rows_cov_ge_5","rows_cov_ge_10","mean_valid_coverage","mean_percent_5mC","notes"
      print sample,group,bam,bed,n+0,nm+0,nh+0,chrrows+0,n1+0,n5+0,n10+0,(n?sumcov/n:0),(n?sumpct/n:0),notes
    }
  ' > "$out"

  local nrows mrows hrows chrrows
  nrows="$(awk 'NR==2{print $5}' "$out")"
  mrows="$(awk 'NR==2{print $6}' "$out")"
  hrows="$(awk 'NR==2{print $7}' "$out")"
  chrrows="$(awk 'NR==2{print $8}' "$out")"
  (( nrows > 0 )) || { echo "ERROR: no valid bedMethyl rows for $sample" >&2; exit 1; }
  (( mrows > 0 )) || { echo "ERROR: no 5mC (m-code) rows were found for $sample" >&2; exit 1; }
  (( hrows == 0 )) || { echo "ERROR: 5hmC (h-code) rows remain in the ALMA input for $sample; refusing ambiguous input." >&2; exit 1; }
  (( chrrows > 0 )) || { echo "ERROR: bedMethyl lacks chr* contigs for $sample; genome-build/contig mismatch is likely." >&2; exit 1; }
}

###############################################################################
# Validate manifest before installing/running models
###############################################################################

EXPECTED_HEADER=$'sample\tgroup\tmodbam\tnotes'
ACTUAL_HEADER="$(head -n 1 "$MANIFEST" | tr -d '\r')"
if [[ "$ACTUAL_HEADER" != "$EXPECTED_HEADER" ]]; then
  echo "ERROR: manifest header must be exactly:" >&2
  printf '  %s\n' "$EXPECTED_HEADER" >&2
  echo "Observed:" >&2
  printf '  %s\n' "$ACTUAL_HEADER" >&2
  exit 1
fi

SAMPLE_COUNT=0
LEUKEMIA_COUNT=0
NORMAL_COUNT=0
declare -A SEEN_SAMPLES=()

while IFS=$'\t' read -r raw_sample raw_group raw_bam raw_notes; do
  [[ -n "${raw_sample// }" ]] || continue
  [[ "$raw_sample" == \#* ]] && continue

  sample="$(sanitize_id "$raw_sample")"
  group="$(sanitize_id "$raw_group")"
  bam="$(readlink -m "$raw_bam")"
  notes="${raw_notes//$'\r'/}"

  [[ -n "$sample" ]] || { echo "ERROR: empty sample ID after sanitization" >&2; exit 1; }
  [[ -z "${SEEN_SAMPLES[$sample]:-}" ]] || { echo "ERROR: duplicate sample ID: $sample" >&2; exit 1; }
  SEEN_SAMPLES[$sample]=1

  [[ "$group" == "leukemia" || "$group" == "normal" ]] || {
    echo "ERROR: group for $sample must be leukemia or normal; observed: $group" >&2
    exit 1
  }
  [[ -s "$bam" ]] || { echo "ERROR: missing modBAM for $sample: $bam" >&2; exit 1; }
  samtools quickcheck -v "$bam" >/dev/null || { echo "ERROR: BAM failed samtools quickcheck: $bam" >&2; exit 1; }
  check_bam_mod_tags "$bam" || { echo "ERROR: BAM lacks MM/ML modified-base tags: $bam" >&2; exit 1; }

  SAMPLE_COUNT=$((SAMPLE_COUNT + 1))
  if [[ "$group" == "leukemia" ]]; then LEUKEMIA_COUNT=$((LEUKEMIA_COUNT + 1)); else NORMAL_COUNT=$((NORMAL_COUNT + 1)); fi
done < <(tail -n +2 "$MANIFEST")

(( SAMPLE_COUNT > 0 )) || { echo "ERROR: manifest contains no samples" >&2; exit 1; }

echo
printf 'Validated manifest: total=%d leukemia=%d normal=%d\n' "$SAMPLE_COUNT" "$LEUKEMIA_COUNT" "$NORMAL_COUNT"

###############################################################################
# Set up the requested model generation(s)
###############################################################################

if [[ "$MODE" == "publication" || "$MODE" == "both" ]]; then
  setup_publication_env
fi
if [[ "$MODE" == "current" || "$MODE" == "both" ]]; then
  setup_current_env
fi

PUB_VERSION="not_run"
CUR_VERSION="not_run"
if [[ "$MODE" == "publication" || "$MODE" == "both" ]]; then PUB_VERSION="$(installed_alma_version "$PUBLICATION_ENV")"; fi
if [[ "$MODE" == "current" || "$MODE" == "both" ]]; then CUR_VERSION="$(installed_alma_version "$CURRENT_ENV")"; fi

cat > "$OUTPUT/environment/run_configuration.txt" <<EOF
LPWGS_ROOT=$LPWGS_ROOT
MANIFEST=$MANIFEST
OUTPUT=$OUTPUT
REFERENCE=$REF
THREADS=$THREADS
MODE=$MODE
CONFIDENCE=$CONFIDENCE
PUBLICATION_ENV=$PUBLICATION_ENV
PUBLICATION_VERSION=$PUB_VERSION
CURRENT_ENV=$CURRENT_ENV
CURRENT_VERSION=$CUR_VERSION
CURRENT_MODELS_DIR=$CURRENT_MODELS_DIR
MODKIT_VERSION=$(modkit --version 2>&1 | head -1)
SAMTOOLS_VERSION=$(samtools --version | head -1)
MODKIT_MODIFICATION_SELECTION=${MOD_SELECTION:-resolved_per_sample}
MODKIT_FILTER_MODE=${FILTER_MODE:-resolved_per_sample}
EOF

###############################################################################
# Process samples sequentially
###############################################################################

printf 'sample\tgroup\tstatus\tpublication_output\tcurrent_output\tmessage\n' > "$OUTPUT/summary/run_status.tsv"

while IFS=$'\t' read -r raw_sample raw_group raw_bam raw_notes; do
  [[ -n "${raw_sample// }" ]] || continue
  [[ "$raw_sample" == \#* ]] && continue

  sample="$(sanitize_id "$raw_sample")"
  group="$(sanitize_id "$raw_group")"
  bam="$(readlink -m "$raw_bam")"
  notes="${raw_notes//$'\r'/}"

  echo
  echo "============================================================================"
  echo "ALMA sample : $sample"
  echo "Group       : $group"
  echo "modBAM      : $bam"
  echo "============================================================================"

  [[ -s "$bam.bai" || -s "$bam.csi" ]] || samtools index -@ "$THREADS" "$bam"

  if [[ "$BED_COMPRESSED" == "true" ]]; then
    alma_bed="$OUTPUT/bedmethyl/${sample}.ALMA.5mC.CpG.bed.gz"
  else
    alma_bed="$OUTPUT/bedmethyl/${sample}.ALMA.5mC.CpG.bed"
  fi

  create_alma_bedmethyl "$sample" "$bam" "$alma_bed" "$OUTPUT/logs/${sample}.modkit_pileup.log"
  write_bed_qc "$sample" "$group" "$bam" "$alma_bed" "$notes" "$OUTPUT/qc/${sample}.bedmethyl_qc.tsv"

  pub_out=""
  cur_out=""

  if [[ "$MODE" == "publication" || "$MODE" == "both" ]]; then
    pub_out="$OUTPUT/predictions/publication_v0.1.4/${sample}.alma_publication_v0.1.4.csv"
    if [[ "$FORCE" == "true" ]]; then rm -f "$pub_out"; fi

    if [[ ! -s "$pub_out" ]]; then
      echo "Running ALMA publication classifier v0.1.4 for $sample..."
      conda run --no-capture-output -n "$PUBLICATION_ENV" alma-classifier \
        --input "$alma_bed" \
        --output "$pub_out" \
        --confidence "$CONFIDENCE" \
        > "$OUTPUT/logs/${sample}.alma_publication_v0.1.4.stdout.log" \
        2> "$OUTPUT/logs/${sample}.alma_publication_v0.1.4.stderr.log"
    else
      echo "Reusing publication prediction: $pub_out"
    fi
    [[ -s "$pub_out" ]] || { echo "ERROR: missing publication prediction for $sample" >&2; exit 1; }
  fi

  if [[ "$MODE" == "current" || "$MODE" == "both" ]]; then
    cur_out="$OUTPUT/predictions/current_v0.2.2/${sample}.alma_current_v0.2.2.csv"
    if [[ "$FORCE" == "true" ]]; then rm -f "$cur_out"; fi

    if [[ ! -s "$cur_out" ]]; then
      echo "Running ALMA current classifier v0.2.2 for $sample..."
      current_cmd=(
        conda run --no-capture-output -n "$CURRENT_ENV"
        alma-classifier
        -i "$alma_bed"
        -o "$cur_out"
      )
      [[ "$ALL_PROBS" == "true" ]] && current_cmd+=(--all_probs)

      ALMA_MODELS_DIR="$CURRENT_MODELS_DIR" \
        "${current_cmd[@]}" \
        > "$OUTPUT/logs/${sample}.alma_current_v0.2.2.stdout.log" \
        2> "$OUTPUT/logs/${sample}.alma_current_v0.2.2.stderr.log"
    else
      echo "Reusing current prediction: $cur_out"
    fi
    [[ -s "$cur_out" ]] || { echo "ERROR: missing current prediction for $sample" >&2; exit 1; }
  fi

  printf '%s\t%s\tsuccess\t%s\t%s\t%s\n' "$sample" "$group" "$pub_out" "$cur_out" "completed" >> "$OUTPUT/summary/run_status.tsv"
done < <(tail -n +2 "$MANIFEST")

###############################################################################
# Consolidate all predictions and QC into CSV/HTML/Markdown reports
###############################################################################

AGGREGATE_PY="$OUTPUT/scripts/consolidate_alma_results.py"
cat > "$AGGREGATE_PY" <<'PY'
#!/usr/bin/env python3
import argparse
import html
import math
from pathlib import Path

import pandas as pd


def read_first_row(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    df = pd.read_csv(path)
    if df.empty:
        return {}
    row = df.iloc[0].to_dict()
    # v0.1.4 writes the DataFrame index as an unnamed first CSV column.
    for key in list(row):
        if str(key).startswith("Unnamed:"):
            row["classifier_input_id"] = row.pop(key)
    return row


def safe_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--mode", required=True, choices=["publication", "current", "both"])
    args = ap.parse_args()

    out = Path(args.output)
    manifest = pd.read_csv(args.manifest, sep="\t", dtype=str, keep_default_na=False)

    pub_rows = []
    cur_rows = []
    combined_rows = []
    qc_rows = []

    for rec in manifest.to_dict(orient="records"):
        sample = rec["sample"]
        group = rec["group"]
        notes = rec.get("notes", "")
        modbam = rec["modbam"]

        base = {
            "sample": sample,
            "group": group,
            "modbam": modbam,
            "notes": notes,
        }

        qc_path = out / "qc" / f"{sample}.bedmethyl_qc.tsv"
        if qc_path.exists():
            qcdf = pd.read_csv(qc_path, sep="\t")
            if not qcdf.empty:
                qc_rows.append(qcdf.iloc[0].to_dict())

        pub = {}
        cur = {}

        if args.mode in ("publication", "both"):
            pub_path = out / "predictions" / "publication_v0.1.4" / f"{sample}.alma_publication_v0.1.4.csv"
            pub = read_first_row(pub_path)
            prow = dict(base)
            prow.update(pub)
            prow["prediction_file"] = str(pub_path)
            pub_rows.append(prow)

        if args.mode in ("current", "both"):
            cur_path = out / "predictions" / "current_v0.2.2" / f"{sample}.alma_current_v0.2.2.csv"
            cur = read_first_row(cur_path)
            crow = dict(base)
            crow.update(cur)
            crow["prediction_file"] = str(cur_path)
            cur_rows.append(crow)

        combined = dict(base)
        for key, value in pub.items():
            combined[f"publication_v0.1.4__{key}"] = value
        for key, value in cur.items():
            combined[f"current_v0.2.2__{key}"] = value
        combined_rows.append(combined)

    summary_dir = out / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)

    if pub_rows:
        pd.DataFrame(pub_rows).to_csv(summary_dir / "alma_publication_v0.1.4_all_samples.csv", index=False)
    if cur_rows:
        pd.DataFrame(cur_rows).to_csv(summary_dir / "alma_current_v0.2.2_all_samples.csv", index=False)
    combined_df = pd.DataFrame(combined_rows)
    combined_df.to_csv(summary_dir / "alma_all_models_combined.csv", index=False)
    if qc_rows:
        pd.DataFrame(qc_rows).to_csv(summary_dir / "alma_bedmethyl_qc_all_samples.tsv", sep="\t", index=False)

    # Compact human-readable report.
    report_rows = []
    for row in combined_rows:
        report_rows.append({
            "Sample": row.get("sample", ""),
            "Group": row.get("group", ""),
            "Publication subtype": safe_text(row.get("publication_v0.1.4__ALMA Subtype")),
            "Publication subtype P": safe_text(row.get("publication_v0.1.4__P(Predicted Subtype)")),
            "AML Epigenomic Risk": safe_text(row.get("publication_v0.1.4__AML Epigenomic Risk")),
            "P(Death) at 5y": safe_text(row.get("publication_v0.1.4__P(Death) at 5y")),
            "38-CpG AML Signature": safe_text(row.get("publication_v0.1.4__38CpG-AMLsignature")),
            "Current subtype v2": safe_text(row.get("current_v0.2.2__ALMA Subtype v2")),
            "Current confidence": safe_text(row.get("current_v0.2.2__Diagnostic Confidence")),
            "Notes": row.get("notes", ""),
        })
    report_df = pd.DataFrame(report_rows)

    html_path = summary_dir / "alma_summary_report.html"
    html_path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>ALMA ONT methylation predictions</title>"
        "<style>body{font-family:Arial,sans-serif;max-width:1500px;margin:30px auto;line-height:1.4}"
        "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #ccc;padding:6px;vertical-align:top}"
        "th{background:#f1f1f1;position:sticky;top:0}.note{background:#fff8dc;padding:12px;margin:16px 0}</style>"
        "</head><body><h1>ALMA ONT methylation predictions</h1>"
        f"<p>Mode: <b>{html.escape(args.mode)}</b>. Samples: <b>{len(report_df)}</b>.</p>"
        "<div class='note'><b>Research use only.</b> Publication v0.1.4 is the paper-compatible output. "
        "Prognostic columns are intended only for samples classified as AML/MDS. Normal-control risk fields should remain blank. "
        "Integrate subtype calls with morphology, cytogenetics, variants, CNVs, blast fraction, and sequencing coverage.</div>"
        + report_df.to_html(index=False, escape=True, border=0)
        + "</body></html>\n",
        encoding="utf-8",
    )

    md = [
        "# ALMA ONT methylation predictions",
        "",
        f"Mode: `{args.mode}`",
        f"Samples: `{len(report_df)}`",
        "",
        "Publication v0.1.4 is the paper-compatible model set. Prognostic columns are intended only for AML/MDS predictions.",
        "",
    ]
    for row in report_rows:
        md.extend([
            f"## {row['Sample']}",
            f"- Group: `{row['Group']}`",
            f"- Publication subtype: `{row['Publication subtype']}` (P={row['Publication subtype P']})",
            f"- AML Epigenomic Risk: `{row['AML Epigenomic Risk']}`; P(Death) at 5y=`{row['P(Death) at 5y']}`",
            f"- 38-CpG AML Signature: `{row['38-CpG AML Signature']}`",
            f"- Current subtype v2: `{row['Current subtype v2']}` (confidence={row['Current confidence']})",
            "",
        ])
    (summary_dir / "alma_summary_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print("Consolidated ALMA outputs:")
    for path in sorted(summary_dir.iterdir()):
        if path.is_file():
            print(f"  {path}")


if __name__ == "__main__":
    main()
PY
chmod +x "$AGGREGATE_PY"

if [[ "$MODE" == "current" ]]; then
  AGG_ENV="$CURRENT_ENV"
elif [[ "$MODE" == "publication" ]]; then
  AGG_ENV="$PUBLICATION_ENV"
else
  AGG_ENV="$CURRENT_ENV"
fi

conda run --no-capture-output -n "$AGG_ENV" python "$AGGREGATE_PY" \
  --manifest "$OUTPUT/input/alma_samples.tsv" \
  --output "$OUTPUT" \
  --mode "$MODE"

# Append the resolved Modkit behavior after it has been selected at least once.
cat >> "$OUTPUT/environment/run_configuration.txt" <<EOF
RESOLVED_MODKIT_MODIFICATION_SELECTION=${MOD_SELECTION:-unknown}
RESOLVED_MODKIT_FILTER_MODE=${FILTER_MODE:-unknown}
EOF

###############################################################################
# Final paths
###############################################################################

echo
echo "============================================================================"
echo "ALMA analysis complete"
echo "============================================================================"
echo "Output directory:"
echo "  $OUTPUT"
echo
echo "Primary publication-compatible results:"
echo "  $OUTPUT/summary/alma_publication_v0.1.4_all_samples.csv"
echo
echo "Current v2 results:"
echo "  $OUTPUT/summary/alma_current_v0.2.2_all_samples.csv"
echo
echo "Side-by-side table:"
echo "  $OUTPUT/summary/alma_all_models_combined.csv"
echo
echo "QC:"
echo "  $OUTPUT/summary/alma_bedmethyl_qc_all_samples.tsv"
echo
echo "Human-readable report:"
echo "  $OUTPUT/summary/alma_summary_report.html"
echo "  $OUTPUT/summary/alma_summary_report.md"
echo
echo "Logs:"
echo "  $OUTPUT/logs"
