#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

###############################################################################
# BAM-supported copy-number segment-boundary refinement for SAMURAI outputs
# with optional ZIPcnv-adapted CUSUM comparison. Version v17 adds cna_cytogenomic_input qDNAseq-style BEDs under 04_final_results for both ONT and Illumina codification.
#
# This wrapper refines the coordinates of existing CNA segment boundaries from
# SAMURAI/qDNAseq/ichorCNA using local evidence from the corresponding BAM files.
# It does not infer structural-variant breakpoints. It can additionally run a
# ZIPcnv-inspired CUSUM detector on the same bin-level signal and compare those
# calls against the BAM-refined final segments.
#
# Main idea:
#   1. Read existing bin-level CNA data and prior SAMURAI/qDNAseq/ichorCNA
#      segmentations.
#   2. For each existing copy-number segment boundary, extract smaller local
#      BAM coverage windows around that boundary.
#   3. Move the boundary only when local BAM evidence supports a better
#      coordinate.
#   4. If BAM resolution is poor or evidence is insufficient, retain the prior
#      segmentation/binning and report that fallback explicitly.
#
# Critical outputs per dataset:
#   01_tables/refined_bins.tsv.gz
#   01_tables/final_segments.tsv
#   01_tables/final_segments.bed
#   01_tables/boundary_refinement_statistics.csv
#   01_tables/sample_refinement_summary.csv
#   01_tables/bam_preparation_report.csv
#   02_samurai_compatible/all_segments.seg
#   02_samurai_compatible/segments_logR_corrected_gistic.seg
#   02_samurai_compatible/bins/<sample>_markdup_bins.bed
#   03_consolidated/consolidated_manifest.csv
#   03_consolidated/zipcnv_adapted_segments.tsv
#   03_consolidated/method_comparison_by_segment.csv
#   03_consolidated/method_comparison_by_sample.csv
#   04_final_results/final_segments.tsv
#   04_final_results/final_segments.bed
#   04_final_results/refined_bins_boundary_bp_difference.csv
#   04_final_results/refined_bins_boundary_bp_difference.xlsx
#   04_final_results/cna_cytogenomic_input/qdnaseq_bins/<sample>_markdup_bins.bed
#   04_final_results/cna_cytogenomic_input/run_cna_codification.sh
###############################################################################

LPWGS_ROOT="/media/server/STORAGE/LPWGS_2025"
ENV_NAME="bam_cnv_boundary_refine_env"
MODE="ont"
OUTDIR=""
SKIP_INSTALL=false
FORCE=false
PURGE_ENV=false

# ONT defaults
ONT_ICHOR_DIR=""
ONT_BAM_DIR=""
ONT_PRIOR_SEG=""
ONT_BINSIZE_KB=500

# Illumina defaults
ILLUMINA_QDNASEQ_DIR=""
ILLUMINA_BAM_DIR=""
ILLUMINA_PRIOR_SEG=""
ILLUMINA_BINSIZE_KB=100

# Refinement defaults
FINE_BIN_KB_ONT=25
FINE_BIN_KB_ILLUMINA=10
SEARCH_RADIUS_BINS=2
SEARCH_RADIUS_BP=0
MIN_SIDE_FINE_BINS=3
MIN_VALID_FINE_BINS=8
MIN_LOCAL_LOG2_DIFF=0.10
MIN_ADJACENT_SEG_DELTA=0.10
MIN_BIC_GAIN=6
PERMUTATIONS=300
PERMUTATION_P=0.05
ACCEPT_RULE="p_and_bic"   # p_and_bic | bic_only | permissive
MAX_CI_BP=0
MAX_CI_FRACTION_OF_COARSE=1.0
MIN_SHIFT_BP=1
MIN_MEDIAN_COVER_UNITS=1
MIN_SIDE_COVER_UNITS=1
MAX_FINE_BINS_PER_WINDOW=400

# BAM options
MIN_MAPQ=20
COVERAGE_MODE_ONT="bases"       # bases for ONT long reads
COVERAGE_MODE_ILLUMINA="starts" # starts for Illumina LP-WGS
NORMAL_SAMPLES="auto"           # auto | none | comma-separated names
NORMAL_BAM_DIRS=""
PON_MODE="auto"                 # auto | on | off
INCLUDE_DUPLICATES=false
INCLUDE_SUPPLEMENTARY=false
INCLUDE_SECONDARY=false

# State labels only; not used for deciding significance of CNAs.
STATE_GAIN_THRESHOLD=0.25
STATE_LOSS_THRESHOLD=-0.25

# ZIPcnv comparison options. The default uses a bin-level CUSUM implementation
# following the ZIPcnv concept, because official ZIPcnv expects base-level depth
# arrays and a large normal baseline. The repository is cloned for provenance.
ZIPCNV_MODE="adapted"          # off | adapted | official | both
ZIPCNV_REPO_URL="https://github.com/Nevermore233/ZIPcnv.git"
ZIPCNV_DIR=""
# Legacy typo guard: older generated copies briefly used ZIPCV_DIR.
# ZIPCNV_DIR is the canonical variable used by this script.
ZIPCV_DIR=""
ZIPCNV_WINDOW_BINS=5
ZIPCNV_K=0.05
ZIPCNV_H_MULT=1.0
ZIPCNV_MIN_SEGMENT_BINS=3
ZIPCNV_MIN_ABS_LOG2=0.25
ZIPCNV_MERGE_GAP_BINS=1
ZIPCNV_COMPARE_MIN_OVERLAP=0.50
ZIPCNV_OFFICIAL_RUN=false
CHECK_ENV_ONLY=false
AUTO_FIX_ENV=true

