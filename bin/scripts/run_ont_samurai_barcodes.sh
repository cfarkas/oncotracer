#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

# Legacy v1.1 comparator/source support only. Native v2 does not package or
# invoke this Nextflow-dependent launcher.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

###############################################################################
# ONT LP-WGS barcodes -> merged FASTQ.gz -> minimap2 BAM -> SAMURAI.
# This frozen comparator accepts TUMOR inputs only and rejects NORMAL inputs
# instead of silently filtering them. It never constructs or applies a
# sample-derived panel. Callers that require a canonical reference asset may
# receive one explicitly with --normal_panel.
###############################################################################

LPWGS_ROOT="${LPWGS_ROOT:-$PWD}"
FOLDER=""
BARCODES_CSV=""
SAMPLE_NAMES_CSV=""
OUTDIR=""
REF_FA=""

GENOME_KEY="hg38"
BINSIZE=500
STATUS="tumor"
ANALYSIS_TYPE="solid_biopsy"
CALLER="auto"
NORMAL_PANEL=""
SIZE_SELECTION=false

# NORMAL folders are independent sample inputs. Each --normal-folder needs one
# matching --normal-barcodes list.
declare -a NORMAL_FOLDERS=()
declare -a NORMAL_BARCODES_CSVS=()
declare -a NORMAL_SAMPLE_NAMES_CSVS=()

QDNASEQ_BIN_DATA=""
QDNASEQ_RSCRIPT=""

ICHORCNA_GC_WIG=""
ICHORCNA_MAP_WIG=""
ICHORCNA_CENTROMERE_FILE=""
ICHORCNA_REPTIME_WIG=""
AUTO_ICHORCNA_REFS=true
AUTO_ICHORCNA_PON=true

MIN_AGE_MINUTES=10
STRICT_SAMPLE_COMPLETENESS=false
FORCE_REALIGN=false
RESUME=true
NFX_SYNTAX_PARSER="v1"

MM2_PRESET="${MM2_PRESET:-map-ont}"
PIGZ_THREADS=8
VALIDATE_THREADS=4
MM2_THREADS=64
SORT_THREADS=32
SORT_MEM="512M"
PATCH_SAMURAI=true
SAMURAI_PROFILE="singularity"

usage() {
  cat <<'EOF'
Usage:
  run_ont_samurai_barcodes.sh \
    --folder PATH \
    --barcodes barcode07,barcode08 \
    --sample-names G7_OC1,H8_OC3 \
    --outdir PATH \
    --analysis_type solid_biopsy \
    --caller qdnaseq \
    --binsize 100

Core options:
  --folder PATH                         tumor/primary fastq_pass, run folder, or parent containing fastq_pass
  --barcodes LIST                       comma-separated tumor/primary barcode dirs
  --sample-names LIST                   comma-separated tumor/primary sample names; defaults to barcode names
  --outdir PATH                         output directory
  --ref FASTA                           default: <lpwgs-root>/references/samurai_hg38/genome.fa
  --lpwgs-root PATH                     default: current directory
  --status tumor                        frozen comparator accepts TUMOR only; default: tumor

Unsupported frozen-comparator options:
  --normal-folder, --normal-barcodes, and --normal-sample-names fail explicitly.
  Use native OncoTracer v2 to CNA-call mixed cohorts independently.
  Removed panel options such as --build-pon also fail explicitly.

Advanced qDNAseq input:
  --qdnaseq-bin-data PATH               optional local QDNAseq bin annotation path

SAMURAI options:
  --analysis_type solid_biopsy|liquid_biopsy    default: solid_biopsy
  --caller auto|qdnaseq|ascat_sc|ichorcna|wisecondorx
                                            default: auto; solid->qdnaseq, liquid->ichorcna
  --binsize N                            default: 500 kbp
  --normal_panel PATH                    optional canonical prebuilt caller panel; not used by qDNAseq
  --size_selection / --no-size_selection default: off

ichorCNA reference options:
  --ichorcna_gc_wig PATH                 required by SAMURAI when caller=ichorcna
  --ichorcna_map_wig PATH                required by SAMURAI when caller=ichorcna
  --ichorcna_centromere_file PATH        optional, auto-filled for hg38/500kb
  --ichorcna_reptime_wig PATH            optional, auto-filled for hg38/500kb
  --auto-ichorcna-refs                   default; use/download SAMURAI v1.4.0 hg38/500kb assets
  --no-auto-ichorcna-refs                require manual WIG paths
  --auto-ichorcna-pon                    default; use bundled canonical HD_ULP hg38/500kb panel
  --no-auto-ichorcna-pon                 do not auto-fill --normal_panel

FASTQ/alignment options:
  --min-age-minutes N                    default: 10; use 0 if the run is finished
  --allow-partial                        default; merge valid FASTQs even if others are bad/recent
  --strict-fastq-completeness            skip barcode if any FASTQ is bad/recent
  --force-realign                        delete existing BAMs and merged FASTQs
  --mm2-preset PRESET                    default: map-ont
  --pigz-threads N                       default: 8
  --validate-threads N                   default: 4
  --mm2-threads N                        default: 64
  --sort-threads N                       default: 32
  --sort-mem VALUE                       default: 512M

Nextflow options:
  --profile NAME                         SAMURAI Nextflow profile: docker, singularity, or conda [singularity]
  --NFX_V1                               export NXF_SYNTAX_PARSER=v1; default
  --NFX_V2                               export NXF_SYNTAX_PARSER=v2
  --no-resume                            run without -resume
  --no-patch-samurai                     skip cached SAMURAI v1.4.0 dict typo patch
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --folder) FOLDER="$2"; shift 2 ;;
    --barcodes) BARCODES_CSV="$2"; shift 2 ;;
    --sample-names) SAMPLE_NAMES_CSV="$2"; shift 2 ;;
    --outdir) OUTDIR="$2"; shift 2 ;;
    --lpwgs-root) LPWGS_ROOT="$2"; shift 2 ;;
    --ref) REF_FA="$2"; shift 2 ;;
    --status) STATUS="$2"; shift 2 ;;
    --normal-folder|--normal_folder) NORMAL_FOLDERS+=("$2"); shift 2 ;;
    --normal-barcodes|--normal_barcodes) NORMAL_BARCODES_CSVS+=("$2"); shift 2 ;;
    --normal-sample-names|--normal_sample_names) NORMAL_SAMPLE_NAMES_CSVS+=("$2"); shift 2 ;;
    --build-pon|--build_pon|--no-build-pon|--no_build_pon|\
    --pon-name|--pon_name|--filter-bam-pon|--filter_bam_pon|\
    --qdnaseq-local-pon|--qdnaseq_local_pon|\
    --no-qdnaseq-local-pon|--no_qdnaseq_local_pon|\
    --qdnaseq-pon-min-normals|--qdnaseq_pon_min_normals|\
    --qdnaseq-min-mapq|--qdnaseq_min_mapq|\
    --qdnaseq-r-container|--qdnaseq_r_container)
      echo "ERROR: sample-derived panel construction has been removed; NORMAL samples are analyzed independently" >&2
      exit 2
      ;;
    --analysis_type|--analysis-type) ANALYSIS_TYPE="$2"; shift 2 ;;
    --caller) CALLER="$2"; shift 2 ;;
    --binsize) BINSIZE="$2"; shift 2 ;;
    --normal_panel|--normal-panel) NORMAL_PANEL="$2"; shift 2 ;;
    --size_selection|--size-selection) SIZE_SELECTION=true; shift ;;
    --no-size_selection|--no-size-selection) SIZE_SELECTION=false; shift ;;
    --ichorcna_gc_wig|--ichorcna-gc-wig) ICHORCNA_GC_WIG="$2"; shift 2 ;;
    --ichorcna_map_wig|--ichorcna-map-wig) ICHORCNA_MAP_WIG="$2"; shift 2 ;;
    --ichorcna_centromere_file|--ichorcna-centromere-file) ICHORCNA_CENTROMERE_FILE="$2"; shift 2 ;;
    --ichorcna_reptime_wig|--ichorcna-reptime-wig) ICHORCNA_REPTIME_WIG="$2"; shift 2 ;;
    --auto-ichorcna-refs|--auto_ichorcna_refs) AUTO_ICHORCNA_REFS=true; shift ;;
    --no-auto-ichorcna-refs|--no-auto_ichorcna_refs) AUTO_ICHORCNA_REFS=false; shift ;;
    --auto-ichorcna-pon|--auto_ichorcna_pon) AUTO_ICHORCNA_PON=true; shift ;;
    --no-auto-ichorcna-pon|--no-auto_ichorcna_pon) AUTO_ICHORCNA_PON=false; shift ;;
    --min-age-minutes) MIN_AGE_MINUTES="$2"; shift 2 ;;
    --allow-partial) STRICT_SAMPLE_COMPLETENESS=false; shift ;;
    --strict-fastq-completeness) STRICT_SAMPLE_COMPLETENESS=true; shift ;;
    --force-realign) FORCE_REALIGN=true; shift ;;
    --profile) SAMURAI_PROFILE="$2"; shift 2 ;;
    --no-resume) RESUME=false; shift ;;
    --NFX_V1|--nfx-v1|--nxf-v1) NFX_SYNTAX_PARSER="v1"; shift ;;
    --NFX_V2|--nfx-v2|--nxf-v2) NFX_SYNTAX_PARSER="v2"; shift ;;
    --mm2-preset) MM2_PRESET="$2"; shift 2 ;;
    --pigz-threads) PIGZ_THREADS="$2"; shift 2 ;;
    --validate-threads) VALIDATE_THREADS="$2"; shift 2 ;;
    --mm2-threads) MM2_THREADS="$2"; shift 2 ;;
    --sort-threads) SORT_THREADS="$2"; shift 2 ;;
    --sort-mem) SORT_MEM="$2"; shift 2 ;;
    --no-patch-samurai) PATCH_SAMURAI=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

