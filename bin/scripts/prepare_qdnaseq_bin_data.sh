#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF_USAGE'
Usage: prepare_qdnaseq_bin_data.sh --binsize KB --cache-dir DIR

Download the pinned QDNAseq.hg38 annotation for the requested bin size,
convert it to the RDS format accepted by SAMURAI --qdnaseq_bin_data, validate
it, and print the resulting absolute path.
EOF_USAGE
}

BINSIZE=""
CACHE_DIR=""
QDNASEQ_HG38_COMMIT="cf7c07e39de0ac64a9c38cb030cba4626e2aae83"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --binsize) BINSIZE="${2:-}"; shift 2 ;;
    --cache-dir) CACHE_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$BINSIZE" =~ ^[0-9]+$ ]] || { echo "ERROR: --binsize must be an integer" >&2; exit 2; }
[[ -n "$CACHE_DIR" ]] || { echo "ERROR: --cache-dir is required" >&2; exit 2; }
command -v Rscript >/dev/null 2>&1 || { echo "ERROR: Rscript is required" >&2; exit 1; }

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
PROVENANCE_PATH="$RDS_PATH.provenance.tsv"
URL="https://raw.githubusercontent.com/asntech/QDNAseq.hg38/${QDNASEQ_HG38_COMMIT}/data/${OBJECT_NAME}.rda"

validate_rds() {
  local path="$1"
  [[ -s "$path" ]] || return 1
  Rscript --vanilla - "$path" <<'RS_VALIDATE' >/dev/null
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

if validate_rds "$RDS_PATH"; then
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

Rscript --vanilla - "$RDA_PATH" "$TMP_RDS" "$OBJECT_NAME" <<'RS_CONVERT'
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
mv -f -- "$TMP_RDS" "$RDS_PATH"
SHA256="$(sha256sum "$RDS_PATH" | awk '{print $1}')"
{
  printf 'field\tvalue\n'
  printf 'source_url\t%s\n' "$URL"
  printf 'source_commit\t%s\n' "$QDNASEQ_HG38_COMMIT"
  printf 'object\t%s\n' "$OBJECT_NAME"
  printf 'rds_sha256\t%s\n' "$SHA256"
} > "$PROVENANCE_PATH.tmp"
mv -f -- "$PROVENANCE_PATH.tmp" "$PROVENANCE_PATH"

printf '%s\n' "$RDS_PATH"
