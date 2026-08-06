#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF_USAGE'
Usage: prepare_qdnaseq_bin_data.sh --rscript FILE --binsize KB --cache-dir DIR

Download the pinned QDNAseq.hg38 annotation for the requested bin size,
convert it to the RDS format accepted by SAMURAI --qdnaseq_bin_data, validate
it, and print the resulting absolute path. FILE must be the exact Rscript
executable from the native qDNAseq environment.
EOF_USAGE
}

RSCRIPT=""
BINSIZE=""
CACHE_DIR=""
QDNASEQ_HG38_COMMIT="cf7c07e39de0ac64a9c38cb030cba4626e2aae83"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rscript) RSCRIPT="${2:-}"; shift 2 ;;
    --binsize) BINSIZE="${2:-}"; shift 2 ;;
    --cache-dir) CACHE_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$RSCRIPT" ]] || { echo "ERROR: --rscript is required" >&2; exit 2; }
[[ "$RSCRIPT" == /* ]] || { echo "ERROR: --rscript must be an absolute path" >&2; exit 2; }
[[ -f "$RSCRIPT" && -x "$RSCRIPT" ]] || {
  echo "ERROR: --rscript is not an executable file: $RSCRIPT" >&2
  exit 1
}
RSCRIPT="$(readlink -f -- "$RSCRIPT")"
[[ "$BINSIZE" =~ ^[0-9]+$ ]] || { echo "ERROR: --binsize must be an integer" >&2; exit 2; }
[[ -n "$CACHE_DIR" ]] || { echo "ERROR: --cache-dir is required" >&2; exit 2; }

case "$BINSIZE" in
  1|5|10|15|30|50|100|500|1000) ;;
  *)
    echo "ERROR: QDNAseq.hg38 does not provide ${BINSIZE} kb SR50 annotations." >&2
    echo "Supported sizes: 1, 5, 10, 15, 30, 50, 100, 500, 1000 kb." >&2
    exit 1
    ;;
esac

CACHE_DIR="$(readlink -m -- "$CACHE_DIR")"
mkdir -p -- "$CACHE_DIR"
OBJECT_NAME="hg38.${BINSIZE}kbp.SR50"
RDS_PATH="$CACHE_DIR/QDNAseq.hg38.${BINSIZE}kbp.SR50.rds"
SOURCE_RDA_PATH="$CACHE_DIR/QDNAseq.hg38.${BINSIZE}kbp.SR50.source.rda"
PROVENANCE_PATH="$RDS_PATH.provenance.tsv"
URL="https://raw.githubusercontent.com/asntech/QDNAseq.hg38/${QDNASEQ_HG38_COMMIT}/data/${OBJECT_NAME}.rda"

clean_rscript() {
  env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
    "$RSCRIPT" --vanilla "$@"
}

sha256_of() {
  sha256sum -- "$1" | awk '{print $1}'
}

provenance_value() {
  local key="$1"
  awk -F '\t' -v key="$key" '$1 == key { print $2 }' "$PROVENANCE_PATH"
}

validate_rds() {
  local path="$1"
  [[ -s "$path" ]] || return 1
  clean_rscript - "$path" <<'RS_VALIDATE' >/dev/null
args <- commandArgs(trailingOnly = TRUE)
suppressPackageStartupMessages(library(Biobase))
obj <- readRDS(args[[1]])
if (!inherits(obj, "AnnotatedDataFrame")) {
  stop("Expected an AnnotatedDataFrame, found: ", paste(class(obj), collapse = ","))
}
if (nrow(obj) < 1L) stop("The annotation contains no bins")
required <- c("chromosome", "start", "end")
missing <- setdiff(required, colnames(pData(obj)))
if (length(missing)) stop("Annotation is missing columns: ", paste(missing, collapse = ","))
RS_VALIDATE
}

validate_provenance() {
  [[ -s "$PROVENANCE_PATH" && -s "$SOURCE_RDA_PATH" && -s "$RDS_PATH" ]] || return 1
  [[ "$(provenance_value source_url)" == "$URL" ]] || return 1
  [[ "$(provenance_value source_commit)" == "$QDNASEQ_HG38_COMMIT" ]] || return 1
  [[ "$(provenance_value object)" == "$OBJECT_NAME" ]] || return 1

  local expected_source_sha expected_rds_sha
  expected_source_sha="$(provenance_value source_rda_sha256)"
  expected_rds_sha="$(provenance_value rds_sha256)"
  [[ "$expected_source_sha" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ "$expected_rds_sha" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ "$(sha256_of "$SOURCE_RDA_PATH")" == "$expected_source_sha" ]] || return 1
  [[ "$(sha256_of "$RDS_PATH")" == "$expected_rds_sha" ]] || return 1
}

if validate_provenance && validate_rds "$RDS_PATH"; then
  printf '%s\n' "$RDS_PATH"
  exit 0
fi

TMP_DIR="$(mktemp -d "$CACHE_DIR/.qdnaseq-bin-data.XXXXXX")"
cleanup() {
  rm -rf -- "$TMP_DIR"
}
trap cleanup EXIT
RDA_PATH="$TMP_DIR/${OBJECT_NAME}.rda"
TMP_RDS="$TMP_DIR/${OBJECT_NAME}.rds"
TMP_PROVENANCE="$TMP_DIR/provenance.tsv"

if command -v curl >/dev/null 2>&1; then
  curl --fail --location --retry 5 --retry-all-errors \
    --output "$RDA_PATH" "$URL"
elif command -v wget >/dev/null 2>&1; then
  wget --tries=5 --output-document="$RDA_PATH" "$URL"
else
  echo "ERROR: curl or wget is required to obtain the qDNAseq annotation" >&2
  exit 1
fi
[[ -s "$RDA_PATH" ]] || { echo "ERROR: downloaded annotation is empty: $URL" >&2; exit 1; }
SOURCE_SHA256="$(sha256_of "$RDA_PATH")"

clean_rscript - "$RDA_PATH" "$TMP_RDS" "$OBJECT_NAME" <<'RS_CONVERT'
args <- commandArgs(trailingOnly = TRUE)
rda_path <- args[[1]]
rds_path <- args[[2]]
object_name <- args[[3]]
suppressPackageStartupMessages(library(Biobase))
env <- new.env(parent = globalenv())
loaded <- load(rda_path, envir = env)
if (!object_name %in% loaded || !exists(object_name, envir = env, inherits = FALSE)) {
  stop("Expected object ", object_name, "; loaded: ", paste(loaded, collapse = ","))
}
obj <- get(object_name, envir = env, inherits = FALSE)
if (!inherits(obj, "AnnotatedDataFrame")) {
  stop("Expected an AnnotatedDataFrame, found: ", paste(class(obj), collapse = ","))
}
if (nrow(obj) < 1L) stop("The annotation contains no bins")
saveRDS(obj, rds_path, compress = "xz")
RS_CONVERT

validate_rds "$TMP_RDS"
RDS_SHA256="$(sha256_of "$TMP_RDS")"
{
  printf 'field\tvalue\n'
  printf 'source_url\t%s\n' "$URL"
  printf 'source_commit\t%s\n' "$QDNASEQ_HG38_COMMIT"
  printf 'source_rda_sha256\t%s\n' "$SOURCE_SHA256"
  printf 'object\t%s\n' "$OBJECT_NAME"
  printf 'rds_sha256\t%s\n' "$RDS_SHA256"
} > "$TMP_PROVENANCE"

mv -f -- "$RDA_PATH" "$SOURCE_RDA_PATH"
mv -f -- "$TMP_RDS" "$RDS_PATH"
mv -f -- "$TMP_PROVENANCE" "$PROVENANCE_PATH"

printf '%s\n' "$RDS_PATH"