[[ -n "$FOLDER" ]] || { echo "ERROR: --folder is required" >&2; usage >&2; exit 1; }
[[ -n "$BARCODES_CSV" ]] || { echo "ERROR: --barcodes is required" >&2; usage >&2; exit 1; }
[[ -n "$OUTDIR" ]] || { echo "ERROR: --outdir is required" >&2; usage >&2; exit 1; }

[[ "$STATUS" == "tumor" || "$STATUS" == "normal" ]] || { echo "ERROR: --status must be tumor or normal" >&2; exit 1; }
if [[ "$STATUS" == "normal" || ${#NORMAL_FOLDERS[@]} -gt 0 ]]; then
  echo "ERROR: the frozen Nextflow comparator cannot CNA-call NORMAL rows independently; use native OncoTracer v2 for mixed cohorts" >&2
  exit 2
fi
[[ "$ANALYSIS_TYPE" == "solid_biopsy" || "$ANALYSIS_TYPE" == "liquid_biopsy" ]] || { echo "ERROR: --analysis_type must be solid_biopsy or liquid_biopsy" >&2; exit 1; }
[[ "$BINSIZE" =~ ^[0-9]+$ && "$BINSIZE" -gt 0 ]] || { echo "ERROR: --binsize must be a positive integer" >&2; exit 1; }
[[ "$MIN_AGE_MINUTES" =~ ^[0-9]+$ ]] || { echo "ERROR: --min-age-minutes must be a non-negative integer" >&2; exit 1; }
[[ "$NFX_SYNTAX_PARSER" == "v1" || "$NFX_SYNTAX_PARSER" == "v2" ]] || { echo "ERROR: Nextflow syntax parser must be v1 or v2" >&2; exit 1; }
[[ "$SAMURAI_PROFILE" == "docker" || "$SAMURAI_PROFILE" == "singularity" || "$SAMURAI_PROFILE" == "conda" ]] || { echo "ERROR: --profile must be docker, singularity, or conda" >&2; exit 1; }

if [[ "$SAMURAI_PROFILE" == "conda" ]]; then
  [[ -n "${CONDA_PREFIX:-}" ]] || { echo "ERROR: --profile conda requires an active CONDA_PREFIX" >&2; exit 1; }
  QDNASEQ_RSCRIPT="$CONDA_PREFIX/bin/Rscript"
  [[ -f "$QDNASEQ_RSCRIPT" && -x "$QDNASEQ_RSCRIPT" ]] || { echo "ERROR: the active Conda prefix is missing Rscript: $QDNASEQ_RSCRIPT" >&2; exit 1; }
  QDNASEQ_RSCRIPT="$(readlink -f -- "$QDNASEQ_RSCRIPT")"
  env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
    "$QDNASEQ_RSCRIPT" --vanilla - <<'RS_CONDA_CHECK'
required <- c("argparser", "readr", "dplyr", "ggplot2", "scales", "yaml")
missing <- required[!vapply(required, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) stop("Missing Conda R package(s): ", paste(missing, collapse = ", "))
RS_CONDA_CHECK
  python3 - <<'PY_CONDA_CHECK'
import janitor
import natsort
import openpyxl
import pandas
import pandera
import polars
import typer
PY_CONDA_CHECK
  [[ -x "$CONDA_PREFIX/bin/qpdf" ]] || { echo "ERROR: the active Conda prefix is missing qpdf: $CONDA_PREFIX/bin/qpdf" >&2; exit 1; }
fi

if [[ "$CALLER" == "auto" ]]; then
  [[ "$ANALYSIS_TYPE" == "solid_biopsy" ]] && CALLER="qdnaseq" || CALLER="ichorcna"
fi

case "$CALLER" in
  qdnaseq|ascat_sc|ichorcna|wisecondorx) ;;
  *) echo "ERROR: --caller must be auto, qdnaseq, ascat_sc, ichorcna, or wisecondorx" >&2; exit 1 ;;
esac

if [[ "$ANALYSIS_TYPE" == "solid_biopsy" ]]; then
  case "$CALLER" in qdnaseq|ascat_sc|ichorcna) ;; *) echo "ERROR: solid_biopsy supports qdnaseq, ascat_sc, or ichorcna" >&2; exit 1 ;; esac
