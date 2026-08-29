#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

usage() {
  cat <<'EOF_USAGE'
Usage:
  prepare_qdnaseq_bin_data.sh --rscript FILE --binsize KB --cache-dir DIR [--validate-only]
  prepare_qdnaseq_bin_data.sh --rscript FILE --binsize KB --project-root DIR

Download the pinned QDNAseq.hg38 annotation for the requested bin size,
convert it to the RDS format accepted by SAMURAI --qdnaseq_bin_data, validate
it, and print the resulting absolute path. FILE must be the exact Rscript
executable from the native qDNAseq environment. --validate-only performs no
writes and accepts only an already-complete, exactly pinned three-file bundle.
--project-root publishes/reuses that bundle through OncoTracer's marker-owned,
content-addressed reference cache; it never adopts the legacy flat cache.
EOF_USAGE
}

RSCRIPT=""
BINSIZE=""
CACHE_DIR=""
PROJECT_ROOT=""
VALIDATE_ONLY="false"
QDNASEQ_HG38_COMMIT="cf7c07e39de0ac64a9c38cb030cba4626e2aae83"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rscript) RSCRIPT="${2:-}"; shift 2 ;;
    --binsize) BINSIZE="${2:-}"; shift 2 ;;
    --cache-dir) CACHE_DIR="${2:-}"; shift 2 ;;
    --project-root) PROJECT_ROOT="${2:-}"; shift 2 ;;
    --validate-only) VALIDATE_ONLY="true"; shift ;;
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
if [[ -n "$CACHE_DIR" && -n "$PROJECT_ROOT" ]] || [[ -z "$CACHE_DIR" && -z "$PROJECT_ROOT" ]]; then
  echo "ERROR: provide exactly one of --cache-dir or --project-root" >&2
  exit 2
fi

case "$BINSIZE" in
  1) EXPECTED_SOURCE_SHA256="b9ab0152649a913ad44ce38679bc8acd3073c636d9daa2b5926a1b410f666495" ;;
  5) EXPECTED_SOURCE_SHA256="fe897acdbe3555cf13f11e9c210b6cf236838990557777c74eed13b87475635a" ;;
  10) EXPECTED_SOURCE_SHA256="e26904321f93ea081559bcce7d59e3cede224db3eb7069581f0770a0ce138d1f" ;;
  15) EXPECTED_SOURCE_SHA256="f5e516f740c3e8acfbda782214358ea10f4e9b2689d9b76aad3d99c5bcf97849" ;;
  30) EXPECTED_SOURCE_SHA256="71a731557709991e62ea5c224919154cebdadb5f3d6e9f4a287f3999940f1e89" ;;
  50) EXPECTED_SOURCE_SHA256="44440e7d4b6d98fe7b422a5137ca121abe80a3ecafde374e20618d7ee480d054" ;;
  100) EXPECTED_SOURCE_SHA256="450b77a74dbba381e2f664334de90e41ec5e9eb6a5a8946d036c4b3534254d98" ;;
  500) EXPECTED_SOURCE_SHA256="1001b1cc723fb96d3eff54c04285049a13159dfe6100e593c628855dffd12089" ;;
  1000) EXPECTED_SOURCE_SHA256="3dc99f8080bc20b2fcfc737680b37894cf92b1416cd3550900b7811a45b76e42" ;;
  *)
    echo "ERROR: QDNAseq.hg38 does not provide ${BINSIZE} kb SR50 annotations." >&2
    echo "Supported sizes: 1, 5, 10, 15, 30, 50, 100, 500, 1000 kb." >&2
    exit 1
    ;;
esac

if [[ -n "$PROJECT_ROOT" ]]; then
  [[ "$VALIDATE_ONLY" != "true" ]] || {
    echo "ERROR: --validate-only is only valid with --cache-dir" >&2
    exit 2
  }
  REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
  PYTHONPATH="$REPOSITORY_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 - "$REPOSITORY_ROOT" "$PROJECT_ROOT" "$BINSIZE" "$RSCRIPT" <<'PY_OWNED_CACHE'
import sys
from pathlib import Path