usage() {
  cat <<'EOF'
BAM-supported CNA segment-boundary refinement for SAMURAI outputs
=================================================================

Purpose
-------
Refine the coordinates of existing copy-number segment boundaries using local
BAM coverage. The workflow preserves the original SAMURAI/qDNAseq/ichorCNA
segmentation when the BAM does not provide enough resolution.

This workflow does NOT:
  - call new CNAs from scratch,
  - decide whether CNA regions are significant,
  - infer structural rearrangement breakpoints,
  - produce plots.

Usage
-----
  bam_cnv_boundary_refine.sh --mode ont|illumina|both [options]

Required ONT ichorCNA inputs
---------------------------
  --ont-ichor-dir PATH
      SAMURAI results/ichorcna directory containing *.correctedDepth.txt.

  --ont-bam-dir PATH
      BAM directory for the same samples, usually <SAMURAI_RUN_ROOT>/bam.

  --ont-prior-seg PATH
      Prior segmentation table, usually segments_logR_corrected_gistic.seg
      or all_segments_ichorcna_gistic.seg.

  --ont-binsize-kb N
      Original coarse bin size in kilobases. Default: 500.

Required Illumina qDNAseq inputs
--------------------------------
  --illumina-qdnaseq-dir PATH
      SAMURAI qdnaseq directory containing bins/*_markdup_bins.bed or
      qDNAseq bin-level files.

  --illumina-bam-dir PATH
      BAM directory for the same samples, for example
      /media/server/STORAGE/LPWGS_2025/samurai_results_100kb/alignment.

  --illumina-prior-seg PATH
      Prior segmentation table, usually qdnaseq/all_segments.seg.

  --illumina-binsize-kb N
      Original coarse bin size in kilobases. Default: 100.

Output options
--------------
  --outdir PATH
      Output root. A dataset-specific subfolder is created inside it.
      Default: <lpwgs-root>/CNA_analyses/bam_cnv_boundary_refine_<YYYYMMDD>.

Environment options
-------------------
  --skip-install
      Reuse the existing conda environment when possible. The script still
      activates the environment and can repair missing packages unless
      --no-auto-fix-env is also supplied.

  --check-env-only
      Activate/check the environment, attempt package repair if enabled, then
      exit without running CNV boundary refinement.

  --purge_env, --purge-env
      Remove the conda environment used by this workflow
      (bam_cnv_boundary_refine_env) and exit. This does not delete analysis
      outputs, BAM files, SAMURAI results, ZIPcnv checkout, or scripts.

  --no-auto-fix-env
      Do not install/repair missing Python packages automatically.

Method options
--------------
  --fine-bin-kb-ont N
      Local BAM window size for ONT in kb. Default: 25.

  --fine-bin-kb-illumina N
      Local BAM window size for Illumina in kb. Default: 10.

  --fine-bin-kb N
      Set the same fine-window size for both ONT and Illumina.

  --search-radius-bins N
      Number of original coarse bins to search on each side of a prior boundary.
      Default: 2.

  --search-radius-bp N
      Override search radius using base pairs instead of coarse bins. Default: 0.

  --min-local-log2-diff X
      Minimum left/right local log2 step needed to move a boundary. Default: 0.10.

  --min-adjacent-seg-delta X
      Skip boundaries where the two prior adjacent segments differ by less than X.
      Default: 0.10.

  --min-bic-gain X
      Minimum BIC improvement supporting the local boundary move. Default: 6.

  --permutations N
      Optional empirical support for the boundary move. This is not a CNA-region
      significance test. Set 0 to disable. Default: 300.

  --permutation-p X
      Empirical support threshold when permutations are enabled. Default: 0.05.

  --accept-rule p_and_bic|bic_only|permissive
      Rule for accepting a refined boundary. Default: p_and_bic.

  --max-ci-fraction-of-coarse X
      Maximum accepted confidence-interval width as a fraction of the original
      coarse bin size. Default: 1.0.

BAM options
-----------
  --min-mapq N
      Minimum mapping quality. Default: 20.

  --coverage-mode-ont bases|starts
      ONT coverage counting mode. Default: bases.

  --coverage-mode-illumina bases|starts
      Illumina coverage counting mode. Default: starts.

  --coverage-mode bases|starts
      Set the same coverage mode for both ONT and Illumina.

  --normal-samples auto|none|sample1,sample2
      Normal/PON sample selection. For ONT local-PON runs, auto treats BAMs not
      matched to tumor samples as normal/PON BAMs. For Illumina, use none unless
      you have explicit normal BAMs. Default: auto.

  --normal-bam-dirs PATH[,PATH2]
      Additional directories containing normal/PON BAMs.

  --pon-mode auto|on|off
      Use local normal/PON BAMs for coverage normalization when available.
      Default: auto.

  --include-duplicates
      Include duplicate-marked reads. Default: off.

  --include-supplementary
      Include supplementary alignments. Default: off.

  --include-secondary
      Include secondary alignments. Default: off.

ZIPcnv comparison options
-------------------------
  --zipcnv-mode off|adapted|official|both
      Add a ZIPcnv comparison layer. Default: adapted.
      adapted: bin-level CUSUM implementation using the same normalized bins.
      official: prepare/provenance for the official ZIPcnv repository; official
                execution is attempted only with --zipcnv-official-run.
      both: run adapted comparison and also prepare/attempt official ZIPcnv.

  --zipcnv-window-bins N
      Sliding-window size in bins for the adapted ZIPcnv CUSUM layer. Default: 5.

  --zipcnv-k X
      CUSUM reference value for the adapted ZIPcnv layer. Default: 0.05.

  --zipcnv-min-abs-log2 X
      Minimum absolute segment median log2 for adapted ZIPcnv calls. Default: 0.25.

  --zipcnv-min-segment-bins N
      Minimum number of consecutive bins in an adapted ZIPcnv segment. Default: 3.

  --zipcnv-compare-min-overlap X
      Minimum reciprocal overlap fraction to call BAM-refined and ZIPcnv-adapted
      segments concordant. Default: 0.50.

  --zipcnv-dir PATH
      Optional local clone of Nevermore233/ZIPcnv. Default: <lpwgs-root>/tools/ZIPcnv.

  --zipcnv-official-run
      Attempt the official ZIPcnv scripts. This is not the default because the
      official implementation expects base-level depth arrays and a baseline file
      of at least 50 normal samples.

Runtime options
---------------
  --lpwgs-root PATH
      Project root. Default: /media/server/STORAGE/LPWGS_2025.

  --skip-install
      Use the existing conda environment instead of creating/updating it.
      Even with --skip-install, the script will activate the workflow
      environment if it exists and automatically repair missing required
      Python packages unless --no-auto-fix-env is used.

  --no-auto-fix-env
      Do not try to install missing Python packages automatically. Instead,
      stop with a clear error listing the missing packages.

  --force
      Overwrite existing output files.

  -h, --help
      Show this help message.

Critical outputs per dataset
----------------------------
  01_tables/boundary_refinement_statistics.csv
      One row per prior segment boundary. Reports original coordinate, final
      coordinate, decision, fallback reason, coverage support, BIC support, and
      confidence interval.

  01_tables/sample_refinement_summary.csv
      One row per sample. Clearly states whether any boundary was refined or
      whether the whole sample fell back to the prior segmentation/binning.

  01_tables/bam_preparation_report.csv
      Reports whether each BAM was used as-is, indexed, or sorted into a temporary
      prepared BAM. The prepared-BAM folder is created only when needed.

  01_tables/refined_bins.tsv.gz
      Headered final bin-level output. If no boundary was refined, it represents
      the original prior segmentation mapped back onto the original bins.

  01_tables/final_segments.tsv
      Headered final segment table.

  01_tables/final_segments.bed
      BED-style final segment table: chrom, start, end, sample, seg_log2, cna_state, final_source, num_mark.

  02_samurai_compatible/all_segments.seg
  02_samurai_compatible/segments_logR_corrected_gistic.seg
  02_samurai_compatible/bins/<sample>_markdup_bins.bed
      SAMURAI/GISTIC-style outputs for downstream CNA analyses.

  03_consolidated/consolidated_manifest.csv
  03_consolidated/zipcnv_adapted_segments.tsv
  03_consolidated/method_comparison_by_segment.csv
  03_consolidated/method_comparison_by_sample.csv
      Consolidated comparison folder. The BAM-refined segmentation remains the
      primary downstream segmentation; ZIPcnv-adapted calls are reported as an
      independent CUSUM-based comparison layer.

  04_final_results/final_segments.tsv
  04_final_results/final_segments.bed
  04_final_results/refined_bins_boundary_bp_difference.csv
  04_final_results/refined_bins_boundary_bp_difference.xlsx
      Final primary outputs for direct review, including refined-bin boundary_bp_difference reports.

  04_final_results/cna_cytogenomic_input/qdnaseq_bins/<sample>_markdup_bins.bed
  04_final_results/cna_cytogenomic_input/run_cna_codification.sh
      qDNAseq-style BED input and runnable command for cna_to_cytogenomic_notation.py, valid for both ONT and Illumina refined bins.

Prepared-BAM folder
-------------------
  _work/prepared_bams/ appears only if one or more input BAMs are not coordinate
  sorted and need a sorted/indexed copy. If your BAMs are already coordinate
  sorted and indexed, this folder is not created. Older runs may have created an
  empty prepared-BAM folder; it is safe to remove.

Examples
--------
  ONT ichorCNA local-PON run:
    bam_cnv_boundary_refine.sh \
      --mode ont \
      --ont-ichor-dir "$ONT_RUN_ROOT/results/ichorcna" \
      --ont-bam-dir "$ONT_RUN_ROOT/bam" \
      --ont-prior-seg "$ONT_RUN_ROOT/results/ichorcna/segments_logR_corrected_gistic.seg" \
      --ont-binsize-kb 500 \
      --outdir "$ONT_OUT" \
      --fine-bin-kb-ont 25 \
      --coverage-mode-ont bases \
      --normal-samples auto \
      --pon-mode auto \
      --skip-install \
      --force

  Illumina qDNAseq run:
    bam_cnv_boundary_refine.sh \
      --mode illumina \
      --illumina-qdnaseq-dir "$ILLUMINA_QDNASEQ_DIR" \
      --illumina-bam-dir "$ILLUMINA_BAM_DIR" \
      --illumina-prior-seg "$ILLUMINA_QDNASEQ_DIR/all_segments.seg" \
      --illumina-binsize-kb 100 \
      --outdir "$ILLUMINA_OUT" \
      --fine-bin-kb-illumina 10 \
      --coverage-mode-illumina starts \
      --normal-samples none \
      --pon-mode off \
      --skip-install \
      --force
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --lpwgs-root) LPWGS_ROOT="$2"; shift 2 ;;
    --outdir) OUTDIR="$2"; shift 2 ;;
    --skip-install) SKIP_INSTALL=true; shift ;;
    --purge_env|--purge-env) PURGE_ENV=true; shift ;;
    --force) FORCE=true; shift ;;

    --ont-ichor-dir) ONT_ICHOR_DIR="$2"; shift 2 ;;
    --ont-bam-dir) ONT_BAM_DIR="$2"; shift 2 ;;
    --ont-prior-seg) ONT_PRIOR_SEG="$2"; shift 2 ;;
    --ont-binsize-kb) ONT_BINSIZE_KB="$2"; shift 2 ;;

    --illumina-qdnaseq-dir) ILLUMINA_QDNASEQ_DIR="$2"; shift 2 ;;
    --illumina-bam-dir) ILLUMINA_BAM_DIR="$2"; shift 2 ;;
    --illumina-prior-seg) ILLUMINA_PRIOR_SEG="$2"; shift 2 ;;
    --illumina-binsize-kb) ILLUMINA_BINSIZE_KB="$2"; shift 2 ;;

    --fine-bin-kb) FINE_BIN_KB_ONT="$2"; FINE_BIN_KB_ILLUMINA="$2"; shift 2 ;;
    --fine-bin-kb-ont) FINE_BIN_KB_ONT="$2"; shift 2 ;;
    --fine-bin-kb-illumina) FINE_BIN_KB_ILLUMINA="$2"; shift 2 ;;
    --search-radius-bins) SEARCH_RADIUS_BINS="$2"; shift 2 ;;
    --search-radius-bp) SEARCH_RADIUS_BP="$2"; shift 2 ;;
    --min-side-fine-bins) MIN_SIDE_FINE_BINS="$2"; shift 2 ;;
    --min-valid-fine-bins) MIN_VALID_FINE_BINS="$2"; shift 2 ;;
    --min-local-log2-diff) MIN_LOCAL_LOG2_DIFF="$2"; shift 2 ;;
    --min-adjacent-seg-delta) MIN_ADJACENT_SEG_DELTA="$2"; shift 2 ;;
    --min-bic-gain) MIN_BIC_GAIN="$2"; shift 2 ;;
    --permutations) PERMUTATIONS="$2"; shift 2 ;;
    --permutation-p) PERMUTATION_P="$2"; shift 2 ;;
    --accept-rule) ACCEPT_RULE="$2"; shift 2 ;;
    --max-ci-bp) MAX_CI_BP="$2"; shift 2 ;;
    --max-ci-fraction-of-coarse) MAX_CI_FRACTION_OF_COARSE="$2"; shift 2 ;;
    --min-shift-bp) MIN_SHIFT_BP="$2"; shift 2 ;;
    --min-median-cover-units) MIN_MEDIAN_COVER_UNITS="$2"; shift 2 ;;
    --min-side-cover-units) MIN_SIDE_COVER_UNITS="$2"; shift 2 ;;
    --max-fine-bins-per-window) MAX_FINE_BINS_PER_WINDOW="$2"; shift 2 ;;

    --min-mapq) MIN_MAPQ="$2"; shift 2 ;;
    --coverage-mode) COVERAGE_MODE_ONT="$2"; COVERAGE_MODE_ILLUMINA="$2"; shift 2 ;;
    --coverage-mode-ont) COVERAGE_MODE_ONT="$2"; shift 2 ;;
    --coverage-mode-illumina) COVERAGE_MODE_ILLUMINA="$2"; shift 2 ;;
    --normal-samples) NORMAL_SAMPLES="$2"; shift 2 ;;
    --normal-bam-dirs) NORMAL_BAM_DIRS="$2"; shift 2 ;;
    --pon-mode) PON_MODE="$2"; shift 2 ;;
    --include-duplicates) INCLUDE_DUPLICATES=true; shift ;;
    --include-supplementary) INCLUDE_SUPPLEMENTARY=true; shift ;;
    --include-secondary) INCLUDE_SECONDARY=true; shift ;;

    --state-gain-threshold) STATE_GAIN_THRESHOLD="$2"; shift 2 ;;
    --state-loss-threshold) STATE_LOSS_THRESHOLD="$2"; shift 2 ;;

    --zipcnv-mode) ZIPCNV_MODE="$2"; shift 2 ;;
    --zipcnv-dir) ZIPCNV_DIR="$2"; shift 2 ;;
    --zipcnv-repo-url) ZIPCNV_REPO_URL="$2"; shift 2 ;;
    --zipcnv-window-bins) ZIPCNV_WINDOW_BINS="$2"; shift 2 ;;
    --zipcnv-k) ZIPCNV_K="$2"; shift 2 ;;
    --zipcnv-h-mult) ZIPCNV_H_MULT="$2"; shift 2 ;;
    --zipcnv-min-segment-bins) ZIPCNV_MIN_SEGMENT_BINS="$2"; shift 2 ;;
    --zipcnv-min-abs-log2) ZIPCNV_MIN_ABS_LOG2="$2"; shift 2 ;;
    --zipcnv-merge-gap-bins) ZIPCNV_MERGE_GAP_BINS="$2"; shift 2 ;;
    --zipcnv-compare-min-overlap) ZIPCNV_COMPARE_MIN_OVERLAP="$2"; shift 2 ;;
    --zipcnv-official-run) ZIPCNV_OFFICIAL_RUN=true; shift ;;
    --check-env-only) CHECK_ENV_ONLY=true; shift ;;
    --no-auto-fix-env) AUTO_FIX_ENV=false; shift ;;

    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

[[ "$MODE" == "ont" || "$MODE" == "illumina" || "$MODE" == "both" ]] || { echo "ERROR: --mode must be ont, illumina, or both" >&2; exit 1; }
[[ "$ZIPCNV_MODE" == "off" || "$ZIPCNV_MODE" == "adapted" || "$ZIPCNV_MODE" == "official" || "$ZIPCNV_MODE" == "both" ]] || { echo "ERROR: --zipcnv-mode must be off, adapted, official, or both" >&2; exit 1; }

# Backward-compatible alias for one historical typo in generated wrappers.
if [[ -z "${ZIPCNV_DIR:-}" && -n "${ZIPCV_DIR:-}" ]]; then
  ZIPCNV_DIR="$ZIPCV_DIR"
fi

# --purge_env is intentionally handled before any output folders or helper
# scripts are created. It only removes the workflow conda environment.
if [[ "$PURGE_ENV" == "true" ]]; then
  if [[ -f /opt/conda/etc/profile.d/conda.sh ]]; then
    # shellcheck source=/dev/null
    source /opt/conda/etc/profile.d/conda.sh
  elif [[ -f /home/server/anaconda3/etc/profile.d/conda.sh ]]; then
    # shellcheck source=/dev/null
    source /home/server/anaconda3/etc/profile.d/conda.sh
  elif [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
  elif [[ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
  fi

  if ! command -v conda >/dev/null 2>&1; then
    echo "ERROR: conda is not available; cannot purge environment '$ENV_NAME'." >&2
    exit 1
  fi

  if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    if [[ "${CONDA_DEFAULT_ENV:-}" == "$ENV_NAME" ]]; then
      echo "Currently inside '$ENV_NAME'; deactivating before removal."
      conda deactivate || true
    fi
    echo "Removing conda environment: $ENV_NAME"
    conda env remove -y -n "$ENV_NAME"
    echo "Done. Environment removed: $ENV_NAME"
  else
    echo "Environment not found; nothing to remove: $ENV_NAME"
  fi
  echo "Analysis outputs, BAMs, SAMURAI results, scripts, and ZIPcnv repository clones were not deleted."
  exit 0
fi

# Resolve workflow locations. The --purge_env branch above exits immediately,
# so normal execution always reaches this point with an analysis or environment
# check requested. Keeping this section linear avoids stale unmatched if/fi blocks
# after manual copy/paste edits.
LPWGS_ROOT="$(readlink -m "$LPWGS_ROOT")"
SCRIPTS_DIR="$LPWGS_ROOT/scripts/bam_cnv_boundary_refine"
PY_HELPER="$SCRIPTS_DIR/bam_cnv_boundary_refine.py"
ZIP_HELPER="$SCRIPTS_DIR/zipcnv_compare.py"
[[ -n "$ZIPCNV_DIR" ]] || ZIPCNV_DIR="$LPWGS_ROOT/tools/ZIPcnv"
ZIPCNV_DIR="$(readlink -m "$ZIPCNV_DIR")"
mkdir -p "$SCRIPTS_DIR"

[[ -n "$OUTDIR" ]] || OUTDIR="$LPWGS_ROOT/CNA_analyses/bam_cnv_boundary_refine_$(date +%Y%m%d)"
OUTDIR="$(readlink -m "$OUTDIR")"
mkdir -p "$OUTDIR"

# -----------------------------------------------------------------------------
# Environment setup and self-repair
# -----------------------------------------------------------------------------
# The workflow is usually run from the base shell with --skip-install after the
# first installation.  In that case we still activate the workflow environment
# when it exists.  If required Python packages are missing, the script attempts
# to repair the environment automatically unless --no-auto-fix-env is supplied.

if [[ -f /opt/conda/etc/profile.d/conda.sh ]]; then
  # shellcheck source=/dev/null
  source /opt/conda/etc/profile.d/conda.sh
elif [[ -f /home/server/anaconda3/etc/profile.d/conda.sh ]]; then
  # shellcheck source=/dev/null
  source /home/server/anaconda3/etc/profile.d/conda.sh
elif [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
  # shellcheck source=/dev/null
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif [[ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]]; then
  # shellcheck source=/dev/null
  source "$HOME/anaconda3/etc/profile.d/conda.sh"
fi

if ! command -v conda >/dev/null 2>&1; then
  echo "ERROR: conda is not available. Activate conda or install the environment manually." >&2
  exit 1
fi

# In Docker/Singularity images, /opt/conda/envs is often read-only. The image
# already includes the required BAM-refinement packages in base, so avoid
# creating a new environment inside an immutable SIF/container filesystem.
if [[ -f /opt/conda/etc/profile.d/conda.sh && ! -w /opt/conda/envs ]]; then
  echo "[INFO] Read-only container Conda detected; using preinstalled base environment." >&2
  ENV_NAME="base"
  AUTO_FIX_ENV=false
fi

if [[ "$ENV_NAME" == "base" ]]; then
  conda activate base
else
  if ! conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    if [[ "$SKIP_INSTALL" == "true" ]]; then
      echo "WARNING: --skip-install was requested, but environment '$ENV_NAME' does not exist. Creating it now." >&2
    fi
    conda create -y -n "$ENV_NAME" -c conda-forge -c bioconda \
      python=3.11 pandas numpy scipy pysam openpyxl samtools git pip
  elif [[ "$SKIP_INSTALL" != "true" ]]; then
    echo "Environment '$ENV_NAME' already exists; checking required packages."
  fi

  conda activate "$ENV_NAME"
fi

py_missing_required() {
  python - <<'PYENV'
import importlib
required = ["pandas", "numpy", "pysam", "scipy", "openpyxl"]
missing = []
for m in required:
    try:
        importlib.import_module(m)
    except Exception:
        missing.append(m)
print(",".join(missing))
PYENV
}

py_missing_optional() {
  python - <<'PYENV'
import importlib
optional = ["tqdm"]
missing = []
for m in optional:
    try:
        importlib.import_module(m)
    except Exception:
        missing.append(m)
print(",".join(missing))
PYENV
}

install_python_packages() {
  local csv="$1"
  [[ -n "$csv" ]] || return 0
  local -a mods pkgs
  IFS=',' read -r -a mods <<< "$csv"
  pkgs=()
  for m in "${mods[@]}"; do
    [[ -n "$m" ]] && pkgs+=("$m")
  done
  (( ${#pkgs[@]} > 0 )) || return 0

  echo "Attempting to install missing Python package(s): ${pkgs[*]}"
  if conda install -y -c conda-forge -c bioconda "${pkgs[@]}"; then
    return 0
  fi
  echo "WARNING: conda install failed; trying python -m pip install for: ${pkgs[*]}" >&2
  python -m pip install --no-cache-dir "${pkgs[@]}"
}

ensure_command_or_install() {
  local cmd="$1"
  local pkg="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$AUTO_FIX_ENV" == "true" ]]; then
    echo "Command '$cmd' not found; attempting to install conda package '$pkg'"
    conda install -y -c conda-forge -c bioconda "$pkg" || true
  fi
  command -v "$cmd" >/dev/null 2>&1
}

echo "Checking Python environment for BAM boundary refinement and ZIPcnv comparison"
missing_required="$(py_missing_required)"
if [[ -n "$missing_required" ]]; then
  if [[ "$AUTO_FIX_ENV" == "true" ]]; then
    echo "Missing required Python packages: $missing_required"
    install_python_packages "$missing_required"
  else
    echo "ERROR: Missing required Python packages in current environment: $missing_required" >&2
    exit 1
  fi
fi

# tqdm is optional. It is not required by the current helper code, but older
# environments may expect it. Install if possible; do not fail if unavailable.
missing_optional="$(py_missing_optional)"
if [[ -n "$missing_optional" && "$AUTO_FIX_ENV" == "true" ]]; then
  echo "Optional Python package(s) missing: $missing_optional"
  install_python_packages "$missing_optional" || echo "WARNING: optional package install failed; continuing without $missing_optional" >&2
fi

missing_required="$(py_missing_required)"
if [[ -n "$missing_required" ]]; then
  echo "ERROR: Required Python packages are still missing after repair attempt: $missing_required" >&2
  echo "Manual fix: conda activate $ENV_NAME && conda install -y -c conda-forge -c bioconda ${missing_required//,/ }" >&2
  exit 1
fi
python - <<'PYENV'
import importlib
for m in ["pandas", "numpy", "pysam", "scipy", "openpyxl"]:
    importlib.import_module(m)
print("Python package check: OK")
PYENV

if ! ensure_command_or_install samtools samtools; then
  echo "ERROR: samtools not found in environment and automatic install failed" >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  if [[ "$AUTO_FIX_ENV" == "true" ]]; then
    conda install -y -c conda-forge git || true
  fi
  command -v git >/dev/null 2>&1 || echo "WARNING: git not found; ZIPcnv repository clone will be skipped" >&2
fi

if [[ "$CHECK_ENV_ONLY" == "true" ]]; then
  echo "Environment check completed successfully."
  exit 0
fi

if [[ "$ZIPCNV_MODE" != "off" ]]; then
  mkdir -p "$(dirname "$ZIPCNV_DIR")"
  if command -v git >/dev/null 2>&1; then
    if [[ -d "$ZIPCNV_DIR/.git" ]]; then
      echo "ZIPcnv repository already present: $ZIPCNV_DIR"
      git -C "$ZIPCNV_DIR" pull --ff-only || echo "WARNING: could not update ZIPcnv repository; using existing copy" >&2
    elif [[ ! -e "$ZIPCNV_DIR" ]]; then
      echo "Cloning ZIPcnv repository into: $ZIPCNV_DIR"
      git clone "$ZIPCNV_REPO_URL" "$ZIPCNV_DIR" || echo "WARNING: ZIPcnv clone failed; adapted comparison does not require the repository" >&2
    else
      echo "WARNING: ZIPcnv path exists but is not a git clone: $ZIPCNV_DIR" >&2
    fi
  fi
fi

cat > "$PY_HELPER" <<'PY'
#!/usr/bin/env python3
import argparse
import csv
import gzip
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pysam

###############################################################################
# Generic parsing helpers
###############################################################################

def log(msg: str):
    print(msg, flush=True)


def mkdir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def clean_token(x) -> str:
    """Return a clean scalar token from SEG/BED/SAMURAI text fields.

    qDNAseq/SAMURAI segment files sometimes quote column names and sample
    identifiers, for example "ID" or "A5465_markdup".  Quoted sample
    IDs must still match unquoted BAM/bin IDs such as A5465.  This helper is
    intentionally conservative: it strips BOMs, whitespace, and wrapping quote
    characters, but it does not otherwise alter biological coordinates.
    """
    if x is None:
        return ""
    s = str(x).replace("\ufeff", "").strip()
    # Remove repeated wrapping quote characters. This handles "A", 'A', and
    # mixed cases produced by some CSV/SEG exporters.
    changed = True
    while changed and len(s) >= 2:
        changed = False
        for q in ('"', "'", '`'):
            if len(s) >= 2 and s[0] == q and s[-1] == q:
                s = s[1:-1].strip()
                changed = True
    # Also remove stray leading/trailing quotes if they are unbalanced.
    s = s.strip().strip('"').strip("'").strip('`').strip()
    return s


def norm_chrom(x) -> str:
    s = clean_token(x)
    if s == "" or s.lower() == "nan":
        return s
    s = re.sub(r"^chromosome", "", s, flags=re.I)
    s = s.replace("CHR", "chr")
    if not s.startswith("chr"):
        s = "chr" + s
    s = s.replace("chrMT", "chrM").replace("chrmt", "chrM")
    return s


def chrom_order(chrom: str) -> int:
    c = norm_chrom(chrom)
    m = re.match(r"chr(\d+)$", c)
    if m:
        return int(m.group(1))
    if c == "chrX":
        return 23
    if c == "chrY":
        return 24
    if c in ("chrM", "chrMT"):
        return 25
    return 100


def strip_bam_suffix(name: str) -> str:
    s = Path(clean_token(name)).name
    for suf in [".bam", ".cram"]:
        if s.endswith(suf):
            s = s[:-len(suf)]
    for suf in [".sorted", "_sorted", ".markdup", "_markdup", ".dedup", "_dedup", ".filt", "_filt"]:
        if s.endswith(suf):
            s = s[:-len(suf)]
    return s


def canonical_sample_name(x) -> str:
    """Normalize sample identifiers across SAMURAI/qDNAseq/ichorCNA/BAM files.

    qDNAseq and SAMURAI files may name the same sample as A5465,
    A5465_markdup, A5465_markdup_filt, A5465.calls, etc.  For boundary
    refinement these should be treated as the same sample.
    """
    s = clean_token(x)
    s = Path(s).name
    s = clean_token(s)
    # Drop common file extensions first.
    for suf in [".correctedDepth.txt.gz", ".correctedDepth.txt", ".calls.seg", ".seg.txt", ".seg", ".bed.gz", ".bed", ".rds", ".bam", ".cram"]:
        if s.endswith(suf):
            s = s[:-len(suf)]
    # Drop common SAMURAI/qDNAseq processing suffixes. Repeat because some
    # names contain more than one suffix, e.g. A5465_markdup_filt.
    suffixes = [
        "_markdup_bins", "_bins", "_reads_filtered",
        "_markdup_filt", "_markdup", "_filt",
        ".markdup.filt", ".markdup", ".filt",
        "_sorted", ".sorted", "_dedup", ".dedup",
        "_calls", ".calls", "_segments", ".segments"
    ]
    changed = True
    while changed:
        changed = False
        for suf in suffixes:
            if s.endswith(suf):
                s = s[:-len(suf)]
                changed = True
    return s


def guess_sep(path: Path) -> str:
    with open_text(path) as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                return "\t" if "\t" in line else r"\s+"
    return "\t"


def open_text(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt")
    return open(path, "rt")


def first_data_line(path: Path) -> str:
    with open_text(path) as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                return line.rstrip("\n")
    return ""


def has_header(path: Path) -> bool:
    line = first_data_line(path)
    if not line:
        return True
    toks = re.split(r"\t|\s+", line.strip())
    lower = [t.lower() for t in toks]
    header_tokens = {"chrom", "chr", "chromosome", "start", "end", "loc.start", "loc.end", "sample", "id", "seg.mean", "adj.seg"}
    if any(t in header_tokens for t in lower):
        return True
    # If first token is chr1 and second/third numeric, it is likely no header.
    if len(toks) >= 3 and re.match(r"^(chr)?[0-9XYM]+$", toks[0], re.I):
        try:
            int(float(toks[1])); int(float(toks[2]))
            return False
        except Exception:
            pass
    return True


def split_fields(line: str) -> List[str]:
    # BED/SEG files from qDNAseq/SAMURAI can be tab-delimited or whitespace-delimited.
    # Use a conservative whitespace splitter after removing outer whitespace.
    return re.split(r"\t+|\s+", line.strip())


def is_non_data_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    low = s.lower()
    if low.startswith("#"):
        return True
    # UCSC BED files sometimes start with one-column track/browser declarations.
    # These were the source of the pandas ParserError in Illumina qDNAseq BEDs.
    if low.startswith("track ") or low.startswith("browser "):
        return True
    return False


def header_tokens(toks: List[str]) -> bool:
    lower = [str(t).lower() for t in toks]
    known = {
        "chrom", "chr", "chromosome", "seqnames", "seqname",
        "start", "end", "loc.start", "loc.end", "sample", "id",
        "seg.mean", "adj.seg", "final_log2", "log2", "logr", "num.mark"
    }
    if any(t in known for t in lower):
        return True
    # A normal BED data line starts chr/number, start, end.
    if len(toks) >= 3 and re.match(r"^(chr)?[0-9xymt]+$", str(toks[0]), re.I):
        try:
            int(float(toks[1])); int(float(toks[2]))
            return False
        except Exception:
            pass
    # No chromosome-looking first field but contains text: treat as header.
    return True


def make_unique_columns(cols: List[str]) -> List[str]:
    out = []
    seen = {}
    for i, c in enumerate(cols):
        name = clean_token(c) if clean_token(c) else f"V{i+1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        out.append(name)
    return out


def read_table(path: Path) -> pd.DataFrame:
    """Robust small/medium tabular reader for SEG/BED/qDNAseq/ichorCNA outputs.

    Avoids pandas C-engine tokenization failures caused by files with leading
    one-field UCSC lines such as `track name=...`, or mixed whitespace. It also
    tolerates extra BED columns by padding shorter rows.
    """
    rows = []
    skipped_leading = 0
    with open_text(path) as fh:
        for line in fh:
            if is_non_data_line(line):
                skipped_leading += 1
                continue
            toks = [clean_token(t) for t in split_fields(line)]
            # Skip malformed leading/non-tabular lines with fewer than chrom/start/end.
            if len(toks) < 3:
                skipped_leading += 1
                continue
            rows.append(toks)
    if not rows:
        raise ValueError(f"No tabular data lines with >=3 fields were found in {path}")

    has_hdr = header_tokens(rows[0])
    if has_hdr:
        hdr = rows[0]
        data = rows[1:]
    else:
        hdr = []
        data = rows

    if not data:
        max_len = len(hdr) if hdr else 0
        return pd.DataFrame(columns=make_unique_columns(hdr if hdr else [f"V{i+1}" for i in range(max_len)]))

    max_len = max([len(r) for r in data] + ([len(hdr)] if hdr else []))
    if has_hdr:
        cols = hdr + [f"V{i+1}" for i in range(len(hdr), max_len)]
    else:
        cols = [f"V{i+1}" for i in range(max_len)]
    cols = make_unique_columns(cols[:max_len])

    padded = [r + [None] * (max_len - len(r)) if len(r) < max_len else r[:max_len] for r in data]
    df = pd.DataFrame(padded, columns=cols)
    df.columns = [clean_token(c) for c in df.columns]
    if skipped_leading:
        log(f"Read {path.name}: skipped {skipped_leading} non-tabular/header/comment line(s); rows={df.shape[0]}, columns={df.shape[1]}")
    return df


def find_col(df: pd.DataFrame, aliases: List[str], required: bool = False) -> Optional[str]:
    low_map = {clean_token(c).lower(): c for c in df.columns}
    for a in aliases:
        if a.lower() in low_map:
            return low_map[a.lower()]
    # punctuation-insensitive match
    def canon(x):
        return re.sub(r"[^a-z0-9]", "", clean_token(x).lower())
    canon_map = {canon(c): c for c in df.columns}
    for a in aliases:
        ca = canon(a)
        if ca in canon_map:
            return canon_map[ca]
    if required:
        raise ValueError(f"Could not find any of {aliases}; columns={list(df.columns)}")
    return None


def numeric_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def choose_value_col(df: pd.DataFrame, exclude: List[str]) -> str:
    aliases = [
        "final_log2", "value", "adj.seg", "adj_seg", "adjSeg", "seg.mean", "seg_mean", "Segment_Mean", "segment_mean",
        "corrected.seg", "corrected_seg", "log2", "log2ratio", "log2_ratio", "log2_TNratio_corrected", "log2_TNratio", "logR", "log.r", "copy.ratio", "copy_ratio",
        "copynumber", "copy.number", "cn", "CN", "ratio", "median", "mean"
    ]
    c = find_col(df, aliases)
    if c is not None and c not in exclude:
        return c
    candidates = []
    for col in df.columns:
        if col in exclude:
            continue
        vals = pd.to_numeric(df[col], errors="coerce")
        frac = vals.notna().mean()
        if frac > 0.7:
            # prefer non-coordinate numeric columns with non-trivial variation
            candidates.append((col, frac, float(np.nanstd(vals.values))))
    if not candidates:
        raise ValueError(f"Cannot identify a numeric copy-number/log2 column. Columns={list(df.columns)}")
    # choose highest variation numeric column among candidates; if bed with V4, this catches V4
    candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return candidates[0][0]

###############################################################################
# Input readers
###############################################################################

def standardize_bin_df(df: pd.DataFrame, sample: str, source_file: Path) -> pd.DataFrame:
    chrom_col = find_col(df, ["chrom", "chr", "chromosome", "seqnames", "seqname", "V1"], required=True)
    start_col = find_col(df, ["start", "loc.start", "loc_start", "begin", "V2"], required=True)
    end_col = find_col(df, ["end", "loc.end", "loc_end", "stop", "V3"], required=True)
    exclude = [chrom_col, start_col, end_col]
    value_col = choose_value_col(df, exclude)
    out = pd.DataFrame({
        "sample": canonical_sample_name(sample),
        "chrom": df[chrom_col].map(norm_chrom),
        "start": numeric_series(df[start_col]).astype("Int64"),
        "end": numeric_series(df[end_col]).astype("Int64"),
        "input_log2": numeric_series(df[value_col]),
        "source_file": str(source_file),
        "source_value_column": value_col,
    })
    out = out.dropna(subset=["chrom", "start", "end"])
    out["start"] = out["start"].astype(int)
    out["end"] = out["end"].astype(int)
    out = out[out["end"] > out["start"]].copy()
    out["chrom_order"] = out["chrom"].map(chrom_order)
    out = out.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])
    return out


def read_ichorcna_bins(input_dir: Path) -> pd.DataFrame:
    files = sorted(input_dir.glob("*.correctedDepth.txt")) + sorted(input_dir.glob("*.correctedDepth.txt.gz"))
    if not files:
        raise FileNotFoundError(f"No *.correctedDepth.txt files found in {input_dir}")
    frames = []
    for f in files:
        sample = f.name.replace(".correctedDepth.txt.gz", "").replace(".correctedDepth.txt", "")
        log(f"Reading ichorCNA bins: {sample} <- {f}")
        frames.append(standardize_bin_df(read_table(f), sample, f))
    return pd.concat(frames, ignore_index=True)


def read_qdnaseq_bins(input_dir: Path) -> pd.DataFrame:
    bin_dir = input_dir / "bins"
    files = []
    if bin_dir.exists():
        files.extend(sorted(bin_dir.glob("*_markdup_bins.bed")))
        files.extend(sorted(bin_dir.glob("*_markdup_bins.bed.gz")))
        files.extend(sorted(bin_dir.glob("*_bins.bed")))
        files.extend(sorted(bin_dir.glob("*_bins.bed.gz")))
    if not files:
        files.extend(sorted(input_dir.glob("*_markdup_bins.bed")))
        files.extend(sorted(input_dir.glob("*_markdup_bins.bed.gz")))
        files.extend(sorted(input_dir.glob("*_bins.bed")))
        files.extend(sorted(input_dir.glob("*_bins.bed.gz")))
    if not files:
        raise FileNotFoundError(
            f"No qDNAseq bin BED files found in {input_dir}/bins. Expected files like *_markdup_bins.bed or *_bins.bed."
        )
    frames = []
    for f in files:
        sample = re.sub(r"_(?:markdup_)?bins\.bed(\.gz)?$", "", f.name)
        log(f"Reading qDNAseq bins: {sample} <- {f}")
        frames.append(standardize_bin_df(read_table(f), sample, f))
    return pd.concat(frames, ignore_index=True)


def read_prior_segments(path: Path, samples: List[str]) -> pd.DataFrame:
    if not path or str(path).lower() in ["none", "na", ""]:
        raise FileNotFoundError("A prior segmentation file is required for this boundary-refinement workflow.")
    df = read_table(path)
    sample_col = find_col(df, ["sample", "ID", "id", "Sample", "sample_id"], required=True)
    chrom_col = find_col(df, ["chrom", "chromosome", "chr", "Chromosome"], required=True)
    start_col = find_col(df, ["start", "loc.start", "loc_start", "Start"], required=True)
    end_col = find_col(df, ["end", "loc.end", "loc_end", "End"], required=True)
    value_col = choose_value_col(df, [sample_col, chrom_col, start_col, end_col])
    num_col = find_col(df, ["num.mark", "num_mark", "num.markers", "markers", "num_probes", "n_bins"])
    log(f"Prior segment columns: sample={sample_col}, chrom={chrom_col}, start={start_col}, end={end_col}, value={value_col}, num_mark={num_col or 'not_detected'}")
    raw_sample = df[sample_col].map(clean_token).astype(str)
    out = pd.DataFrame({
        "sample": raw_sample.map(canonical_sample_name),
        "sample_raw": raw_sample,
        "chrom": df[chrom_col].map(norm_chrom),
        "start": numeric_series(df[start_col]).astype("Int64"),
        "end": numeric_series(df[end_col]).astype("Int64"),
        "seg_log2": numeric_series(df[value_col]),
        "prior_file": str(path),
        "prior_value_column": value_col,
    })
    if num_col is not None:
        out["num_mark"] = numeric_series(df[num_col])
    else:
        out["num_mark"] = np.nan
    out = out.dropna(subset=["sample", "chrom", "start", "end", "seg_log2"])
    out["start"] = out["start"].astype(int)
    out["end"] = out["end"].astype(int)
    out = out[out["end"] > out["start"]].copy()
    # keep only samples with bins, but report if mismatches exist
    have = set(samples)
    extra = sorted(set(out["sample"]) - have)
    missing = sorted(have - set(out["sample"]))
    if extra:
        log(f"WARNING: prior has samples not present in bins after normalization; ignoring: {extra[:10]}{'...' if len(extra)>10 else ''}")
    if missing:
        log(f"WARNING: bins have samples not present in prior segmentation after normalization: {missing[:10]}{'...' if len(missing)>10 else ''}")
    matched = sorted(set(out["sample"]).intersection(have))
    log(f"Prior/bin sample matches after normalization: {len(matched)} sample(s)")
    if matched:
        log(f"Matched examples: {matched[:10]}{'...' if len(matched)>10 else ''}")
    out = out[out["sample"].isin(have)].copy()
    out["chrom_order"] = out["chrom"].map(chrom_order)
    out = out.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])
    return out

###############################################################################
# BAM handling and coverage
###############################################################################

def run_cmd(cmd: List[str]):
    log("Running: " + " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)


def list_bams(dirs_csv: str) -> Dict[str, Path]:
    result = {}
    if not dirs_csv:
        return result
    for d in re.split(r",|;", dirs_csv):
        d = d.strip()
        if not d:
            continue
        p = Path(d)
        if not p.exists():
            log(f"WARNING: BAM directory does not exist: {p}")
            continue
        for bam in sorted(list(p.glob("*.bam")) + list(p.glob("*.cram"))):
            result[canonical_sample_name(strip_bam_suffix(bam.name))] = bam
    return result


def bam_sort_order(path: Path) -> str:
    try:
        with pysam.AlignmentFile(str(path), "rb") as bam:
            return bam.header.to_dict().get("HD", {}).get("SO", "unknown")
    except Exception:
        return "unknown"


def has_bam_index(path: Path) -> bool:
    return Path(str(path) + ".bai").exists() or Path(str(path) + ".csi").exists() or path.with_suffix(path.suffix + ".bai").exists()


def prepare_bam(path: Path, sample: str, outdir: Path) -> Tuple[Path, dict]:
    """Return a coordinate-sorted/indexed BAM and a preparation record.

    The input BAM is used directly when it is already coordinate sorted. A
    prepared-BAM directory is created only when a sorted copy is necessary.
    """
    order = bam_sort_order(path)
    record = {
        "sample": sample,
        "input_bam": str(path),
        "input_sort_order": order,
        "used_bam": str(path),
        "action": "used_original_bam",
        "prepared_bam_dir": "not_created",
        "index_action": "index_already_present" if has_bam_index(path) else "index_missing_at_start",
    }
    if order == "coordinate":
        if not has_bam_index(path):
            run_cmd(["samtools", "index", str(path)])
            record["action"] = "indexed_original_bam"
            record["index_action"] = "created_index_next_to_original_bam"
        return path, record

    mkdir(outdir)
    out = outdir / f"{sample}.sorted.bam"
    record["prepared_bam_dir"] = str(outdir)
    record["used_bam"] = str(out)
    record["action"] = "created_sorted_indexed_bam_copy"
    if not out.exists():
        run_cmd(["samtools", "sort", "-o", str(out), str(path)])
    if not has_bam_index(out):
        run_cmd(["samtools", "index", str(out)])
    record["index_action"] = "prepared_bam_index_present"
    return out, record


def resolve_bam_chrom(bam: pysam.AlignmentFile, chrom: str) -> Optional[str]:
    refs = set(bam.references)
    c = norm_chrom(chrom)
    if c in refs:
        return c
    c2 = c.replace("chr", "", 1)
    if c2 in refs:
        return c2
    if c == "chrM" and "MT" in refs:
        return "MT"
    if c == "chrM" and "M" in refs:
        return "M"
    return None


def fine_intervals(start: int, end: int, fine_bp: int, max_bins: int) -> List[Tuple[int, int]]:
    start = max(0, int(start))
    end = max(start + fine_bp, int(end))
    n = math.ceil((end - start) / fine_bp)
    if n > max_bins:
        fine_bp = math.ceil((end - start) / max_bins)
        n = math.ceil((end - start) / fine_bp)
    intervals = []
    x = start
    while x < end:
        y = min(end, x + fine_bp)
        if y > x:
            intervals.append((x, y))
        x = y
    return intervals


def add_overlap_to_bins(arr: np.ndarray, intervals: List[Tuple[int, int]], a: int, b: int, value: float = 1.0):
    """Accumulate overlap-weighted coverage into local intervals.

    The implementation intentionally avoids mixed-indentation nested blocks so the
    generated helper remains robust when copied between shells/editors.
    """
    if b <= intervals[0][0] or a >= intervals[-1][1]:
        return
    # Local windows are small; a simple explicit loop is sufficient and clear.
    for i, (s, e) in enumerate(intervals):
        if e <= a:
            continue
        if s >= b:
            break
        ov = max(0, min(e, b) - max(s, a))
        arr[i] += value * ov


def count_bam_coverage(path: Path, chrom: str, intervals: List[Tuple[int, int]], mode: str,
                       min_mapq: int, include_duplicates: bool, include_secondary: bool,
                       include_supplementary: bool) -> np.ndarray:
    arr = np.zeros(len(intervals), dtype=float)
    start, end = intervals[0][0], intervals[-1][1]
    with pysam.AlignmentFile(str(path), "rb") as bam:
        bam_chrom = resolve_bam_chrom(bam, chrom)
        if bam_chrom is None:
            return np.full(len(intervals), np.nan)
        try:
            it = bam.fetch(bam_chrom, start, end)
        except ValueError:
            return np.full(len(intervals), np.nan)
        for read in it:
            if read.is_unmapped:
                continue
            if read.mapping_quality < min_mapq:
                continue
            if read.is_duplicate and not include_duplicates:
                continue
            if read.is_secondary and not include_secondary:
                continue
            if read.is_supplementary and not include_supplementary:
                continue
            if read.reference_start is None or read.reference_end is None:
                continue
            if mode == "starts":
                pos = read.reference_start
                if start <= pos < end:
                    idx = np.searchsorted([iv[1] for iv in intervals], pos, side="right")
                    if 0 <= idx < len(arr) and intervals[idx][0] <= pos < intervals[idx][1]:
                        arr[idx] += 1.0
            else:
                # Use aligned blocks for bases, robust for ONT CIGARs.
                try:
                    blocks = read.get_blocks()
                except Exception:
                    blocks = [(read.reference_start, read.reference_end)]
                for a, b in blocks:
                    add_overlap_to_bins(arr, intervals, a, b, value=1.0)
    return arr

###############################################################################
# Boundary refinement statistics
###############################################################################

def compute_signal(tumor_counts: np.ndarray, normal_counts: Optional[np.ndarray]) -> np.ndarray:
    counts = tumor_counts.astype(float)
    if normal_counts is not None and normal_counts.size > 0 and not np.all(np.isnan(normal_counts)):
        mat = normal_counts.astype(float)
        # normalize each normal by its local median positive count to reduce library size effects
        norm_rows = []
        for row in mat:
            pos = row[np.isfinite(row) & (row > 0)]
            sf = np.median(pos) if len(pos) else 1.0
            if not np.isfinite(sf) or sf <= 0:
                sf = 1.0
            norm_rows.append(row / sf)
        matn = np.vstack(norm_rows)
        pon = np.nanmedian(matn, axis=0)
        pos_t = counts[np.isfinite(counts) & (counts > 0)]
        sf_t = np.median(pos_t) if len(pos_t) else 1.0
        if not np.isfinite(sf_t) or sf_t <= 0:
            sf_t = 1.0
        t = counts / sf_t
        pc = max(0.01, np.nanmedian(pon[np.isfinite(pon)]) * 0.01) if np.any(np.isfinite(pon)) else 0.01
        return np.log2((t + pc) / (pon + pc))
    else:
        pos = counts[np.isfinite(counts) & (counts > 0)]
        med = np.median(pos) if len(pos) else 1.0
        if not np.isfinite(med) or med <= 0:
            med = 1.0
        pc = max(0.5, med * 0.01)
        return np.log2((counts + pc) / (med + pc))


def split_stats(y: np.ndarray, min_side: int):
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y)
    # require candidates on original full vector; candidate-specific finite filtering
    n = len(y)
    if n < 2 * min_side:
        return None
    y_all = y[ok]
    if len(y_all) < 2 * min_side:
        return None
    mean0 = np.nanmean(y)
    sse0 = np.nansum((y - mean0) ** 2)
    if sse0 <= 0 or not np.isfinite(sse0):
        sse0 = 1e-9
    candidates = []
    for k in range(min_side, n - min_side + 1):
        left = y[:k]
        right = y[k:]
        left = left[np.isfinite(left)]
        right = right[np.isfinite(right)]
        if len(left) < min_side or len(right) < min_side:
            continue
        ml = float(np.mean(left)); mr = float(np.mean(right))
        ssel = float(np.sum((left - ml) ** 2))
        sser = float(np.sum((right - mr) ** 2))
        sse1 = max(ssel + sser, 1e-9)
        neff = len(left) + len(right)
        bic_gain = neff * math.log(max(sse0 / max(len(y_all), 1), 1e-9)) - neff * math.log(max(sse1 / max(neff, 1), 1e-9)) - math.log(max(neff, 2))
        diff = mr - ml
        candidates.append({"k": k, "left_mean": ml, "right_mean": mr, "diff": diff, "abs_diff": abs(diff), "bic_gain": bic_gain})
    if not candidates:
        return None
    best = sorted(candidates, key=lambda d: (d["bic_gain"], d["abs_diff"]), reverse=True)[0]
    return best, candidates


def permutation_pvalue(y: np.ndarray, observed_bic: float, min_side: int, nperm: int, seed: int = 17) -> float:
    if nperm <= 0:
        return np.nan
    rng = np.random.default_rng(seed)
    yy = np.asarray(y, dtype=float)
    finite = yy[np.isfinite(yy)]
    if len(finite) < 2 * min_side:
        return np.nan
    count = 0
    total = 0
    for _ in range(nperm):
        perm = yy.copy()
        perm[np.isfinite(perm)] = rng.permutation(finite)
        st = split_stats(perm, min_side)
        if st is None:
            continue
        best, _ = st
        total += 1
        if best["bic_gain"] >= observed_bic:
            count += 1
    if total == 0:
        return np.nan
    return (count + 1) / (total + 1)


def candidate_ci(candidates: List[dict], intervals: List[Tuple[int, int]], best_bic: float) -> Tuple[int, int, int]:
    """Return a simple BIC-support confidence interval for a local CN boundary.

    The coordinate of each candidate split is the start coordinate of the
    right-hand fine window.  Candidates within 2 BIC units of the best split
    define the support interval.  This function is intentionally written with
    simple, explicit control flow because the helper is generated from a Bash
    wrapper and must remain robust to editor/copy indentation artifacts.
    """
    if not candidates or not intervals:
        return 0, 0, 0

    kept_candidates = [c for c in candidates if float(c.get("bic_gain", -np.inf)) >= float(best_bic) - 2.0]
    if not kept_candidates:
        kept_candidates = [max(candidates, key=lambda d: float(d.get("bic_gain", -np.inf)))]

    coords = []
    last_end = int(intervals[-1][1])
    n_intervals = len(intervals)
    for cand in kept_candidates:
        split_index = int(cand.get("k", n_intervals))
        coord = int(intervals[split_index][0]) if 0 <= split_index < n_intervals else last_end
        coords.append(coord)

    if not coords:
        coords = [last_end]
    ci_start = int(min(coords))
    ci_end = int(max(coords))
    ci_width = int(max(0, ci_end - ci_start))
    return ci_start, ci_end, ci_width

###############################################################################
# Segment/boundary transformations
###############################################################################

def assign_cna_state(x: float, gain_thr: float, loss_thr: float) -> str:
    if pd.isna(x):
        return "unknown"
    if x >= gain_thr:
        return "gain"
    if x <= loss_thr:
        return "loss"
    return "neutral"


def empty_boundary_stats() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "sample", "chrom", "boundary_index", "left_segment_start", "left_segment_end",
        "right_segment_start", "right_segment_end", "original_boundary", "refined_boundary",
        "final_boundary", "boundary_shift_bp", "left_seg_log2", "right_seg_log2",
        "adjacent_seg_delta", "adjacent_seg_abs_delta", "eligible_for_refinement",
        "coverage_resolution_status", "fine_bin_kb", "n_fine_bins", "n_left_bins",
        "n_right_bins", "local_log2_diff", "best_bic_gain", "empirical_p",
        "ci_start", "ci_end", "ci_width_bp", "final_decision", "decision_reason",
        "refinement_source"
    ])


def segments_from_bins(bins: pd.DataFrame, source: str = "input_bins_no_prior_segmentation") -> pd.DataFrame:
    """Fallback final segments when no usable prior segment table is available.

    This does not invent segmentation. It preserves the input bins as the final
    downstream representation so the workflow can still emit SAMURAI-compatible
    files and a clear report.
    """
    rows = []
    if bins.empty:
        return pd.DataFrame(columns=["sample", "chrom", "start", "end", "num_mark", "seg_log2", "cna_state", "final_source"])
    for (sample, chrom), g in bins.groupby(["sample", "chrom"], sort=False):
        g = g.sort_values(["start", "end"])
        # Merge consecutive input bins only when they have exactly the same log2
        # after rounding; otherwise preserve individual bins. This is a fallback,
        # not a new CNA caller.
        current = None
        for _, b in g.iterrows():
            val = b.get("input_log2", np.nan)
            val_round = None if pd.isna(val) else round(float(val), 6)
            if current is None:
                current = {
                    "sample": sample, "chrom": chrom, "start": int(b["start"]), "end": int(b["end"]),
                    "num_mark": 1, "seg_log2": float(val) if not pd.isna(val) else np.nan,
                    "_val_round": val_round
                }
            elif current["_val_round"] == val_round and int(b["start"]) <= int(current["end"]):
                current["end"] = max(int(current["end"]), int(b["end"]))
                current["num_mark"] += 1
            else:
                current["cna_state"] = assign_cna_state(current["seg_log2"], args_global.state_gain_threshold, args_global.state_loss_threshold)
                current["final_source"] = source
                current.pop("_val_round", None)
                rows.append(current)
                current = {
                    "sample": sample, "chrom": chrom, "start": int(b["start"]), "end": int(b["end"]),
                    "num_mark": 1, "seg_log2": float(val) if not pd.isna(val) else np.nan,
                    "_val_round": val_round
                }
        if current is not None:
            current["cna_state"] = assign_cna_state(current["seg_log2"], args_global.state_gain_threshold, args_global.state_loss_threshold)
            current["final_source"] = source
            current.pop("_val_round", None)
            rows.append(current)
    out = pd.DataFrame(rows)
    if not out.empty:
        out["chrom_order"] = out["chrom"].map(chrom_order)
        out = out.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])
    return out


def build_boundaries(prior: pd.DataFrame, min_adjacent_delta: float) -> pd.DataFrame:
    rows = []
    for (sample, chrom), g in prior.groupby(["sample", "chrom"], sort=False):
        g = g.sort_values(["start", "end"]).reset_index(drop=True)
        for i in range(len(g) - 1):
            left = g.iloc[i]
            right = g.iloc[i + 1]
            coarse_boundary = int(round((int(left["end"]) + int(right["start"])) / 2))
            delta = float(right["seg_log2"] - left["seg_log2"])
            rows.append({
                "sample": sample,
                "chrom": chrom,
                "boundary_index": i + 1,
                "left_segment_start": int(left["start"]),
                "left_segment_end": int(left["end"]),
                "right_segment_start": int(right["start"]),
                "right_segment_end": int(right["end"]),
                "original_boundary": coarse_boundary,
                "left_seg_log2": float(left["seg_log2"]),
                "right_seg_log2": float(right["seg_log2"]),
                "adjacent_seg_delta": delta,
                "adjacent_seg_abs_delta": abs(delta),
                "eligible_for_refinement": abs(delta) >= min_adjacent_delta,
            })
    return pd.DataFrame(rows)


def apply_final_boundaries(prior: pd.DataFrame, bstats: pd.DataFrame) -> pd.DataFrame:
    boundary_map = {}
    if not bstats.empty:
        for _, r in bstats.iterrows():
            boundary_map[(r["sample"], r["chrom"], int(r["boundary_index"]))] = int(r["final_boundary"])
    out_rows = []
    for (sample, chrom), g in prior.groupby(["sample", "chrom"], sort=False):
        g = g.sort_values(["start", "end"]).reset_index(drop=True)
        starts = [int(x) for x in g["start"]]
        ends = [int(x) for x in g["end"]]
        sources = ["prior_segmentation"] * len(g)
        for i in range(len(g) - 1):
            key = (sample, chrom, i + 1)
            if key in boundary_map:
                fb = boundary_map[key]
                ends[i] = fb
                starts[i + 1] = fb
                row = bstats[(bstats["sample"] == sample) & (bstats["chrom"] == chrom) & (bstats["boundary_index"] == i + 1)]
                if not row.empty and row.iloc[0]["final_decision"] == "refined_boundary":
                    sources[i] = "bam_refined_boundary"
                    sources[i + 1] = "bam_refined_boundary"
        for i, row in g.iterrows():
            if ends[i] <= starts[i]:
                continue
            out_rows.append({
                "sample": sample,
                "chrom": chrom,
                "start": int(starts[i]),
                "end": int(ends[i]),
                "num_mark": row.get("num_mark", np.nan),
                "seg_log2": float(row["seg_log2"]),
                "cna_state": assign_cna_state(float(row["seg_log2"]), args_global.state_gain_threshold, args_global.state_loss_threshold),
                "final_source": sources[i],
            })
    if not out_rows:
        return pd.DataFrame(columns=["sample", "chrom", "start", "end", "num_mark", "seg_log2", "cna_state", "final_source"])
    segs = pd.DataFrame(out_rows)
    if not segs.empty:
        segs["chrom_order"] = segs["chrom"].map(chrom_order)
        segs = segs.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])
    return segs


def overlay_bins_with_segments(bins: pd.DataFrame, segs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if segs is None or segs.empty or "sample" not in segs.columns or "chrom" not in segs.columns:
        seg_lookup = {}
    else:
        seg_lookup = {(s, c): g.sort_values(["start", "end"]).reset_index(drop=True) for (s, c), g in segs.groupby(["sample", "chrom"], sort=False)}
    for (sample, chrom), bg in bins.groupby(["sample", "chrom"], sort=False):
        sg = seg_lookup.get((sample, chrom))
        if sg is None or sg.empty:
            # no prior; keep original bins
            for _, b in bg.iterrows():
                rows.append({
                    "sample": sample, "chrom": chrom, "start": int(b["start"]), "end": int(b["end"]),
                    "input_log2": b.get("input_log2", np.nan), "final_log2": b.get("input_log2", np.nan),
                    "final_source": "input_bins_no_prior_segment", "cna_state": assign_cna_state(b.get("input_log2", np.nan), args_global.state_gain_threshold, args_global.state_loss_threshold),
                    "original_bin_start": int(b["start"]), "original_bin_end": int(b["end"]),
                })
            continue
        for _, b in bg.sort_values(["start", "end"]).iterrows():
            bs, be = int(b["start"]), int(b["end"])
            overlaps = sg[(sg["end"] > bs) & (sg["start"] < be)]
            if overlaps.empty:
                rows.append({
                    "sample": sample, "chrom": chrom, "start": bs, "end": be,
                    "input_log2": b.get("input_log2", np.nan), "final_log2": b.get("input_log2", np.nan),
                    "final_source": "input_bins_no_segment_overlap", "cna_state": assign_cna_state(b.get("input_log2", np.nan), args_global.state_gain_threshold, args_global.state_loss_threshold),
                    "original_bin_start": bs, "original_bin_end": be,
                })
            else:
                for _, s in overlaps.iterrows():
                    os = max(bs, int(s["start"])); oe = min(be, int(s["end"]))
                    if oe <= os:
                        continue
                    rows.append({
                        "sample": sample, "chrom": chrom, "start": os, "end": oe,
                        "input_log2": b.get("input_log2", np.nan), "final_log2": float(s["seg_log2"]),
                        "final_source": s["final_source"], "cna_state": s["cna_state"],
                        "original_bin_start": bs, "original_bin_end": be,
                    })
    out = pd.DataFrame(rows)
    if not out.empty:
        out["chrom_order"] = out["chrom"].map(chrom_order)
        out = out.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])
    return out

###############################################################################
# Main boundary refinement
###############################################################################

def refine_one_boundary(row, sample_bams, normal_bams, args, prepared_dir: Path) -> dict:
    sample = row["sample"]
    chrom = row["chrom"]
    original_boundary = int(row["original_boundary"])
    coarse_bp = int(args.coarse_binsize_kb * 1000)
    fine_bp = int(args.fine_bin_kb * 1000)
    search_radius = int(args.search_radius_bp) if args.search_radius_bp and int(args.search_radius_bp) > 0 else int(args.search_radius_bins * coarse_bp)
    search_start = max(0, original_boundary - search_radius)
    search_end = original_boundary + search_radius

    base = {
        "sample": sample,
        "chrom": chrom,
        "boundary_index": int(row["boundary_index"]),
        "left_segment_start": int(row["left_segment_start"]),
        "left_segment_end": int(row["left_segment_end"]),
        "right_segment_start": int(row["right_segment_start"]),
        "right_segment_end": int(row["right_segment_end"]),
        "original_boundary": original_boundary,
        "refined_boundary": np.nan,
        "final_boundary": original_boundary,
        "boundary_shift_bp": 0,
        "left_seg_log2": float(row["left_seg_log2"]),
        "right_seg_log2": float(row["right_seg_log2"]),
        "adjacent_seg_delta": float(row["adjacent_seg_delta"]),
        "adjacent_seg_abs_delta": float(row["adjacent_seg_abs_delta"]),
        "search_start": search_start,
        "search_end": search_end,
        "fine_bin_bp": fine_bp,
        "n_fine_bins": 0,
        "n_valid_fine_bins": 0,
        "left_median_coverage_units": np.nan,
        "right_median_coverage_units": np.nan,
        "median_coverage_units": np.nan,
        "used_pon": False,
        "best_bic_gain": np.nan,
        "best_left_mean_log2": np.nan,
        "best_right_mean_log2": np.nan,
        "best_local_log2_diff": np.nan,
        "best_abs_local_log2_diff": np.nan,
        "permutation_p_boundary_support": np.nan,
        "ci_start": np.nan,
        "ci_end": np.nan,
        "ci_width_bp": np.nan,
        "coverage_resolution_status": "not_evaluated",
        "final_decision": "kept_original_binning",
        "decision_reason": "not_evaluated",
    }

    if not bool(row["eligible_for_refinement"]):
        base["coverage_resolution_status"] = "not_attempted"
        base["decision_reason"] = "adjacent_prior_segments_have_small_log2_difference"
        return base

    bam = sample_bams.get(sample)
    if bam is None:
        base["coverage_resolution_status"] = "missing_bam"
        base["decision_reason"] = "no_matching_bam_for_sample"
        return base

    intervals = fine_intervals(search_start, search_end, fine_bp, int(args.max_fine_bins_per_window))
    base["n_fine_bins"] = len(intervals)
    if len(intervals) < int(args.min_valid_fine_bins):
        base["coverage_resolution_status"] = "poor_bam_resolution"
        base["decision_reason"] = "too_few_fine_bins_in_search_window"
        return base

    tumor_counts = count_bam_coverage(
        bam, chrom, intervals, args.coverage_mode, int(args.min_mapq),
        args.include_duplicates, args.include_secondary, args.include_supplementary
    )
    if np.all(~np.isfinite(tumor_counts)):
        base["coverage_resolution_status"] = "missing_chromosome_in_bam"
        base["decision_reason"] = "chromosome_not_found_in_sample_bam"
        return base

    normal_counts = None
    if args.pon_mode in ("auto", "on") and normal_bams:
        mats = []
        for nbam in normal_bams.values():
            nc = count_bam_coverage(
                nbam, chrom, intervals, args.coverage_mode, int(args.min_mapq),
                args.include_duplicates, args.include_secondary, args.include_supplementary
            )
            if np.any(np.isfinite(nc)):
                mats.append(nc)
        if mats:
            normal_counts = np.vstack(mats)
            base["used_pon"] = True
        elif args.pon_mode == "on":
            base["coverage_resolution_status"] = "poor_bam_resolution"
            base["decision_reason"] = "pon_requested_but_no_usable_normal_bam_coverage"
            return base

    y = compute_signal(tumor_counts, normal_counts)
    valid = np.isfinite(y)
    base["n_valid_fine_bins"] = int(np.sum(valid))
    base["median_coverage_units"] = float(np.nanmedian(tumor_counts)) if np.any(np.isfinite(tumor_counts)) else np.nan

    left_cov = []
    right_cov = []
    for c, (s, e) in zip(tumor_counts, intervals):
        mid = (s + e) / 2
        if mid <original_boundary:
            left_cov.append(c)
        else:
            right_cov.append(c)
    base["left_median_coverage_units"] = float(np.nanmedian(left_cov)) if left_cov else np.nan
    base["right_median_coverage_units"] = float(np.nanmedian(right_cov)) if right_cov else np.nan

    if base["n_valid_fine_bins"] < int(args.min_valid_fine_bins):
        base["coverage_resolution_status"] = "poor_bam_resolution"
        base["decision_reason"] = "too_few_valid_fine_bins_after_bam_counting"
        return base
    if not np.isfinite(base["median_coverage_units"]) or base["median_coverage_units"] < float(args.min_median_cover_units):
        base["coverage_resolution_status"] = "poor_bam_resolution"
        base["decision_reason"] = "median_bam_coverage_units_below_threshold"
        return base
    if (np.isfinite(base["left_median_coverage_units"]) and base["left_median_coverage_units"] < float(args.min_side_cover_units)) or \
       (np.isfinite(base["right_median_coverage_units"]) and base["right_median_coverage_units"] < float(args.min_side_cover_units)):
        base["coverage_resolution_status"] = "poor_bam_resolution"
        base["decision_reason"] = "one_side_of_boundary_has_insufficient_bam_coverage"
        return base

    st = split_stats(y, int(args.min_side_fine_bins))
    if st is None:
        base["coverage_resolution_status"] = "poor_bam_resolution"
        base["decision_reason"] = "cannot_evaluate_local_changepoint_with_available_fine_bins"
        return base
    best, candidates = st
    k = int(best["k"])
    refined = intervals[k][0] if k < len(intervals) else intervals[-1][1]
    ci_start, ci_end, ci_width = candidate_ci(candidates, intervals, best["bic_gain"])
    pval = permutation_pvalue(y, best["bic_gain"], int(args.min_side_fine_bins), int(args.permutations))

    base.update({
        "refined_boundary": int(refined),
        "boundary_shift_bp": int(refined - original_boundary),
        "best_bic_gain": float(best["bic_gain"]),
        "best_left_mean_log2": float(best["left_mean"]),
        "best_right_mean_log2": float(best["right_mean"]),
        "best_local_log2_diff": float(best["diff"]),
        "best_abs_local_log2_diff": float(abs(best["diff"])),
        "permutation_p_boundary_support": float(pval) if np.isfinite(pval) else np.nan,
        "ci_start": int(ci_start),
        "ci_end": int(ci_end),
        "ci_width_bp": int(ci_width),
        "coverage_resolution_status": "usable_bam_resolution",
    })

    # Acceptance is about whether the BAM supports moving the boundary; it is not a test of whether the CNV exists.
    reasons = []
    accept = True
    if abs(best["diff"]) < float(args.min_local_log2_diff):
        accept = False; reasons.append("local_bam_log2_step_below_threshold")
    if best["bic_gain"] < float(args.min_bic_gain):
        accept = False; reasons.append("bic_gain_below_threshold")
    if int(args.permutations) > 0 and args.accept_rule in ("p_and_bic", "permissive"):
        if not np.isfinite(pval) or pval > float(args.permutation_p):
            if args.accept_rule == "p_and_bic":
                accept = False; reasons.append("empirical_boundary_support_p_above_threshold")
    max_ci = int(args.max_ci_bp) if int(args.max_ci_bp) > 0 else int(float(args.max_ci_fraction_of_coarse) * coarse_bp)
    if max_ci > 0 and ci_width > max_ci:
        accept = False; reasons.append("boundary_confidence_interval_too_wide")
    if abs(refined - original_boundary) < int(args.min_shift_bp):
        accept = False; reasons.append("refined_boundary_same_as_original_or_shift_too_small")

    if args.accept_rule == "bic_only":
        # Ignore permutation p entirely, keep all other quality checks.
        reasons = [r for r in reasons if r != "empirical_boundary_support_p_above_threshold"]
        accept = (abs(best["diff"]) >= float(args.min_local_log2_diff) and best["bic_gain"] >= float(args.min_bic_gain)
                  and (max_ci <= 0 or ci_width <= max_ci) and abs(refined - original_boundary) >= int(args.min_shift_bp))
    elif args.accept_rule == "permissive":
        # Accept if BIC and local step pass, even if p is borderline, unless CI is too wide.
        accept = (abs(best["diff"]) >= float(args.min_local_log2_diff) and best["bic_gain"] >= float(args.min_bic_gain)
                  and (max_ci <= 0 or ci_width <= max_ci) and abs(refined - original_boundary) >= int(args.min_shift_bp))

    if accept:
        base["final_boundary"] = int(refined)
        base["final_decision"] = "refined_boundary"
        base["decision_reason"] = "bam_local_coverage_supports_boundary_shift"
    else:
        base["final_boundary"] = original_boundary
        base["final_decision"] = "kept_original_binning"
        base["decision_reason"] = ";".join(reasons) if reasons else "boundary_shift_not_supported"
    return base

###############################################################################
# Outputs
###############################################################################

def write_outputs(outdir: Path, bins: pd.DataFrame, prior: pd.DataFrame, bstats: pd.DataFrame, final_segments: pd.DataFrame, refined_bins: pd.DataFrame, args):
    tables = outdir / "01_tables"
    compat = outdir / "02_samurai_compatible"
    final_results = outdir / "04_final_results"
    cna_input = final_results / "cna_cytogenomic_input"
    cna_bins = cna_input / "qdnaseq_bins"
    mkdir(tables); mkdir(compat); mkdir(final_results); mkdir(cna_input); mkdir(cna_bins); mkdir(compat / "bins"); mkdir(compat / "bins_headered"); mkdir(compat / "segments")

    if bstats is None or bstats.empty:
        bstats = empty_boundary_stats()
    if final_segments is None or final_segments.empty:
        final_segments = segments_from_bins(bins, source="input_bins_no_final_segments_available")
    if refined_bins is None or refined_bins.empty:
        refined_bins = overlay_bins_with_segments(bins, final_segments)

    refined_bins.to_csv(tables / "refined_bins.tsv.gz", sep="\t", index=False, compression="gzip")
    final_segments.to_csv(tables / "final_segments.tsv", sep="\t", index=False)
    final_segments_bed = final_segments.copy()
    final_segments_bed["chrom_order"] = final_segments_bed["chrom"].map(chrom_order)
    final_segments_bed = final_segments_bed.sort_values(["chrom_order", "start", "end", "sample"]).drop(columns=["chrom_order"])
    final_segments_bed_cols = [c for c in ["chrom", "start", "end", "sample", "seg_log2", "cna_state", "final_source", "num_mark"] if c in final_segments_bed.columns]
    final_segments_bed[final_segments_bed_cols].to_csv(tables / "final_segments.bed", sep="\t", index=False, header=False)

    shutil.copy2(tables / "final_segments.tsv", final_results / "final_segments.tsv")
    shutil.copy2(tables / "final_segments.bed", final_results / "final_segments.bed")

    refined_bins_bp = refined_bins.copy()
    refined_bins_bp["boundary_bp_difference"] = 0
    refined_bins_bp["abs_boundary_bp_difference"] = 0
    refined_bins_bp["refined_boundary_original_position"] = pd.NA
    refined_bins_bp["refined_boundary_final_position"] = pd.NA
    refined_bins_bp["boundary_refinement_note"] = "no_boundary_refinement_accepted"
    if bstats is not None and not bstats.empty and "final_decision" in bstats.columns:
        accepted = bstats[bstats["final_decision"] == "refined_boundary"].copy()
        if not accepted.empty:
            refined_bins_bp["boundary_refinement_note"] = "no_refined_boundary_touching_bin"
            for _, br in accepted.iterrows():
                sample = br.get("sample")
                chrom = br.get("chrom")
                original_boundary = br.get("original_boundary", pd.NA)
                final_boundary = br.get("final_boundary", br.get("refined_boundary", pd.NA))
                if pd.isna(final_boundary):
                    continue
                final_boundary_int = int(round(float(final_boundary)))
                if pd.isna(original_boundary):
                    shift = int(round(float(br.get("boundary_shift_bp", 0) or 0)))
                else:
                    shift = int(final_boundary_int - int(round(float(original_boundary))))
                mask = (
                    (refined_bins_bp["sample"] == sample)
                    & (refined_bins_bp["chrom"] == chrom)
                    & ((refined_bins_bp["start"] == final_boundary_int) | (refined_bins_bp["end"] == final_boundary_int))
                )
                refined_bins_bp.loc[mask, "boundary_bp_difference"] = shift
                refined_bins_bp.loc[mask, "abs_boundary_bp_difference"] = abs(shift)
                refined_bins_bp.loc[mask, "refined_boundary_original_position"] = original_boundary
                refined_bins_bp.loc[mask, "refined_boundary_final_position"] = final_boundary_int
                refined_bins_bp.loc[mask, "boundary_refinement_note"] = "bin_touches_refined_final_boundary"
    refined_bins_bp.to_csv(final_results / "refined_bins_boundary_bp_difference.csv", index=False)
    refined_bins_bp.to_excel(final_results / "refined_bins_boundary_bp_difference.xlsx", index=False, engine="openpyxl")

    bstats.to_csv(tables / "boundary_refinement_statistics.csv", index=False)

    # sample summary
    summaries = []
    for sample in sorted(refined_bins["sample"].unique()):
        bs = bstats[bstats["sample"] == sample]
        n_bound = len(bs)
        n_refined = int((bs["final_decision"] == "refined_boundary").sum()) if n_bound else 0
        n_kept = int((bs["final_decision"] == "kept_original_binning").sum()) if n_bound else 0
        n_poor = int((bs["coverage_resolution_status"] == "poor_bam_resolution").sum()) if n_bound else 0
        fallback = n_refined == 0
        if n_bound == 0:
            status = "no_prior_boundaries_to_refine"
        elif fallback:
            status = "fallback_to_prior_segmentation_for_entire_sample"
        else:
            status = "bam_refined_one_or_more_boundaries"
        summaries.append({
            "sample": sample,
            "n_prior_boundaries_evaluated": n_bound,
            "n_boundaries_refined": n_refined,
            "n_boundaries_kept_original": n_kept,
            "n_boundaries_with_poor_bam_resolution": n_poor,
            "sample_level_result": status,
            "user_interpretation": "Existing SAMURAI/qDNAseq/ichorCNA binning was retained for this sample" if fallback else "At least one prior CNA segment boundary was moved using BAM-supported local coverage evidence",
        })
    pd.DataFrame(summaries).to_csv(tables / "sample_refinement_summary.csv", index=False)

    # compatibility segments
    gistic = final_segments.rename(columns={"sample": "ID", "start": "loc.start", "end": "loc.end", "seg_log2": "seg.mean"})
    gistic = gistic[["ID", "chrom", "loc.start", "loc.end", "num_mark", "seg.mean"]].copy()
    gistic = gistic.rename(columns={"chrom": "chrom", "num_mark": "num.mark"})
    gistic["num.mark"] = pd.to_numeric(gistic["num.mark"], errors="coerce")
    # recalculate num.mark if missing
    if gistic["num.mark"].isna().any():
        counts = refined_bins.groupby(["sample", "chrom", "final_log2"]).size()
        gistic["num.mark"] = gistic["num.mark"].fillna(1).astype(int)
    gistic["chrom_order"] = gistic["chrom"].map(chrom_order)
    gistic = gistic.sort_values(["ID", "chrom_order", "loc.start", "loc.end"]).drop(columns=["chrom_order"])
    gistic.to_csv(compat / "all_segments.seg", sep="\t", index=False)
    gistic.to_csv(compat / "bam_boundary_refined_gistic.seg", sep="\t", index=False)

    ichor_style = final_segments.rename(columns={"chrom": "chromosome", "seg_log2": "adj.seg", "num_mark": "num.mark"})
    ichor_style = ichor_style[["sample", "chromosome", "start", "end", "num.mark", "adj.seg", "final_source", "cna_state"]].copy()
    ichor_style["chrom_order"] = ichor_style["chromosome"].map(chrom_order)
    ichor_style = ichor_style.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])
    ichor_style.to_csv(compat / "segments_logR_corrected_gistic.seg", sep="\t", index=False)

    # per sample segments and bins
    bed_manifest = []
    cna_manifest = []
    cytogenomic_manifest = []
    for sample, rb in refined_bins.groupby("sample", sort=True):
        rb = rb.copy()
        rb["chrom_order"] = rb["chrom"].map(chrom_order)
        rb = rb.sort_values(["chrom_order", "start", "end"]).drop(columns=["chrom_order"])
        bed = rb[["chrom", "start", "end", "final_log2"]]
        bed_file = compat / "bins" / f"{sample}_markdup_bins.bed"
        bed.to_csv(bed_file, sep="\t", index=False, header=False)
        header_file = compat / "bins_headered" / f"{sample}_bam_boundary_refined_bins.bed"
        rb.to_csv(header_file, sep="\t", index=False)
        bed_manifest.append({"sample": sample, "file": str(bed_file)})

        cytobed = rb[[c for c in ["chrom", "start", "end", "final_log2", "input_log2"] if c in rb.columns]].copy()
        cytobed["codification_log2"] = pd.to_numeric(cytobed["final_log2"], errors="coerce")
        if "input_log2" in cytobed.columns:
            cytobed["codification_log2"] = cytobed["codification_log2"].fillna(pd.to_numeric(cytobed["input_log2"], errors="coerce"))
        cytobed["codification_log2"] = cytobed["codification_log2"].fillna(0.0)
        cytobed["bed_start"] = (pd.to_numeric(cytobed["start"], errors="coerce").fillna(0).astype(int) - 1).clip(lower=0)
        cytobed["name"] = cytobed["chrom"].astype(str) + ":" + cytobed["start"].astype(str) + "-" + cytobed["end"].astype(str)
        cytobed["strand"] = "."
        cytobed_file = cna_bins / f"{sample}_markdup_bins.bed"
        cytobed_file.write_text(f"track name=\"{sample}_markdup\" description=\"bam_boundary_refined_final_log2\"\n")
        cytobed[["chrom", "bed_start", "end", "name", "codification_log2", "strand"]].to_csv(cytobed_file, sep="\t", index=False, header=False, mode="a")
        cytogenomic_manifest.append({"sample": sample, "file": str(cytobed_file), "format": "qdnaseq_bed", "log2_column": 5, "coordinate_system": "BED_0_based_start_1_based_end"})

        sg = final_segments[final_segments["sample"] == sample].copy()
        sfile = compat / "segments" / f"{sample}.calls.seg"
        sg_g = sg.rename(columns={"sample": "ID", "start": "loc.start", "end": "loc.end", "seg_log2": "seg.mean", "num_mark": "num.mark"})
        sg_g = sg_g[["ID", "chrom", "loc.start", "loc.end", "num.mark", "seg.mean"]]
        sg_g["chrom_order"] = sg_g["chrom"].map(chrom_order)
        sg_g = sg_g.sort_values(["ID", "chrom_order", "loc.start", "loc.end"]).drop(columns=["chrom_order"])
        sg_g.to_csv(sfile, sep="\t", index=False)
        cna_manifest.append({"sample": sample, "file": str(sfile)})

    pd.DataFrame(bed_manifest).to_csv(compat / "input_bed_files.tsv", sep="\t", index=False)
    pd.DataFrame(cna_manifest).to_csv(compat / "input_cna_files.tsv", sep="\t", index=False)
    pd.DataFrame(cytogenomic_manifest).to_csv(cna_input / "input_bed_files.tsv", sep="\t", index=False)
    cytogenomic_out = cna_input / "cytogenomic_notation"
    run_script = cna_input / "run_cna_codification.sh"
    run_script.write_text(f"""#!/usr/bin/env bash
set -Eeuo pipefail
mkdir -p "{cytogenomic_out}"
python /media/server/STORAGE/LPWGS_2025/cna_codification/scripts/cna_to_cytogenomic_notation.py \
  --input_dir "{cna_bins}" \
  --cytoband /media/server/STORAGE/LPWGS_2025/cna_codification/resources/hg38.cytoBand.txt.gz \
  --outdir "{cytogenomic_out}" \
  --genome-label GRCh38 \
  --prefix seq \
  --loss -0.30 \
  --gain 0.25 \
  --deep-loss -1.00 \
  --amp 0.70 \
  --min-bins 3 \
  --min-mb 1.0 \
  --max-gap-bp 500000 \
  --qdnaseq
""")
    run_script.chmod(0o755)

    readme = compat / "README.txt"
    readme.write_text(
        "BAM-supported CNA boundary refinement outputs\n"
        "=============================================\n\n"
        "This folder contains downstream-compatible files only.\n"
        "The workflow does not test whether CNA regions are statistically significant.\n"
        "It only tests whether existing prior CNA segment boundaries can be moved using local BAM coverage.\n"
        "If BAM resolution is insufficient, original SAMURAI/qDNAseq/ichorCNA binning is retained.\n\n"
        "Key files:\n"
        "  all_segments.seg: GISTIC/SAMURAI-like final segments.\n"
        "  ../01_tables/final_segments.bed: BED-style final segments matching final_segments.tsv.\n"
        "  segments_logR_corrected_gistic.seg: ichorCNA-like final segments.\n"
        "  bins/<sample>_markdup_bins.bed: final bins, chrom/start/end/final_log2, no header.\n"
        "  ../01_tables/boundary_refinement_statistics.csv: per-boundary evidence and fallback decisions.\n"
        "  ../01_tables/bam_preparation_report.csv: whether BAMs were used as-is, indexed, or sorted.\n"
        "  ../critical_outputs_manifest.csv: machine-readable index of critical outputs.\n"
        "Temporary sorted BAM copies, if needed, are placed under ../_work/prepared_bams/.\n"
    )

    final_readme = final_results / "README.txt"
    final_readme.write_text(
        "Final primary CNA outputs\n"
        "=========================\n\n"
        "This folder collects the main final segment outputs and the refined-bin boundary-shift report.\n"
        "final_segments.tsv is the headered primary segment table.\n"
        "final_segments.bed is the BED-style companion: chrom, start, end, sample, seg_log2, cna_state, final_source, num_mark.\n"
        "refined_bins_boundary_bp_difference.csv and .xlsx contain refined bins plus boundary_bp_difference.\n"
        "boundary_bp_difference is 0 for bins not touching an accepted refined boundary, including samples with no accepted refinement.\n"        "cna_cytogenomic_input/qdnaseq_bins contains converter-ready BED bins for both ONT and Illumina; use run_cna_codification.sh to run cna_to_cytogenomic_notation.py with --qdnaseq.\n"
    )
    pd.DataFrame([
        {"dataset": args.dataset_name, "category": "final_segments", "path": "04_final_results/final_segments.tsv", "description": "Headered primary final segment table."},
        {"dataset": args.dataset_name, "category": "final_segments_bed", "path": "04_final_results/final_segments.bed", "description": "BED-style primary final segment table."},
        {"dataset": args.dataset_name, "category": "refined_bins_boundary_shift", "path": "04_final_results/refined_bins_boundary_bp_difference.csv", "description": "Refined bins with boundary_bp_difference in bp; zero when no accepted refined boundary touches the bin."},
        {"dataset": args.dataset_name, "category": "refined_bins_boundary_shift", "path": "04_final_results/refined_bins_boundary_bp_difference.xlsx", "description": "Excel copy of refined bins with boundary_bp_difference in bp."},
        {"dataset": args.dataset_name, "category": "cna_cytogenomic_input", "path": "04_final_results/cna_cytogenomic_input/qdnaseq_bins/", "description": "Per-sample qDNAseq-style BED bins with final_log2 in column 5 for cna_to_cytogenomic_notation.py --qdnaseq; valid for ONT and Illumina refined outputs."},
        {"dataset": args.dataset_name, "category": "cna_cytogenomic_input", "path": "04_final_results/cna_cytogenomic_input/run_cna_codification.sh", "description": "Runnable command for CNA-to-cytogenomic-notation conversion using the converter-ready BED bins."},
    ]).to_csv(final_results / "final_results_manifest.csv", index=False)

    manifest_rows = [
        {"dataset": args.dataset_name, "category": "statistics", "path": "01_tables/boundary_refinement_statistics.csv", "description": "Per-boundary evidence, accepted coordinate if refined, fallback decision, local coverage contrast, BIC gain, and confidence interval width."},
        {"dataset": args.dataset_name, "category": "summary", "path": "01_tables/sample_refinement_summary.csv", "description": "Per-sample counts of evaluated boundaries, BAM-supported refinements, retained prior boundaries, and poor-resolution fallbacks."},
        {"dataset": args.dataset_name, "category": "bam_qc", "path": "01_tables/bam_preparation_report.csv", "description": "Whether each BAM was used in place, indexed, or copied/sorted into _work/prepared_bams."},
        {"dataset": args.dataset_name, "category": "bins", "path": "01_tables/refined_bins.tsv.gz", "description": "Headered final bin table; coarse bins are split only where a BAM-supported boundary refinement was accepted."},
        {"dataset": args.dataset_name, "category": "segments", "path": "01_tables/final_segments.tsv", "description": "Headered final segment table after supported boundary refinements and fallback to prior segmentation."},
        {"dataset": args.dataset_name, "category": "segments_bed", "path": "01_tables/final_segments.bed", "description": "BED-style final segment table: chrom, start, end, sample, seg_log2, cna_state, final_source, num_mark."},
        {"dataset": args.dataset_name, "category": "final_results", "path": "04_final_results/final_segments.tsv", "description": "Copied primary final segment table for direct review."},
        {"dataset": args.dataset_name, "category": "final_results", "path": "04_final_results/final_segments.bed", "description": "Copied BED-style primary final segment table for direct review."},
        {"dataset": args.dataset_name, "category": "final_results", "path": "04_final_results/refined_bins_boundary_bp_difference.csv", "description": "Refined bins with boundary_bp_difference in bp; zero when no accepted refined boundary touches the bin."},
        {"dataset": args.dataset_name, "category": "final_results", "path": "04_final_results/refined_bins_boundary_bp_difference.xlsx", "description": "Excel copy of refined bins with boundary_bp_difference in bp."},
        {"dataset": args.dataset_name, "category": "final_results", "path": "04_final_results/cna_cytogenomic_input/qdnaseq_bins/", "description": "Converter-ready qDNAseq-style BED bins with final_log2 in column 5 for cna_to_cytogenomic_notation.py --qdnaseq."},
        {"dataset": args.dataset_name, "category": "final_results", "path": "04_final_results/cna_cytogenomic_input/run_cna_codification.sh", "description": "Runnable CNA-to-cytogenomic-notation command for the converter-ready BED bins."},
        {"dataset": args.dataset_name, "category": "samurai_compatible_segments", "path": "02_samurai_compatible/all_segments.seg", "description": "GISTIC/SAMURAI-like final segment file for downstream analyses."},
        {"dataset": args.dataset_name, "category": "samurai_compatible_segments", "path": "02_samurai_compatible/segments_logR_corrected_gistic.seg", "description": "ichorCNA/SAMURAI-like final segment file with adj.seg values."},
        {"dataset": args.dataset_name, "category": "samurai_compatible_bins", "path": "02_samurai_compatible/bins/", "description": "Per-sample no-header BED files: chrom, start, end, final_log2."},
        {"dataset": args.dataset_name, "category": "samurai_compatible_segments", "path": "02_samurai_compatible/segments/", "description": "Per-sample .calls.seg files."},
    ]
    pd.DataFrame(manifest_rows).to_csv(outdir / "critical_outputs_manifest.csv", index=False)

###############################################################################
# Dataset runner
###############################################################################

args_global = None


def refine_dataset(args):
    input_dir = Path(args.input_dir)
    outdir = Path(args.outdir)
    mkdir(outdir)
    mkdir(outdir / "01_tables")
    mkdir(outdir / "02_samurai_compatible")

    if args.caller == "ichorcna":
        bins = read_ichorcna_bins(input_dir)
    elif args.caller == "qdnaseq":
        bins = read_qdnaseq_bins(input_dir)
    else:
        raise ValueError(f"Unsupported caller: {args.caller}")
    log(f"Loaded {len(bins)} bins for {bins['sample'].nunique()} samples")
    samples = sorted(bins["sample"].unique())
    prior = read_prior_segments(Path(args.prior_seg), samples)
    if prior.empty:
        log("WARNING: No prior segments matched the bin-level samples after sample-name normalization. The workflow will retain input bins and report fallback instead of aborting.")
    else:
        log(f"Loaded {len(prior)} prior segments for {prior['sample'].nunique()} samples")

    all_bams = list_bams(args.bam_dirs)
    if not all_bams:
        raise FileNotFoundError(f"No BAM files found in {args.bam_dirs}")
    sample_bams = {}
    for s in samples:
        if s in all_bams:
            sample_bams[s] = all_bams[s]
        else:
            # relaxed matching
            candidates = [p for name, p in all_bams.items() if name == s or name.startswith(s) or s.startswith(name)]
            if candidates:
                sample_bams[s] = candidates[0]
    missing = sorted(set(samples) - set(sample_bams))
    if missing:
        log(f"WARNING: missing BAMs for samples; these samples will fall back to prior segmentation: {missing}")

    # Normal BAMs for local PON correction. In auto mode, BAMs not matched to tumor/bin samples are normals.
    normal_bams = {}
    if args.normal_samples == "none" or args.pon_mode == "off":
        normal_bams = {}
    elif args.normal_samples == "auto":
        for name, path in all_bams.items():
            if name not in samples:
                normal_bams[name] = path
        if args.normal_bam_dirs:
            normal_bams.update(list_bams(args.normal_bam_dirs))
    else:
        names = [x.strip() for x in re.split(r",|;", args.normal_samples) if x.strip()]
        for n in names:
            if n in all_bams:
                normal_bams[n] = all_bams[n]
        if args.normal_bam_dirs:
            normal_bams.update(list_bams(args.normal_bam_dirs))
    log(f"Matched sample BAMs: {len(sample_bams)}; normal/PON BAMs: {len(normal_bams)}")

    prepared_dir = outdir / "_work" / "prepared_bams"
    bam_prep_records = []
    prepared_sample_bams = {}
    for s, p in sample_bams.items():
        used, rec = prepare_bam(p, s, prepared_dir)
        rec["role"] = "sample"
        bam_prep_records.append(rec)
        prepared_sample_bams[s] = used
    prepared_normal_bams = {}
    for s, p in normal_bams.items():
        used, rec = prepare_bam(p, s, prepared_dir)
        rec["role"] = "normal_or_pon"
        bam_prep_records.append(rec)
        prepared_normal_bams[s] = used
    sample_bams = prepared_sample_bams
    normal_bams = prepared_normal_bams
    if prepared_dir.exists() and not any(prepared_dir.iterdir()):
        try:
            prepared_dir.rmdir()
        except OSError:
            pass

    boundaries = build_boundaries(prior, float(args.min_adjacent_seg_delta)) if not prior.empty else pd.DataFrame()
    log(f"Prior boundaries to evaluate: {len(boundaries)}")
    rows = []
    for idx, row in boundaries.iterrows():
        if idx % 25 == 0:
            log(f"Evaluating boundary {idx+1}/{len(boundaries)}")
        rows.append(refine_one_boundary(row, sample_bams, normal_bams, args, prepared_dir))
    bstats = pd.DataFrame(rows) if rows else empty_boundary_stats()

    if prior.empty:
        final_segments = segments_from_bins(bins, source="input_bins_no_prior_segmentation")
        refined_bins = overlay_bins_with_segments(bins, final_segments)
    else:
        final_segments = apply_final_boundaries(prior, bstats)
        if final_segments.empty:
            log("WARNING: prior segmentation did not yield final segments; falling back to input bins.")
            final_segments = segments_from_bins(bins, source="input_bins_no_final_segments_available")
        refined_bins = overlay_bins_with_segments(bins, final_segments)
    write_outputs(outdir, bins, prior, bstats, final_segments, refined_bins, args)
    pd.DataFrame(bam_prep_records).to_csv(outdir / "01_tables" / "bam_preparation_report.csv", index=False)

    n_ref = int((bstats["final_decision"] == "refined_boundary").sum()) if not bstats.empty else 0
    n_keep = int((bstats["final_decision"] == "kept_original_binning").sum()) if not bstats.empty else 0
    log(f"Done. Refined boundaries: {n_ref}; kept original boundaries: {n_keep}")
    log(f"Critical statistics: {outdir / '01_tables' / 'boundary_refinement_statistics.csv'}")
    log(f"Refined bins:       {outdir / '01_tables' / 'refined_bins.tsv.gz'}")
    log(f"SAMURAI segments:   {outdir / '02_samurai_compatible' / 'all_segments.seg'}")
    log(f"SAMURAI bins:       {outdir / '02_samurai_compatible' / 'bins'}")


def main():
    global args_global
    p = argparse.ArgumentParser()
    p.add_argument("--dataset-name", required=True)
    p.add_argument("--input-dir", required=True)
    p.add_argument("--caller", choices=["ichorcna", "qdnaseq"], required=True)
    p.add_argument("--platform", choices=["ONT", "illumina"], required=True)
    p.add_argument("--bam-dirs", required=True)
    p.add_argument("--prior-seg", required=True)
    p.add_argument("--outdir", required=True)
    p.add_argument("--coarse-binsize-kb", type=float, required=True)
    p.add_argument("--fine-bin-kb", type=float, required=True)
    p.add_argument("--search-radius-bins", type=int, default=2)
    p.add_argument("--search-radius-bp", type=int, default=0)
    p.add_argument("--min-side-fine-bins", type=int, default=3)
    p.add_argument("--min-valid-fine-bins", type=int, default=8)
    p.add_argument("--min-local-log2-diff", type=float, default=0.10)
    p.add_argument("--min-adjacent-seg-delta", type=float, default=0.10)
    p.add_argument("--min-bic-gain", type=float, default=6.0)
    p.add_argument("--permutations", type=int, default=300)
    p.add_argument("--permutation-p", type=float, default=0.05)
    p.add_argument("--accept-rule", choices=["p_and_bic", "bic_only", "permissive"], default="p_and_bic")
    p.add_argument("--max-ci-bp", type=int, default=0)
    p.add_argument("--max-ci-fraction-of-coarse", type=float, default=1.0)
    p.add_argument("--min-shift-bp", type=int, default=1)
    p.add_argument("--min-median-cover-units", type=float, default=1.0)
    p.add_argument("--min-side-cover-units", type=float, default=1.0)
    p.add_argument("--max-fine-bins-per-window", type=int, default=400)
    p.add_argument("--min-mapq", type=int, default=20)
    p.add_argument("--coverage-mode", choices=["bases", "starts"], default="bases")
    p.add_argument("--normal-samples", default="auto")
    p.add_argument("--normal-bam-dirs", default="")
    p.add_argument("--pon-mode", choices=["auto", "on", "off"], default="auto")
    p.add_argument("--include-duplicates", action="store_true")
    p.add_argument("--include-supplementary", action="store_true")
    p.add_argument("--include-secondary", action="store_true")
    p.add_argument("--state-gain-threshold", type=float, default=0.25)
    p.add_argument("--state-loss-threshold", type=float, default=-0.25)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    args_global = args
    refine_dataset(args)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
PY
# Normalize helper indentation defensively and fail early if generated Python is invalid.
python - "$PY_HELPER" <<'PYCHECK'
from pathlib import Path
import py_compile
import sys
path = Path(sys.argv[1])
text = path.read_text()
# Convert hard tabs to spaces inside the generated helper to avoid editor/copy
# indentation artifacts on shared systems.
text = text.expandtabs(4)
path.write_text(text)
py_compile.compile(str(path), doraise=True)
print(f"Python helper syntax check: OK ({path})", flush=True)
PYCHECK
chmod +x "$PY_HELPER"


cat > "$ZIP_HELPER" <<'PYZIP'
#!/usr/bin/env python3
"""ZIPcnv-adapted comparison for BAM boundary-refined CNA outputs.

The official ZIPcnv implementation is designed around base-level depth arrays and
large normal baselines. For SAMURAI qDNAseq/ichorCNA outputs, this helper applies
an adapted CUSUM detector to the already normalized bin-level log2 signal and
compares the resulting candidate CNV regions with the BAM-refined final segments.
"""
import argparse
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


def log(msg: str):
    print(msg, flush=True)


def mkdir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def clean_token(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return str(x).strip().strip('"').strip("'").strip('`').strip()


def norm_chrom(x) -> str:
    s = clean_token(x)
    if not s:
        return s
    s = re.sub(r"^chromosome", "", s, flags=re.I)
    if not s.startswith("chr"):
        s = "chr" + s
    return s.replace("chrMT", "chrM").replace("chrmt", "chrM")


def chrom_order(chrom: str) -> int:
    c = norm_chrom(chrom)
    m = re.match(r"chr(\d+)$", c)
    if m:
        return int(m.group(1))
    if c == "chrX":
        return 23
    if c == "chrY":
        return 24
    if c in ("chrM", "chrMT"):
        return 25
    return 100


def state_from_log2(x: float, gain_thr: float, loss_thr: float) -> str:
    try:
        v = float(x)
    except Exception:
        return "neutral"
    if np.isnan(v):
        return "neutral"
    if v >= gain_thr:
        return "gain"
    if v <= loss_thr:
        return "loss"
    return "neutral"


def read_tsv(path: Path) -> pd.DataFrame:
    if str(path).endswith(".gz"):
        return pd.read_csv(path, sep="\t", low_memory=False, compression="gzip")
    return pd.read_csv(path, sep="\t", low_memory=False)


def safe_numeric(s):
    return pd.to_numeric(s, errors="coerce")


def rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    if len(arr) == 0:
        return arr
    window = max(1, int(window))
    return pd.Series(arr).rolling(window=window, center=True, min_periods=max(1, window // 2)).mean().to_numpy()


def cusum_scores(y: np.ndarray, k: float):
    up = np.zeros(len(y), dtype=float)
    down = np.zeros(len(y), dtype=float)
    for i, v in enumerate(y):
        if not np.isfinite(v):
            v = 0.0
        if i == 0:
            up[i] = max(0.0, v - k)
            down[i] = min(0.0, v + k)
        else:
            up[i] = max(0.0, v - k + up[i - 1])
            down[i] = min(0.0, v + k + down[i - 1])
    return up, down


def merge_short_gaps(states, merge_gap_bins: int):
    states = list(states)
    n = len(states)
    if n == 0 or merge_gap_bins <= 0:
        return states
    i = 0
    while i < n:
        if states[i] != "neutral":
            i += 1
            continue
        j = i
        while j < n and states[j] == "neutral":
            j += 1
        gap_len = j - i
        left = states[i - 1] if i > 0 else None
        right = states[j] if j < n else None
        if gap_len <= merge_gap_bins and left in ("gain", "loss") and left == right:
            for k in range(i, j):
                states[k] = left
        i = j
    return states


def segments_from_states(bg: pd.DataFrame, states, smooth, up, down, args):
    rows = []
    n = len(bg)
    i = 0
    while i < n:
        st = states[i]
        j = i + 1
        while j < n and states[j] == st:
            j += 1
        if st in ("gain", "loss") and (j - i) >= args.zipcnv_min_segment_bins:
            sub = bg.iloc[i:j]
            vals = safe_numeric(sub["zip_input_log2"]).to_numpy(dtype=float)
            med = float(np.nanmedian(vals)) if np.isfinite(vals).any() else np.nan
            if np.isfinite(med) and abs(med) >= args.zipcnv_min_abs_log2:
                rows.append({
                    "sample": sub["sample"].iloc[0],
                    "chrom": sub["chrom"].iloc[0],
                    "start": int(sub["start"].iloc[0]),
                    "end": int(sub["end"].iloc[-1]),
                    "n_bins": int(j - i),
                    "zipcnv_state": st,
                    "zipcnv_median_log2": med,
                    "zipcnv_mean_log2": float(np.nanmean(vals)) if np.isfinite(vals).any() else np.nan,
                    "zipcnv_smooth_mean": float(np.nanmean(smooth[i:j])) if len(smooth[i:j]) else np.nan,
                    "zipcnv_cusum_peak": float(np.nanmax(up[i:j]) if st == "gain" else abs(np.nanmin(down[i:j]))),
                    "zipcnv_window_bins": int(args.zipcnv_window_bins),
                    "zipcnv_k": float(args.zipcnv_k),
                    "zipcnv_h_threshold": float(args.zipcnv_h),
                    "method": "ZIPcnv_adapted_bin_CUSUM",
                })
        i = j
    return rows


def run_zipcnv_adapted(refined_bins: pd.DataFrame, args) -> pd.DataFrame:
    df = refined_bins.copy()
    df["sample"] = df["sample"].map(clean_token)
    df["chrom"] = df["chrom"].map(norm_chrom)
    df["start"] = safe_numeric(df["start"]).astype("Int64")
    df["end"] = safe_numeric(df["end"]).astype("Int64")
    if "input_log2" in df.columns:
        df["zip_input_log2"] = safe_numeric(df["input_log2"])
    elif "final_log2" in df.columns:
        df["zip_input_log2"] = safe_numeric(df["final_log2"])
    else:
        raise ValueError("refined_bins.tsv.gz must contain input_log2 or final_log2")
    df["chrom_order"] = df["chrom"].map(chrom_order)
    df = df.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])
    rows = []
    for (_, _), bg in df.groupby(["sample", "chrom"], sort=False):
        bg = bg.sort_values(["start", "end"]).reset_index(drop=True)
        y = safe_numeric(bg["zip_input_log2"]).to_numpy(dtype=float)
        if len(y) < max(2, args.zipcnv_min_segment_bins):
            continue
        med = np.nanmedian(y) if np.isfinite(y).any() else 0.0
        y = np.where(np.isfinite(y), y, med)
        smooth = rolling_mean(y, args.zipcnv_window_bins)
        smooth = np.where(np.isfinite(smooth), smooth, y)
        baseline = np.nanmedian(smooth) if np.isfinite(smooth).any() else 0.0
        z = smooth - baseline
        up, down = cusum_scores(z, args.zipcnv_k)
        states = []
        for zi, ui, di in zip(z, up, down):
            if ((ui >= args.zipcnv_h) and (zi > args.zipcnv_k)) or (zi >= args.zipcnv_min_abs_log2):
                states.append("gain")
            elif ((di <= -args.zipcnv_h) and (zi < -args.zipcnv_k)) or (zi <= -args.zipcnv_min_abs_log2):
                states.append("loss")
            else:
                states.append("neutral")
        states = merge_short_gaps(states, args.zipcnv_merge_gap_bins)
        rows.extend(segments_from_states(bg, states, smooth, up, down, args))
    cols = ["sample", "chrom", "start", "end", "n_bins", "zipcnv_state", "zipcnv_median_log2", "zipcnv_mean_log2", "zipcnv_smooth_mean", "zipcnv_cusum_peak", "zipcnv_window_bins", "zipcnv_k", "zipcnv_h_threshold", "method"]
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows)
    out["chrom_order"] = out["chrom"].map(chrom_order)
    return out.sort_values(["sample", "chrom_order", "start", "end"]).drop(columns=["chrom_order"])


def prepare_primary_segments(final_segments: pd.DataFrame, args) -> pd.DataFrame:
    seg = final_segments.copy()
    seg["sample"] = seg["sample"].map(clean_token)
    seg["chrom"] = seg["chrom"].map(norm_chrom)
    seg["start"] = safe_numeric(seg["start"]).astype("Int64")
    seg["end"] = safe_numeric(seg["end"]).astype("Int64")
    if "seg_log2" not in seg.columns:
        seg["seg_log2"] = safe_numeric(seg["seg.mean"]) if "seg.mean" in seg.columns else np.nan
    seg["seg_log2"] = safe_numeric(seg["seg_log2"])
    seg["primary_state"] = seg["seg_log2"].apply(lambda x: state_from_log2(x, args.state_gain_threshold, args.state_loss_threshold))
    seg = seg[seg["primary_state"].isin(["gain", "loss"])].copy()
    if "num_mark" not in seg.columns:
        seg["num_mark"] = np.nan
    if "final_source" not in seg.columns:
        seg["final_source"] = "primary_bam_boundary_refinement"
    return seg


def interval_overlap(a_start, a_end, b_start, b_end):
    return max(0, min(int(a_end), int(b_end)) - max(int(a_start), int(b_start)))


def compare_methods(primary: pd.DataFrame, zipseg: pd.DataFrame, args):
    rows = []
    for _, p in primary.iterrows() if not primary.empty else []:
        cand = zipseg[(zipseg["sample"] == p["sample"]) & (zipseg["chrom"] == p["chrom"]) & (zipseg["zipcnv_state"] == p["primary_state"])] if not zipseg.empty else pd.DataFrame()
        p_len = max(1, int(p["end"]) - int(p["start"]))
        best = None
        best_ov = 0
        for _, z in cand.iterrows():
            ov = interval_overlap(p["start"], p["end"], z["start"], z["end"])
            if ov > best_ov:
                best_ov = ov
                best = z
        if best is not None:
            z_len = max(1, int(best["end"]) - int(best["start"]))
            frac_primary = best_ov / p_len
            frac_zip = best_ov / z_len
            supported = (frac_primary >= args.zipcnv_compare_min_overlap) or (frac_zip >= args.zipcnv_compare_min_overlap)
            rows.append({
                "record_type": "primary_segment",
                "sample": p["sample"], "chrom": p["chrom"], "start": int(p["start"]), "end": int(p["end"]),
                "primary_state": p["primary_state"], "primary_seg_log2": p["seg_log2"],
               "primary_final_source": p.get("final_source", "primary_bam_boundary_refinement"),
                "zipcnv_supported": bool(supported), "same_state_overlap_bp": int(best_ov),
                "overlap_fraction_of_primary": frac_primary, "overlap_fraction_of_zipcnv": frac_zip,
                "best_zipcnv_start": int(best["start"]), "best_zipcnv_end": int(best["end"]),
                "best_zipcnv_state": best["zipcnv_state"], "best_zipcnv_median_log2": best["zipcnv_median_log2"],
                "interpretation": "Primary BAM-refined segment has ZIPcnv-adapted CUSUM support" if supported else "Primary BAM-refined segment has weak ZIPcnv-adapted overlap",
            })
        else:
            rows.append({
                "record_type": "primary_segment",
                "sample": p["sample"], "chrom": p["chrom"], "start": int(p["start"]), "end": int(p["end"]),
                "primary_state": p["primary_state"], "primary_seg_log2": p["seg_log2"],
                "primary_final_source": p.get("final_source", "primary_bam_boundary_refinement"),
                "zipcnv_supported": False, "same_state_overlap_bp": 0,
                "overlap_fraction_of_primary": 0.0, "overlap_fraction_of_zipcnv": 0.0,
                "best_zipcnv_start": np.nan, "best_zipcnv_end": np.nan,
                "best_zipcnv_state": "none", "best_zipcnv_median_log2": np.nan,
                "interpretation": "Primary BAM-refined segment has no same-state ZIPcnv-adapted overlap",
            })
    for _, z in zipseg.iterrows() if not zipseg.empty else []:
        cand = primary[(primary["sample"] == z["sample"]) & (primary["chrom"] == z["chrom"]) & (primary["primary_state"] == z["zipcnv_state"])] if not primary.empty else pd.DataFrame()
        z_len = max(1, int(z["end"]) - int(z["start"]))
        best_ov = 0
        for _, p in cand.iterrows():
            best_ov = max(best_ov, interval_overlap(z["start"], z["end"], p["start"], p["end"]))
        if best_ov / z_len < args.zipcnv_compare_min_overlap:
            rows.append({
                "record_type": "zipcnv_adapted_only",
                "sample": z["sample"], "chrom": z["chrom"], "start": int(z["start"]), "end": int(z["end"]),
                "primary_state": "none", "primary_seg_log2": np.nan, "primary_final_source": "none",
                "zipcnv_supported": np.nan, "same_state_overlap_bp": int(best_ov),
                "overlap_fraction_of_primary": np.nan, "overlap_fraction_of_zipcnv": best_ov / z_len,
                "best_zipcnv_start": int(z["start"]), "best_zipcnv_end": int(z["end"]),
                "best_zipcnv_state": z["zipcnv_state"], "best_zipcnv_median_log2": z["zipcnv_median_log2"],
                "interpretation": "ZIPcnv-adapted CUSUM region without matching same-state primary segment",
            })
    comp = pd.DataFrame(rows)
    if not comp.empty:
        comp["chrom_order"] = comp["chrom"].map(chrom_order)
        comp = comp.sort_values(["sample", "chrom_order", "start", "end", "record_type"]).drop(columns=["chrom_order"])
    return comp


def sample_summary(primary, zipseg, comp, bstats):
    samples = sorted(set(primary["sample"].unique() if not primary.empty else []) | set(zipseg["sample"].unique() if not zipseg.empty else []) | set(bstats["sample"].unique() if not bstats.empty and "sample" in bstats.columns else []))
    rows = []
    for s in samples:
        ps = primary[primary["sample"] == s] if not primary.empty else pd.DataFrame()
        zs = zipseg[zipseg["sample"] == s] if not zipseg.empty else pd.DataFrame()
        cs = comp[comp["sample"] == s] if not comp.empty else pd.DataFrame()
        bs = bstats[bstats["sample"] == s] if not bstats.empty and "sample" in bstats.columns else pd.DataFrame()
        rows.append({
            "sample": s,
            "n_primary_gain_loss_segments": len(ps),
            "n_zipcnv_adapted_segments": len(zs),
            "n_primary_segments_with_zipcnv_support": int(((cs["record_type"] == "primary_segment") & (cs["zipcnv_supported"] == True)).sum()) if not cs.empty else 0,
            "n_primary_segments_without_zipcnv_support": int(((cs["record_type"] == "primary_segment") & (cs["zipcnv_supported"] == False)).sum()) if not cs.empty else 0,
            "n_zipcnv_adapted_only_segments": int((cs["record_type"] == "zipcnv_adapted_only").sum()) if not cs.empty else 0,
            "n_boundaries_refined_by_bam_method": int((bs["final_decision"] == "refined_boundary").sum()) if not bs.empty and "final_decision" in bs.columns else 0,
            "n_boundaries_retained_by_bam_method": int((bs["final_decision"].astype(str).str.contains("kept|fallback", case=False, na=False)).sum()) if not bs.empty and "final_decision" in bs.columns else 0,
            "recommended_primary_downstream_method": "BAM_boundary_refined_primary",
            "zipcnv_role": "independent_CUSUM_comparison_not_primary_boundary_refinement",
        })
    return pd.DataFrame(rows)


def write_status(consolidated: Path, args, repo_dir: Path):
    repo_present = repo_dir.exists() if str(repo_dir) else False
    readme_present = (repo_dir / "README.md").exists() if str(repo_dir) else False
    status = []
    if args.zipcnv_mode in ("adapted", "both"):
        status.append({"component": "ZIPcnv_adapted_bin_CUSUM", "status": "run", "message": "Implemented on normalized SAMURAI/qDNAseq/ichorCNA bins using the ZIPcnv CUSUM concept."})
    if args.zipcnv_mode in ("official", "both"):
        st = "not_run_requires_official_baseline_config" if args.zipcnv_official_run else "skipped_by_default"
        msg = "Official ZIPcnv execution was not performed automatically because the official code expects base-level depth arrays and a large normal baseline."
        status.append({"component": "official_ZIPcnv_repository", "status": st, "message": msg})
    status.append({"component": "ZIPcnv_repository", "status": "present" if repo_present else "not_present", "message": f"repo_dir={repo_dir}; README_present={readme_present}"})
    pd.DataFrame(status).to_csv(consolidated / "zipcnv_status.csv", index=False)


def copy_primary_outputs(dataset_dir: Path, consolidated: Path):
    primary = consolidated / "samurai_compatible_primary"
    if primary.exists():
        shutil.rmtree(primary)
    src = dataset_dir / "02_samurai_compatible"
    if src.exists():
        shutil.copytree(src, primary)
    for rel in ["02_samurai_compatible/all_segments.seg", "02_samurai_compatible/segments_logR_corrected_gistic.seg", "01_tables/refined_bins.tsv.gz", "01_tables/final_segments.tsv", "01_tables/final_segments.bed", "01_tables/boundary_refinement_statistics.csv", "01_tables/sample_refinement_summary.csv"]:
        p = dataset_dir / rel
        if p.exists():
            shutil.copy2(p, consolidated / Path(rel).name)


def main():
    ap = argparse.ArgumentParser(description="ZIPcnv-adapted comparison for BAM-refined CNA outputs")
    ap.add_argument("--dataset-name", required=True)
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--zipcnv-mode", choices=["off", "adapted", "official", "both"], default="adapted")
    ap.add_argument("--zipcnv-repo-dir", default="")
    ap.add_argument("--zipcnv-window-bins", type=int, default=5)
    ap.add_argument("--zipcnv-k", type=float, default=0.05)
    ap.add_argument("--zipcnv-h-mult", type=float, default=1.0)
    ap.add_argument("--zipcnv-min-segment-bins", type=int, default=3)
    ap.add_argument("--zipcnv-min-abs-log2", type=float, default=0.25)
    ap.add_argument("--zipcnv-merge-gap-bins", type=int, default=1)
    ap.add_argument("--zipcnv-compare-min-overlap", type=float, default=0.50)
    ap.add_argument("--zipcnv-official-run", action="store_true")
    ap.add_argument("--state-gain-threshold", type=float, default=0.25)
    ap.add_argument("--state-loss-threshold", type=float, default=-0.25)
    args = ap.parse_args()

    if args.zipcnv_mode == "off":
        log("ZIPcnv comparison disabled (--zipcnv-mode off)")
        return 0

    dataset_dir = Path(args.dataset_dir)
    tables = dataset_dir / "01_tables"
    consolidated = dataset_dir / "03_consolidated"
    mkdir(consolidated)
    args.zipcnv_h = max(0.25, args.zipcnv_window_bins * args.zipcnv_k * args.zipcnv_h_mult)

    refined_bins_file = tables / "refined_bins.tsv.gz"
    final_segments_file = tables / "final_segments.tsv"
    bstats_file = tables / "boundary_refinement_statistics.csv"
    if not refined_bins_file.exists() or not final_segments_file.exists():
        raise FileNotFoundError("Boundary refinement outputs not found; run the primary method first")

    refined_bins = read_tsv(refined_bins_file)
    final_segments = read_tsv(final_segments_file)
    bstats = pd.read_csv(bstats_file) if bstats_file.exists() else pd.DataFrame()

    zipseg = run_zipcnv_adapted(refined_bins, args) if args.zipcnv_mode in ("adapted", "both") else pd.DataFrame()
    primary = prepare_primary_segments(final_segments, args)
    comp = compare_methods(primary, zipseg, args)
    summ = sample_summary(primary, zipseg, comp, bstats)

    zipseg.to_csv(consolidated / "zipcnv_adapted_segments.tsv", sep="\t", index=False)
    comp.to_csv(consolidated / "method_comparison_by_segment.csv", index=False)
    summ.to_csv(consolidated / "method_comparison_by_sample.csv", index=False)

    annotated = final_segments.copy()
    if not comp.empty:
        prim = comp[comp["record_type"] == "primary_segment"].copy()
        key_cols = ["sample", "chrom", "start", "end"]
        for c in key_cols:
            if c in prim.columns and c in annotated.columns:
                prim[c] = prim[c].astype(str)
                annotated[c] = annotated[c].astype(str)
        keep = [c for c in key_cols + ["zipcnv_supported", "same_state_overlap_bp", "overlap_fraction_of_primary", "best_zipcnv_start", "best_zipcnv_end", "best_zipcnv_state", "best_zipcnv_median_log2", "interpretation"] if c in prim.columns]
        annotated = annotated.merge(prim[keep], on=key_cols, how="left")
    annotated.to_csv(consolidated / "consolidated_segments_annotated.tsv", sep="\t", index=False)

    pd.DataFrame([{
        "dataset": args.dataset_name,
        "primary_downstream_segmentation": "BAM_boundary_refined_primary",
        "primary_segments_file": "02_samurai_compatible/all_segments.seg",
        "primary_bins_folder": "02_samurai_compatible/bins/",
        "comparison_method": "ZIPcnv_adapted_bin_CUSUM" if args.zipcnv_mode in ("adapted", "both") else "official_ZIPcnv_status_only",
        "interpretation": "Use BAM boundary-refined SAMURAI-compatible files for downstream analyses; use ZIPcnv-adapted comparison files as an independent CUSUM support layer.",
        "zipcnv_window_bins": args.zipcnv_window_bins,
        "zipcnv_k": args.zipcnv_k,
        "zipcnv_min_abs_log2": args.zipcnv_min_abs_log2,
        "zipcnv_compare_min_overlap": args.zipcnv_compare_min_overlap,
    }]).to_csv(consolidated / "method_decision_summary.csv", index=False)

    write_status(consolidated, args, Path(args.zipcnv_repo_dir) if args.zipcnv_repo_dir else Path(""))
    copy_primary_outputs(dataset_dir, consolidated)

    pd.DataFrame([
        {"dataset": args.dataset_name, "category": "primary", "path": "01_tables/refined_bins.tsv.gz", "description": "Primary final bin table from BAM-supported boundary refinement with fallback to prior binning when unsupported."},
        {"dataset": args.dataset_name, "category": "primary", "path": "01_tables/final_segments.tsv", "description": "Primary final segment table from BAM-supported boundary refinement."},
        {"dataset": args.dataset_name, "category": "primary", "path": "01_tables/final_segments.bed", "description": "BED-style primary final segment table from BAM-supported boundary refinement."},
        {"dataset": args.dataset_name, "category": "primary", "path": "02_samurai_compatible/all_segments.seg", "description": "Primary SAMURAI/GISTIC-compatible segment file for downstream analyses."},
        {"dataset": args.dataset_name, "category": "zipcnv", "path": "03_consolidated/zipcnv_adapted_segments.tsv", "description": "ZIPcnv-adapted bin-level CUSUM CNV regions."},
        {"dataset": args.dataset_name, "category": "comparison", "path": "03_consolidated/method_comparison_by_segment.csv", "description": "Segment-level overlap between BAM-refined primary segments and ZIPcnv-adapted CUSUM regions."},
        {"dataset": args.dataset_name, "category": "comparison", "path": "03_consolidated/method_comparison_by_sample.csv", "description": "Sample-level comparison summary."},
        {"dataset": args.dataset_name, "category": "consolidated", "path": "03_consolidated/consolidated_segments_annotated.tsv", "description": "Primary final segments annotated with ZIPcnv-adapted support where applicable."},
        {"dataset": args.dataset_name, "category": "consolidated", "path": "03_consolidated/samurai_compatible_primary/", "description": "Copy of primary downstream SAMURAI-compatible outputs."},
        {"dataset": args.dataset_name, "category": "status", "path": "03_consolidated/zipcnv_status.csv", "description": "ZIPcnv repository/adapted/official status."},
    ]).to_csv(consolidated / "consolidated_manifest.csv", index=False)

    log(f"ZIPcnv comparison complete: {consolidated}")
    log(f"  ZIPcnv adapted segments: {consolidated / 'zipcnv_adapted_segments.tsv'}")
    log(f"  Method comparison:       {consolidated / 'method_comparison_by_segment.csv'}")
    log(f"  Consolidated manifest:   {consolidated / 'consolidated_manifest.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PYZIP
# Normalize and syntax-check ZIPcnv-adapted comparison helper as well.
python - "$ZIP_HELPER" <<'PYCHECKZIP'
from pathlib import Path
import py_compile
import sys
path = Path(sys.argv[1])
text = path.read_text().replace("\t", "    ")
path.write_text(text)
py_compile.compile(str(path), doraise=True)
print(f"ZIPcnv comparison helper syntax check: OK ({path})", flush=True)
PYCHECKZIP
chmod +x "$ZIP_HELPER"

run_dataset() {
  local dataset="$1" input_dir="$2" caller="$3" platform="$4" bam_dir="$5" prior_seg="$6" binsize="$7" finekb="$8" coverage_mode="$9"
  local ds_out="$OUTDIR/$dataset"
  mkdir -p "$ds_out"
  echo "======================================================================"
  echo "Dataset: $dataset"
  echo "Input:   $input_dir"
  echo "Caller:  $caller"
  echo "BAM dir: $bam_dir"
  echo "Prior:   $prior_seg"
  echo "Output:  $ds_out"
  echo "======================================================================"

  [[ -d "$input_dir" ]] || { echo "ERROR: input directory missing: $input_dir" >&2; exit 1; }
  [[ -d "$bam_dir" ]] || { echo "ERROR: BAM directory missing: $bam_dir" >&2; exit 1; }
  [[ -s "$prior_seg" ]] || { echo "ERROR: prior segmentation missing/empty: $prior_seg" >&2; exit 1; }

  python "$PY_HELPER" \
    --dataset-name "$dataset" \
    --input-dir "$input_dir" \
    --caller "$caller" \
    --platform "$platform" \
    --bam-dirs "$bam_dir" \
    --prior-seg "$prior_seg" \
    --outdir "$ds_out" \
    --coarse-binsize-kb "$binsize" \
    --fine-bin-kb "$finekb" \
    --search-radius-bins "$SEARCH_RADIUS_BINS" \
    --search-radius-bp "$SEARCH_RADIUS_BP" \
    --min-side-fine-bins "$MIN_SIDE_FINE_BINS" \
    --min-valid-fine-bins "$MIN_VALID_FINE_BINS" \
    --min-local-log2-diff "$MIN_LOCAL_LOG2_DIFF" \
    --min-adjacent-seg-delta "$MIN_ADJACENT_SEG_DELTA" \
    --min-bic-gain "$MIN_BIC_GAIN" \
    --permutations "$PERMUTATIONS" \
    --permutation-p "$PERMUTATION_P" \
    --accept-rule "$ACCEPT_RULE" \
    --max-ci-bp "$MAX_CI_BP" \
    --max-ci-fraction-of-coarse "$MAX_CI_FRACTION_OF_COARSE" \
    --min-shift-bp "$MIN_SHIFT_BP" \
    --min-median-cover-units "$MIN_MEDIAN_COVER_UNITS" \
    --min-side-cover-units "$MIN_SIDE_COVER_UNITS" \
    --max-fine-bins-per-window "$MAX_FINE_BINS_PER_WINDOW" \
    --min-mapq "$MIN_MAPQ" \
    --coverage-mode "$coverage_mode" \
    --normal-samples "$NORMAL_SAMPLES" \
    --normal-bam-dirs "$NORMAL_BAM_DIRS" \
    --pon-mode "$PON_MODE" \
    $([[ "$INCLUDE_DUPLICATES" == "true" ]] && echo --include-duplicates) \
    $([[ "$INCLUDE_SUPPLEMENTARY" == "true" ]] && echo --include-supplementary) \
    $([[ "$INCLUDE_SECONDARY" == "true" ]] && echo --include-secondary) \
    --state-gain-threshold "$STATE_GAIN_THRESHOLD" \
    --state-loss-threshold "$STATE_LOSS_THRESHOLD" \
    $([[ "$FORCE" == "true" ]] && echo --force)

  if [[ "$ZIPCNV_MODE" != "off" ]]; then
    echo "Running ZIPcnv comparison layer for $dataset"
    python "$ZIP_HELPER"       --dataset-name "$dataset"       --dataset-dir "$ds_out"       --zipcnv-mode "$ZIPCNV_MODE"       --zipcnv-repo-dir "$ZIPCNV_DIR"       --zipcnv-window-bins "$ZIPCNV_WINDOW_BINS"       --zipcnv-k "$ZIPCNV_K"       --zipcnv-h-mult "$ZIPCNV_H_MULT"       --zipcnv-min-segment-bins "$ZIPCNV_MIN_SEGMENT_BINS"       --zipcnv-min-abs-log2 "$ZIPCNV_MIN_ABS_LOG2"       --zipcnv-merge-gap-bins "$ZIPCNV_MERGE_GAP_BINS"       --zipcnv-compare-min-overlap "$ZIPCNV_COMPARE_MIN_OVERLAP"       --state-gain-threshold "$STATE_GAIN_THRESHOLD"       --state-loss-threshold "$STATE_LOSS_THRESHOLD"       $([[ "$ZIPCNV_OFFICIAL_RUN" == "true" ]] && echo --zipcnv-official-run)
  fi
}

if [[ "$MODE" == "ont" || "$MODE" == "both" ]]; then
  [[ -n "$ONT_ICHOR_DIR" ]] || { echo "ERROR: --ont-ichor-dir required for --mode ont/both" >&2; exit 1; }
  [[ -n "$ONT_BAM_DIR" ]] || { echo "ERROR: --ont-bam-dir required for --mode ont/both" >&2; exit 1; }
  [[ -n "$ONT_PRIOR_SEG" ]] || { echo "ERROR: --ont-prior-seg required for --mode ont/both" >&2; exit 1; }
  run_dataset "ONT_ichorcna_${ONT_BINSIZE_KB}kb" "$ONT_ICHOR_DIR" "ichorcna" "ONT" "$ONT_BAM_DIR" "$ONT_PRIOR_SEG" "$ONT_BINSIZE_KB" "$FINE_BIN_KB_ONT" "$COVERAGE_MODE_ONT"
fi

if [[ "$MODE" == "illumina" || "$MODE" == "both" ]]; then
  [[ -n "$ILLUMINA_QDNASEQ_DIR" ]] || { echo "ERROR: --illumina-qdnaseq-dir required for --mode illumina/both" >&2; exit 1; }
  [[ -n "$ILLUMINA_BAM_DIR" ]] || { echo "ERROR: --illumina-bam-dir required for --mode illumina/both" >&2; exit 1; }
  [[ -n "$ILLUMINA_PRIOR_SEG" ]] || { echo "ERROR: --illumina-prior-seg required for --mode illumina/both" >&2; exit 1; }
  # Default: no PON for Illumina unless explicitly supplied.
  if [[ "$MODE" == "illumina" && "$NORMAL_SAMPLES" == "auto" && -z "$NORMAL_BAM_DIRS" ]]; then
    NORMAL_SAMPLES="none"
    PON_MODE="off"
  fi
  run_dataset "illumina_qdnaseq_${ILLUMINA_BINSIZE_KB}kb" "$ILLUMINA_QDNASEQ_DIR" "qdnaseq" "illumina" "$ILLUMINA_BAM_DIR" "$ILLUMINA_PRIOR_SEG" "$ILLUMINA_BINSIZE_KB" "$FINE_BIN_KB_ILLUMINA" "$COVERAGE_MODE_ILLUMINA"
fi

echo
echo "Done. Critical outputs are under: $OUTDIR"
echo "Inspect:"
echo "  <dataset>/01_tables/refined_bins.tsv.gz"
echo "  <dataset>/01_tables/final_segments.tsv"
echo "  <dataset>/01_tables/final_segments.bed"
echo "  <dataset>/01_tables/boundary_refinement_statistics.csv"
echo "  <dataset>/01_tables/sample_refinement_summary.csv"
echo "  <dataset>/02_samurai_compatible/all_segments.seg"
echo "  <dataset>/02_samurai_compatible/bins/"
echo "  <dataset>/03_consolidated/consolidated_manifest.csv"
echo "  <dataset>/03_consolidated/method_comparison_by_segment.csv"
echo "  <dataset>/03_consolidated/method_comparison_by_sample.csv"
echo "  <dataset>/04_final_results/final_segments.tsv"
echo "  <dataset>/04_final_results/final_segments.bed"
echo "  <dataset>/04_final_results/refined_bins_boundary_bp_difference.csv"
echo "  <dataset>/04_final_results/refined_bins_boundary_bp_difference.xlsx"
echo "  <dataset>/04_final_results/cna_cytogenomic_input/qdnaseq_bins/"
echo "  <dataset>/04_final_results/cna_cytogenomic_input/run_cna_codification.sh"