else
  case "$CALLER" in ichorcna|wisecondorx) ;; *) echo "ERROR: liquid_biopsy supports ichorcna or wisecondorx" >&2; exit 1 ;; esac
fi

if [[ "$ANALYSIS_TYPE" == "liquid_biopsy" && "$CALLER" == "wisecondorx" && -z "$NORMAL_PANEL" ]]; then
  echo "ERROR: liquid_biopsy + wisecondorx requires an explicit canonical --normal_panel" >&2
  exit 1
fi

if [[ "$CALLER" == "qdnaseq" && -n "$NORMAL_PANEL" ]]; then
  echo "WARNING: SAMURAI qDNAseq does not consume --normal_panel; NORMAL samples remain independent inputs." >&2
fi

LPWGS_ROOT="$(readlink -m "$LPWGS_ROOT")"
cd "$LPWGS_ROOT"

if [[ -f /opt/conda/etc/profile.d/conda.sh ]]; then
  # shellcheck source=/dev/null
  source /opt/conda/etc/profile.d/conda.sh
  conda activate base
fi

command -v pigz >/dev/null 2>&1 || sudo apt-get install -y pigz
command -v minimap2 >/dev/null 2>&1 || { echo "ERROR: minimap2 not found" >&2; exit 1; }
command -v samtools >/dev/null 2>&1 || { echo "ERROR: samtools not found" >&2; exit 1; }
command -v nextflow >/dev/null 2>&1 || { echo "ERROR: nextflow not found" >&2; exit 1; }
export NXF_SYNTAX_PARSER="$NFX_SYNTAX_PARSER"
SAMURAI_REVISION="v1.4.0"
SAMURAI_SOURCE_HELPER="$SCRIPT_DIR/prepare_samurai_source.sh"
[[ -s "$SAMURAI_SOURCE_HELPER" ]] || { echo "ERROR: SAMURAI source helper not found: $SAMURAI_SOURCE_HELPER" >&2; exit 1; }
SAMURAI_SOURCE="$(bash "$SAMURAI_SOURCE_HELPER" --lpwgs-root "$LPWGS_ROOT" --revision "$SAMURAI_REVISION")"
[[ -s "$SAMURAI_SOURCE/main.nf" ]] || { echo "ERROR: prepared SAMURAI source is invalid: $SAMURAI_SOURCE" >&2; exit 1; }

trim_ws() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }
sanitize_id() { local s; s="$(trim_ws "$1")"; s="${s// /_}"; printf '%s' "$s" | sed 's/[^A-Za-z0-9_.-]/_/g'; }
csv_to_array() { local csv="$1"; local -n arr_ref="$2"; csv="${csv//;/,}"; csv="${csv// /}"; IFS=',' read -r -a arr_ref <<< "$csv"; }
is_old_enough() { local f="$1" now mtime age; now="$(date +%s)"; mtime="$(stat -c %Y "$f")"; age="$((now-mtime))"; (( age >= MIN_AGE_MINUTES * 60 )); }
is_valid_fastq_gz() { pigz -t -p "$VALIDATE_THREADS" "$1" >/dev/null 2>&1; }
bam_is_ok() { samtools quickcheck -v "$1" >/dev/null 2>&1; }
first_fasta_contig() { awk '/^>/{sub(/^>/,""); sub(/[[:space:]].*/,""); print; exit}' "$1"; }
count_null_list() { tr -cd '\0' < "$1" | wc -c; }

validate_file() {
  local p="$1" label="$2"
  p="$(readlink -m "$p")"
  [[ -s "$p" ]] || { echo "ERROR: $label missing or empty: $p" >&2; exit 1; }
  printf '%s' "$p"
}

if [[ -n "$QDNASEQ_BIN_DATA" ]]; then
  QDNASEQ_BIN_DATA="$(validate_file "$QDNASEQ_BIN_DATA" "--qdnaseq-bin-data")"
fi

find_samurai_ichorcna_asset() {
  local filename="$1" root hit
  if [[ -s "$SAMURAI_SOURCE/assets/ichorcna/$filename" ]]; then
    echo "$SAMURAI_SOURCE/assets/ichorcna/$filename"
    return 0
  fi
  for root in \
    "$HOME/.nextflow/assets/.repos/dincalcilab/samurai/clones" \
    "$HOME/.nextflow/assets/dincalcilab/samurai" \
    "$HOME/.nextflow-2510/assets/dincalcilab/samurai"; do
    [[ -d "$root" ]] || continue
    hit="$(find "$root" -type f -path "*/assets/ichorcna/$filename" -size +0c -print 2>/dev/null | sort -V | tail -n 1 || true)"
    [[ -n "$hit" && -s "$hit" ]] && { echo "$hit"; return 0; }
  done
  return 1
}