from oncotracer_cli.engine import Toolchain, prepare_qdnaseq_annotation
from oncotracer_cli.runtime import CommandRunner, OncoTracerError

repository = Path(sys.argv[1]).resolve(strict=True)
project = Path(sys.argv[2]).expanduser()
binsize = int(sys.argv[3])
rscript = Path(sys.argv[4]).resolve(strict=True)
if rscript.name != "Rscript" or rscript.parent.name != "bin":
    raise SystemExit(f"ERROR: --rscript is not a <prefix>/bin/Rscript executable: {rscript}")
prefix = rscript.parent.parent
try:
    annotation = prepare_qdnaseq_annotation(
        repository,
        project,
        binsize,
        CommandRunner(Path("/dev/null"), echo=False),
        Toolchain(qdnaseq_prefix=prefix),
    )
except OncoTracerError as error:
    raise SystemExit(f"ERROR: {error}") from error
print(annotation)
PY_OWNED_CACHE
  exit 0
fi

if [[ -L "$CACHE_DIR" ]]; then
  echo "ERROR: --cache-dir must not be a symbolic link: $CACHE_DIR" >&2
  exit 1
fi
CACHE_PARENT="$(dirname -- "$CACHE_DIR")"
[[ -d "$CACHE_PARENT" && ! -L "$CACHE_PARENT" ]] || {
  echo "ERROR: --cache-dir parent must be an existing physical directory: $CACHE_PARENT" >&2
  exit 1
}
if [[ ! -e "$CACHE_DIR" ]]; then
  if [[ "$VALIDATE_ONLY" == "true" ]]; then
    echo "ERROR: --validate-only cache does not exist: $CACHE_DIR" >&2
    exit 1
  fi
  mkdir -- "$CACHE_DIR"
fi
[[ -d "$CACHE_DIR" && ! -L "$CACHE_DIR" ]] || {
  echo "ERROR: --cache-dir must be a physical directory: $CACHE_DIR" >&2
  exit 1
}
CACHE_DIR="$(readlink -f -- "$CACHE_DIR")"
if [[ "$VALIDATE_ONLY" != "true" ]] && \
   [[ -n "$(find "$CACHE_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "ERROR: --cache-dir must be empty; publication and reuse are managed by OncoTracer: $CACHE_DIR" >&2
  exit 1
fi
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
  [[ "$expected_source_sha" == "$EXPECTED_SOURCE_SHA256" ]] || return 1
  [[ "$(sha256_of "$RDS_PATH")" == "$expected_rds_sha" ]] || return 1
}

if [[ "$VALIDATE_ONLY" == "true" ]]; then
  if ! validate_provenance || ! validate_rds "$RDS_PATH"; then
    echo "ERROR: existing qDNAseq bundle is not complete and exactly pinned: $CACHE_DIR" >&2
    exit 1
  fi
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
[[ "$SOURCE_SHA256" == "$EXPECTED_SOURCE_SHA256" ]] || {
  echo "ERROR: downloaded qDNAseq source SHA-256 mismatch: expected $EXPECTED_SOURCE_SHA256, observed $SOURCE_SHA256" >&2
  exit 1
}

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

publish_no_clobber() {
  local source="$1" destination="$2"
  if ! mv -n -- "$source" "$destination"; then
    if [[ -e "$destination" || -L "$destination" ]]; then
      echo "ERROR: qDNAseq cache destination appeared during publication; refusing to overwrite: $destination" >&2
    else
      echo "ERROR: could not publish qDNAseq cache file: $destination" >&2
    fi
    return 1
  fi
  if [[ -e "$source" || -L "$source" ]]; then
    echo "ERROR: qDNAseq cache destination appeared during publication; refusing to overwrite: $destination" >&2
    return 1
  fi
}

publish_no_clobber "$RDA_PATH" "$SOURCE_RDA_PATH"
publish_no_clobber "$TMP_RDS" "$RDS_PATH"
publish_no_clobber "$TMP_PROVENANCE" "$PROVENANCE_PATH"

validate_provenance
validate_rds "$RDS_PATH"

printf '%s\n' "$RDS_PATH"