download_samurai_ichorcna_asset() {
  local filename="$1" outdir out tmp url
  outdir="$LPWGS_ROOT/references/samurai_ichorcna_hg38_500kb"
  out="$outdir/$filename"
  tmp="$out.tmp"
  url="https://raw.githubusercontent.com/DIncalciLab/samurai/v1.4.0/assets/ichorcna/$filename"
  mkdir -p "$outdir"
  [[ -s "$out" ]] && { echo "$out"; return 0; }
  echo "Downloading SAMURAI ichorCNA asset: $filename" >&2
  rm -f "$tmp"
  if command -v curl >/dev/null 2>&1; then
    curl -L -f --retry 3 --connect-timeout 20 -o "$tmp" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$tmp" "$url"
  else
    echo "ERROR: curl or wget is required to download $filename" >&2
    return 1
  fi
  mv "$tmp" "$out"
  [[ -s "$out" ]] || { echo "ERROR: downloaded asset is empty: $out" >&2; return 1; }
  echo "$out"
}

resolve_ichor_asset_var() {
  local -n var="$1"
  local filename="$2"
  local label="$3"
  local required="$4"
  local found=""

  if [[ -n "$var" ]]; then
    var="$(validate_file "$var" "$label")"
    return 0
  fi

  if [[ "$AUTO_ICHORCNA_REFS" != "true" ]]; then
    [[ "$required" == "true" ]] && { echo "ERROR: $label is required for --caller ichorcna" >&2; exit 1; }
    return 0
  fi

  if [[ "$BINSIZE" != "500" ]]; then
    [[ "$required" == "true" ]] && { echo "ERROR: automatic ichorCNA assets are hg38/500kb only. Provide $label for binsize $BINSIZE." >&2; exit 1; }
    return 0
  fi

  found="$(find_samurai_ichorcna_asset "$filename" || true)"
  [[ -n "$found" ]] || found="$(download_samurai_ichorcna_asset "$filename" || true)"
  [[ -n "$found" && -s "$found" ]] || { [[ "$required" == "true" ]] && { echo "ERROR: could not resolve $label ($filename)" >&2; exit 1; } || return 0; }
  var="$found"
}

resolve_ichorcna_refs() {
  [[ "$CALLER" == "ichorcna" ]] || return 0

  resolve_ichor_asset_var ICHORCNA_GC_WIG "gc_hg38_500kb.wig" "--ichorcna_gc_wig" true
  resolve_ichor_asset_var ICHORCNA_MAP_WIG "map_hg38_500kb.wig" "--ichorcna_map_wig" true
  resolve_ichor_asset_var ICHORCNA_CENTROMERE_FILE "GRCh38.GCA_000001405.2_centromere_acen.txt" "--ichorcna_centromere_file" false
  resolve_ichor_asset_var ICHORCNA_REPTIME_WIG "Koren_repTiming_hg38_500kb.wig" "--ichorcna_reptime_wig" false

  if [[ -n "$NORMAL_PANEL" ]]; then
    NORMAL_PANEL="$(validate_file "$NORMAL_PANEL" "--normal_panel")"
  elif [[ "$ANALYSIS_TYPE" == "liquid_biopsy" && "$AUTO_ICHORCNA_PON" == "true" && "$BINSIZE" == "500" ]]; then
    local pon="HD_ULP_PoN_hg38_500kb_median_normAutosome_median.rds" found=""
    found="$(find_samurai_ichorcna_asset "$pon" || true)"
    [[ -n "$found" ]] || found="$(download_samurai_ichorcna_asset "$pon" || true)"
    [[ -n "$found" && -s "$found" ]] || { echo "ERROR: could not resolve SAMURAI ichorCNA PoN. Provide --normal_panel or use --no-auto-ichorcna-pon." >&2; exit 1; }
    NORMAL_PANEL="$found"
  fi

  echo
  echo "ichorCNA assets:"
  echo "  GC WIG       : $ICHORCNA_GC_WIG"
  echo "  MAP WIG      : $ICHORCNA_MAP_WIG"
  echo "  Centromere   : ${ICHORCNA_CENTROMERE_FILE:-not_set}"
  echo "  Reptime WIG  : ${ICHORCNA_REPTIME_WIG:-not_set}"
  echo "  Normal panel : ${NORMAL_PANEL:-not_set}"
}

resolve_fastq_pass() {
  local base="$1" one
  if [[ "$(basename "$base")" == "fastq_pass" ]]; then echo "$base"; return 0; fi
  if [[ -d "$base/fastq_pass" ]]; then echo "$base/fastq_pass"; return 0; fi
  one="$(find "$base" -mindepth 1 -maxdepth 1 -type d -name 'barcode*' -print -quit 2>/dev/null || true)"
  if [[ -n "$one" ]]; then echo "$base"; return 0; fi
  mapfile -t hits < <(find "$base" -maxdepth 8 -type d -name fastq_pass | sort)
  if (( ${#hits[@]} == 1 )); then echo "${hits[0]}"; return 0; fi
  if (( ${#hits[@]} == 0 )); then echo "ERROR: no fastq_pass found under $base" >&2; return 1; fi
  echo "ERROR: multiple fastq_pass folders found; pass exact fastq_pass with --folder/--normal-folder" >&2
  printf '  %s\n' "${hits[@]}" >&2
  return 1
}

resolve_barcode_dir() {
  local root="$1" barcode="$2" bc n
  if [[ -d "$root/$barcode" ]]; then echo "$root/$barcode"; return 0; fi
  if [[ "$barcode" =~ ^[0-9]+$ ]]; then
    printf -v bc 'barcode%02d' "$((10#$barcode))"
    [[ -d "$root/$bc" ]] && { echo "$root/$bc"; return 0; }
  fi
  if [[ "$barcode" =~ ^barcode0*([0-9]+)$ ]]; then
    n="${BASH_REMATCH[1]}"
    printf -v bc 'barcode%02d' "$((10#$n))"
    [[ -d "$root/$bc" ]] && { echo "$root/$bc"; return 0; }
  fi
  return 1
}

REF_DIR_DEFAULT="$LPWGS_ROOT/references/samurai_hg38"
[[ -n "$REF_FA" ]] || REF_FA="$REF_DIR_DEFAULT/genome.fa"
REF_FA="$(readlink -m "$REF_FA")"
REF_FAI="${REF_FA}.fai"
DICT="${REF_FA%.fa}.dict"

download_samurai_hg38_reference() {
  local ref_dir="$1" fasta="$1/genome.fa" fai="$1/genome.fa.fai" dict="$1/genome.dict"
  local s3="s3://ngi-igenomes/igenomes/Homo_sapiens/UCSC/hg38/Sequence/WholeGenomeFasta"
  local https="https://ngi-igenomes.s3.amazonaws.com/igenomes/Homo_sapiens/UCSC/hg38/Sequence/WholeGenomeFasta"
  mkdir -p "$ref_dir"
  [[ -s "$fasta" && -s "$fai" && -s "$dict" ]] && return 0
  echo "Downloading SAMURAI/iGenomes UCSC hg38 reference to $ref_dir"
  if command -v aws >/dev/null 2>&1; then
    [[ -s "$fasta" ]] || aws s3 cp --no-sign-request "$s3/genome.fa" "$fasta"
    [[ -s "$fai" ]] || aws s3 cp --no-sign-request "$s3/genome.fa.fai" "$fai"
    [[ -s "$dict" ]] || aws s3 cp --no-sign-request "$s3/genome.dict" "$dict"
  else
    [[ -s "$fasta" ]] || wget -O "$fasta" "$https/genome.fa"
    [[ -s "$fai" ]] || wget -O "$fai" "$https/genome.fa.fai"
    [[ -s "$dict" ]] || wget -O "$dict" "$https/genome.dict"
  fi
}
[[ "$REF_FA" == "$REF_DIR_DEFAULT/genome.fa" ]] && download_samurai_hg38_reference "$REF_DIR_DEFAULT"
[[ -s "$REF_FA" ]] || { echo "ERROR: missing reference FASTA: $REF_FA" >&2; exit 1; }

RUN_ROOT="$(readlink -m "$OUTDIR")"
[[ -e "$RUN_ROOT" && ! -d "$RUN_ROOT" ]] && { echo "ERROR: --outdir exists but is not a directory: $RUN_ROOT" >&2; exit 1; }
mkdir -p "$RUN_ROOT"/{input,bam,merged_fastq,results,work,logs,tmp,nextflow_launch,scripts}
mkdir -p "$LPWGS_ROOT/.singularity_cache"
LOCAL_CONFIG="$RUN_ROOT/samurai_hg38.config"
REF_MMI="${REF_FA}.${MM2_PRESET}.mmi"
export NXF_HOME="$RUN_ROOT/.nextflow"
mkdir -p "$NXF_HOME" "$NXF_HOME/plugins"
export NXF_PLUGINS_DIR="$NXF_HOME/plugins"
export NXF_ASSETS="$LPWGS_ROOT/.oncotracer/nxf-assets"
mkdir -p "$NXF_ASSETS"
export NXF_WORK="$RUN_ROOT/work"
export NXF_SINGULARITY_CACHEDIR="$LPWGS_ROOT/.singularity_cache"
SAMPLESHEET="$RUN_ROOT/input/samplesheet.csv"
USED_LOG="$RUN_ROOT/logs/used_fastq.tsv"
SKIP_FILE_LOG="$RUN_ROOT/logs/skipped_fastq.tsv"
SKIP_SAMPLE_LOG="$RUN_ROOT/logs/skipped_samples.tsv"
WARN_SAMPLE_LOG="$RUN_ROOT/logs/warning_samples.tsv"

unset DISPLAY
[[ -s "$REF_FAI" ]] || samtools faidx "$REF_FA"
if [[ ! -s "$DICT" ]]; then
  if command -v picard >/dev/null 2>&1; then
    picard CreateSequenceDictionary R="$REF_FA" O="$DICT"
  else
    samtools dict "$REF_FA" > "$DICT"
  fi
fi
first_contig="$(first_fasta_contig "$REF_FA")"
[[ "$first_contig" == chr* ]] || { echo "ERROR: first FASTA contig is '$first_contig', not UCSC chr* style." >&2; exit 1; }

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
cat > "$LOCAL_CONFIG" <<EOF
params {
  genomes {
    hg38 {
      fasta     = "${REF_FA}"
      fasta_fai = "${REF_FAI}"
      dict      = "${DICT}"
    }
  }
}
report { overwrite = true }
timeline { overwrite = true }
docker {
  runOptions = "-u ${HOST_UID}:${HOST_GID} -e HOME=/tmp -e MPLCONFIGDIR=/tmp/matplotlib -e XDG_CACHE_HOME=/tmp/cache"
}
EOF

echo "Created SAMURAI hg38 config: $LOCAL_CONFIG"

if [[ "$PATCH_SAMURAI" == "true" ]]; then
  python3 - "$SAMURAI_SOURCE/main.nf" <<'PY_PATCH_SAMURAI'
from pathlib import Path
import sys

path = Path(sys.argv[1])
old = "dict = params.dict ? channel.fromPath(params.fai).map { it -> [[id: it.baseName], it] }.collect() : channel.empty()"
new = "dict = params.dict ? channel.fromPath(params.dict).map { it -> [[id: it.baseName], it] }.collect() : channel.empty()"
text = path.read_text(encoding="utf-8")
if new in text:
    print(f"No SAMURAI patch needed: {path}")
elif old in text:
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"Patched SAMURAI dict input: {path}")
else:
    print(f"WARNING: SAMURAI dict typo pattern was not found: {path}")
PY_PATCH_SAMURAI
fi

resolve_ichorcna_refs

if [[ "$SAMURAI_PROFILE" == "conda" && "$CALLER" == "qdnaseq" && -z "$QDNASEQ_BIN_DATA" ]]; then
  QDNASEQ_BIN_HELPER="$SCRIPT_DIR/prepare_qdnaseq_bin_data.sh"
  [[ -s "$QDNASEQ_BIN_HELPER" ]] || { echo "ERROR: qDNAseq annotation helper not found: $QDNASEQ_BIN_HELPER" >&2; exit 1; }
  QDNASEQ_BIN_DATA="$(bash "$QDNASEQ_BIN_HELPER" \
    --rscript "$QDNASEQ_RSCRIPT" \
    --binsize "$BINSIZE" \
    --project-root "$LPWGS_ROOT")"
  [[ -s "$QDNASEQ_BIN_DATA" ]] || { echo "ERROR: qDNAseq annotation was not prepared: $QDNASEQ_BIN_DATA" >&2; exit 1; }
  echo "Using qDNAseq hg38 annotation: $QDNASEQ_BIN_DATA"
fi

if [[ ! -s "$REF_MMI" ]]; then
  echo "Building minimap2 index: $REF_MMI"
  minimap2 -x "$MM2_PRESET" -d "$REF_MMI" "$REF_FA"
fi

printf 'sample,bam,gender,status\n' > "$SAMPLESHEET"
printf 'sample\tstatus\tset\tbarcode\tpath\n' > "$USED_LOG"
printf 'sample\tstatus\tset\tbarcode\treason\tpath\n' > "$SKIP_FILE_LOG"
printf 'sample\tstatus\tset\tbarcode\treason\tdetail\n' > "$SKIP_SAMPLE_LOG"
printf 'sample\tstatus\tset\tbarcode\twarning\tdetail\n' > "$WARN_SAMPLE_LOG"

declare -A SEEN_SAMPLE_IDS=()
TUMOR_COUNT=0
NORMAL_COUNT=0

log_valid_fastqs() {
  local sample="$1" status="$2" set_label="$3" barcode="$4" valid_list="$5" fq
  while IFS= read -r -d '' fq; do
    printf '%s\t%s\t%s\t%s\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$fq" >> "$USED_LOG"
  done < "$valid_list"
}

merge_complete_fastqs() {
  local sample status set_label barcode valid_list out_fastq tmp_fastq

  if (( $# != 6 )); then
    echo "ERROR: merge_complete_fastqs expects 6 args: sample status set_label barcode valid_list out_fastq; got $#" >&2
    return 2
  fi

  sample="$1"
  status="$2"
  set_label="$3"
  barcode="$4"
  valid_list="$5"
  out_fastq="$6"
  tmp_fastq="${out_fastq}.tmp"

  rm -f "$tmp_fastq"
  echo ">>> [$sample] cat-merging complete FASTQ.gz file(s): $out_fastq"
  if xargs -0 -r cat -- < "$valid_list" > "$tmp_fastq"; then
    mv "$tmp_fastq" "$out_fastq"
  else
    rm -f "$tmp_fastq"
    printf '%s\t%s\t%s\t%s\tmerge_failed\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$out_fastq" >> "$SKIP_SAMPLE_LOG"
    echo ">>> [$sample] skipped: FASTQ.gz merge failed"
    return 1
  fi
  if ! is_valid_fastq_gz "$out_fastq"; then
    rm -f "$out_fastq"
    printf '%s\t%s\t%s\t%s\tmerged_fastq_failed_validation\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$out_fastq" >> "$SKIP_SAMPLE_LOG"
    echo ">>> [$sample] skipped: merged FASTQ.gz failed validation"
    return 1
  fi
}

append_ready_bam_to_samplesheet() {
  local sample="$1" bam="$2" status="$3"
  printf '%s,%s,,%s\n' "$sample" "$bam" "$status" >> "$SAMPLESHEET"
  if [[ "$status" == "normal" ]]; then
    NORMAL_COUNT=$((NORMAL_COUNT + 1))
  else
    TUMOR_COUNT=$((TUMOR_COUNT + 1))
  fi
}

prepare_barcode_set() {
  local raw_folder="$1" barcodes_csv="$2" sample_names_csv="$3" status="$4" set_label="$5"
  local folder fastq_pass_root
  local -a barcodes sample_names
  local idx barcode sample barcode_dir valid_list found_any valid_count sample_has_issue
  local valid_count_from_list out_bam tmp_bam merged_fastq RG

  folder="$(readlink -f "$raw_folder")"
  [[ -d "$folder" ]] || { echo "ERROR: folder does not exist for set '$set_label': $raw_folder" >&2; exit 1; }
  fastq_pass_root="$(resolve_fastq_pass "$folder")"

  csv_to_array "$barcodes_csv" barcodes
  (( ${#barcodes[@]} > 0 )) || { echo "ERROR: no barcodes parsed for set '$set_label'" >&2; exit 1; }

  if [[ -n "$sample_names_csv" ]]; then
    csv_to_array "$sample_names_csv" sample_names
    (( ${#sample_names[@]} == ${#barcodes[@]} )) || {
      echo "ERROR: sample name count must match barcode count for set '$set_label'" >&2
      exit 1
    }
  else
    sample_names=("${barcodes[@]}")
  fi

  echo
  echo "============================================================================"
  echo "FASTQ set           : $set_label"
  echo "FASTQ pass root     : $fastq_pass_root"
  echo "Status              : $status"
  echo "Barcodes            : ${barcodes[*]}"
  echo "Sample names        : ${sample_names[*]}"
  echo "============================================================================"

  for idx in "${!barcodes[@]}"; do
    barcode="$(sanitize_id "${barcodes[$idx]}")"
    sample="$(sanitize_id "${sample_names[$idx]}")"

    [[ -n "$sample" ]] || { echo "ERROR: empty sample ID in set '$set_label'" >&2; exit 1; }
    if [[ -n "${SEEN_SAMPLE_IDS[$sample]:-}" ]]; then
      echo "ERROR: duplicate sample ID after sanitization: $sample. Use unique sample names." >&2
      exit 1
    fi
    SEEN_SAMPLE_IDS[$sample]=1

    if ! barcode_dir="$(resolve_barcode_dir "$fastq_pass_root" "$barcode")"; then
      barcode_dir="$fastq_pass_root/$barcode"
    fi

    valid_list="$RUN_ROOT/tmp/${sample}.${barcode}.valid.list"
    : > "$valid_list"
    found_any=0
    valid_count=0
    sample_has_issue=0

    echo
    echo "----------------------------------------------------------------------------"
    echo "Sample : $sample"
    echo "Status : $status"
    echo "Set    : $set_label"
    echo "Barcode: $barcode"
    echo "Input  : $barcode_dir"
    echo "----------------------------------------------------------------------------"

    if [[ ! -d "$barcode_dir" ]]; then
      printf '%s\t%s\t%s\t%s\tbarcode_dir_missing\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$barcode_dir" >> "$SKIP_SAMPLE_LOG"
      echo ">>> [$sample] skipped: barcode directory missing"
      continue
    fi

    while IFS= read -r -d '' fq; do
      found_any=1
      if ! is_old_enough "$fq"; then
        printf '%s\t%s\t%s\t%s\ttoo_recent\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$fq" >> "$SKIP_FILE_LOG"
        sample_has_issue=1
        continue
      fi
      if is_valid_fastq_gz "$fq"; then
        printf '%s\0' "$fq" >> "$valid_list"
        valid_count="$((valid_count+1))"
      else
        printf '%s\t%s\t%s\t%s\tcorrupt_or_incomplete\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$fq" >> "$SKIP_FILE_LOG"
        sample_has_issue=1
      fi
    done < <(find "$barcode_dir" -maxdepth 1 -type f \( -name '*.fastq.gz' -o -name '*.fq.gz' \) -print0 | sort -z -V)

    if (( found_any == 0 )); then
      printf '%s\t%s\t%s\t%s\tno_fastq\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$barcode_dir" >> "$SKIP_SAMPLE_LOG"
      echo ">>> [$sample] skipped: no FASTQ found"
      continue
    fi

    if (( valid_count == 0 )); then
      printf '%s\t%s\t%s\t%s\tno_valid_fastq\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$barcode_dir" >> "$SKIP_SAMPLE_LOG"
      echo ">>> [$sample] skipped: no valid FASTQ found"
      continue
    fi

    if [[ "$STRICT_SAMPLE_COMPLETENESS" == "true" && "$sample_has_issue" -eq 1 ]]; then
      printf '%s\t%s\t%s\t%s\tincomplete_or_corrupt_input\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$barcode_dir" >> "$SKIP_SAMPLE_LOG"
      echo ">>> [$sample] skipped: at least one FASTQ is too recent or corrupt"
      continue
    fi

    if [[ "$sample_has_issue" -eq 1 ]]; then
      printf '%s\t%s\t%s\t%s\tpartial_input_merged\tvalid_fastq_gz=%s; skipped_files_log=%s\n' "$sample" "$status" "$set_label" "$barcode" "$valid_count" "$SKIP_FILE_LOG" >> "$WARN_SAMPLE_LOG"
      echo ">>> [$sample] WARNING: at least one FASTQ is too recent or corrupt, merging available fastqs.gz"
      echo "    Available complete FASTQ.gz files: $valid_count"
      echo "    Skipped-file log: $SKIP_FILE_LOG"
    fi

    valid_count_from_list="$(count_null_list "$valid_list")"
    [[ "$valid_count_from_list" == "$valid_count" ]] || echo "WARNING: FASTQ count mismatch for $sample" >&2

    out_bam="$RUN_ROOT/bam/${sample}.sorted.bam"
    tmp_bam="${out_bam}.tmp"
    merged_fastq="$RUN_ROOT/merged_fastq/${sample}.complete.fastq.gz"

    if [[ "$FORCE_REALIGN" == "true" ]]; then
      rm -f "$out_bam" "$out_bam.bai" "$out_bam.csi" "$merged_fastq"
    fi

    if [[ -s "$out_bam" && -s "$out_bam.bai" ]] && bam_is_ok "$out_bam"; then
      echo ">>> [$sample] reusing existing BAM: $out_bam"
      append_ready_bam_to_samplesheet "$sample" "$out_bam" "$status"
      log_valid_fastqs "$sample" "$status" "$set_label" "$barcode" "$valid_list"
      continue
    fi

    merge_complete_fastqs "$sample" "$status" "$set_label" "$barcode" "$valid_list" "$merged_fastq" || continue

    rm -f "$tmp_bam" "${tmp_bam}.bai" "${tmp_bam}.csi"
    RG="@RG\tID:${sample}\tSM:${sample}\tPL:ONT"
    echo ">>> [$sample] aligning merged FASTQ.gz with minimap2 ($MM2_PRESET)"
    echo "    Merged FASTQ.gz: $merged_fastq"

    if pigz -dc -p "$PIGZ_THREADS" "$merged_fastq" \
        | minimap2 -a -x "$MM2_PRESET" -t "$MM2_THREADS" -R "$RG" "$REF_MMI" - \
        | samtools sort -@ "$SORT_THREADS" -m "$SORT_MEM" -T "$RUN_ROOT/tmp/${sample}.sort" -o "$tmp_bam" -; then
      mv "$tmp_bam" "$out_bam"
    else
      rm -f "$tmp_bam"
      printf '%s\t%s\t%s\t%s\talignment_failed\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$barcode_dir" >> "$SKIP_SAMPLE_LOG"
      echo ">>> [$sample] skipped: alignment failed"
      continue
    fi

    samtools index -@ "$SORT_THREADS" "$out_bam"
    if ! bam_is_ok "$out_bam"; then
      rm -f "$out_bam" "$out_bam.bai" "$out_bam.csi"
      printf '%s\t%s\t%s\t%s\tbam_failed_validation\t%s\n' "$sample" "$status" "$set_label" "$barcode" "$out_bam" >> "$SKIP_SAMPLE_LOG"
      echo ">>> [$sample] skipped: BAM validation failed"
      continue
    fi

    append_ready_bam_to_samplesheet "$sample" "$out_bam" "$status"
    log_valid_fastqs "$sample" "$status" "$set_label" "$barcode" "$valid_list"
    echo ">>> [$sample] ready: $out_bam"
  done
}


# Prepare primary/tumor samples.
prepare_barcode_set "$FOLDER" "$BARCODES_CSV" "$SAMPLE_NAMES_CSV" "$STATUS" "primary"

# Prepare independent NORMAL samples, if provided.
for idx in "${!NORMAL_FOLDERS[@]}"; do
  ns_names=""
  if (( ${#NORMAL_SAMPLE_NAMES_CSVS[@]} > 0 )); then
    ns_names="${NORMAL_SAMPLE_NAMES_CSVS[$idx]}"
  fi
  prepare_barcode_set "${NORMAL_FOLDERS[$idx]}" "${NORMAL_BARCODES_CSVS[$idx]}" "$ns_names" "normal" "normal_$((idx+1))"
done

n_samples="$(( $(wc -l < "$SAMPLESHEET") - 1 ))"
if (( n_samples == 0 )); then
  echo
  echo "No valid samples found. See logs:"
  echo "  $SKIP_FILE_LOG"
  echo "  $SKIP_SAMPLE_LOG"
  echo "  $WARN_SAMPLE_LOG"
  exit 0
fi

cat > "$RUN_ROOT/logs/run_summary.txt" <<EOF
RUN_ROOT=$RUN_ROOT
SAMPLESHEET=$SAMPLESHEET
TUMOR_COUNT=$TUMOR_COUNT
NORMAL_COUNT=$NORMAL_COUNT
SAMPLE_DERIVED_PANEL_USED=false
ANALYSIS_TYPE=$ANALYSIS_TYPE
CALLER=$CALLER
BINSIZE=$BINSIZE
NORMAL_PANEL=${NORMAL_PANEL:-not_set}
QDNASEQ_BIN_DATA=${QDNASEQ_BIN_DATA:-not_set}
EOF

cat > "$RUN_ROOT/logs/normal_sample_manifest.tsv" <<EOF
sample	bam	status
EOF
awk -F',' 'NR>1 && $4=="normal" {print $1"\t"$2"\t"$4}' "$SAMPLESHEET" >> "$RUN_ROOT/logs/normal_sample_manifest.tsv"

echo
echo "Prepared $n_samples sample(s): tumor=$TUMOR_COUNT normal=$NORMAL_COUNT"
cat "$SAMPLESHEET"

NF_CMD=(
  nextflow run "$SAMURAI_SOURCE"
  -c "$LOCAL_CONFIG"
  -profile "$SAMURAI_PROFILE"
  -work-dir "$NXF_WORK"
  --input "$SAMPLESHEET"
  --outdir "$RUN_ROOT/results"
  --genome hg38
  --analysis_type "$ANALYSIS_TYPE"
  --caller "$CALLER"
  --binsize "$BINSIZE"
  --aligner false
)

if [[ "$CALLER" == "qdnaseq" ]]; then
  NF_CMD+=( --qdnaseq_paired_ends false )
  [[ -n "$QDNASEQ_BIN_DATA" ]] && NF_CMD+=( --qdnaseq_bin_data "$QDNASEQ_BIN_DATA" )
fi

if [[ -n "$NORMAL_PANEL" && "$CALLER" != "qdnaseq" ]]; then
  NF_CMD+=( --normal_panel "$NORMAL_PANEL" )
fi

if [[ "$CALLER" == "ichorcna" ]]; then
  NF_CMD+=( --ichorcna_gc_wig "$ICHORCNA_GC_WIG" )
  NF_CMD+=( --ichorcna_map_wig "$ICHORCNA_MAP_WIG" )
  [[ -n "$ICHORCNA_CENTROMERE_FILE" ]] && NF_CMD+=( --ichorcna_centromere_file "$ICHORCNA_CENTROMERE_FILE" )
  [[ -n "$ICHORCNA_REPTIME_WIG" ]] && NF_CMD+=( --ichorcna_reptime_wig "$ICHORCNA_REPTIME_WIG" )
fi

[[ "$ANALYSIS_TYPE" == "liquid_biopsy" && "$SIZE_SELECTION" == "true" ]] && NF_CMD+=( --size_selection )
[[ "$RESUME" == "true" ]] && NF_CMD+=( -resume )

echo
echo "Running SAMURAI:"
printf ' %q' "${NF_CMD[@]}"
echo
echo

rescue_ichorcna_partial_outputs() {
  [[ "$CALLER" == "ichorcna" ]] || return 1

  local src sample seg_file
  seg_file="$(find "$NXF_WORK" -type f -name "*.seg.txt" -printf "%T@ %p\n" 2>/dev/null | sort -n | tail -n 1 | cut -d" " -f2-)"
  [[ -n "$seg_file" ]] || return 1
  src="$(dirname "$seg_file")"
  sample="$(basename "$seg_file" .seg.txt)"

  [[ -n "$sample" ]] || return 1
  [[ -s "$src/$sample.seg.txt" ]] || return 1
  [[ -s "$src/$sample.cna.seg" ]] || return 1
  [[ -s "$src/$sample.correctedDepth.txt" ]] || return 1

  echo "WARNING: SAMURAI/ichorCNA failed after writing core outputs; publishing partial ichorCNA tables for $sample" >&2
  mkdir -p "$RUN_ROOT/results/ichorcna"
  cp "$src/$sample.seg.txt" "$RUN_ROOT/results/ichorcna/$sample.seg.txt"
  cp "$src/$sample.seg.txt" "$RUN_ROOT/results/ichorcna/all_segments_ichorcna_gistic.seg"
  cp "$src/$sample.seg.txt" "$RUN_ROOT/results/ichorcna/segments_logR_corrected_gistic.seg"
  cp "$src/$sample.cna.seg" "$RUN_ROOT/results/ichorcna/$sample.cna.seg"
  cp "$src/$sample.correctedDepth.txt" "$RUN_ROOT/results/ichorcna/$sample.correctedDepth.txt"
  [[ -s "$src/$sample.params.txt" ]] && cp "$src/$sample.params.txt" "$RUN_ROOT/results/ichorcna/$sample.params.txt"
}

pushd "$RUN_ROOT/nextflow_launch" >/dev/null
set +e
"${NF_CMD[@]}"
nf_status=$?
set -e
popd >/dev/null
if [[ "$nf_status" -ne 0 ]]; then
  rescue_ichorcna_partial_outputs || exit "$nf_status"
fi


echo
echo "Done."
echo "Results: $RUN_ROOT/results"
echo "BAMs: $RUN_ROOT/bam"
echo "Merged FASTQ.gz: $RUN_ROOT/merged_fastq"
echo "Samplesheet: $SAMPLESHEET"
echo "NORMAL sample manifest: $RUN_ROOT/logs/normal_sample_manifest.tsv"
echo "Logs:"
echo "  $USED_LOG"
echo "  $SKIP_FILE_LOG"
echo "  $SKIP_SAMPLE_LOG"
echo "  $WARN_SAMPLE_LOG"
