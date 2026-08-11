#!/usr/bin/env bash
set -Eeuo pipefail

# Legacy v1.1 comparator/source support only. Native v2 uses the committed
# native_qdnaseq_pon.R execution path and does not package this wrapper.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
R_SCRIPT="$SCRIPT_DIR/qdnaseq_local_pon.R"

SAMPLESHEET=""
OUTDIR=""
BINSIZE=100
GENOME=hg38
MIN_MAPQ=37
MIN_NORMALS=2
PAIRED_ENDS=true
PON_NAME=Illumina_local_PoN
QDNASEQ_BIN_DATA=""
PROFILE=auto
LPWGS_ROOT="${LPWGS_ROOT:-$PWD}"
CONTAINER_IMAGE="${QDNASEQ_R_CONTAINER:-}"
DEFAULT_CONTAINER="docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1"
SELF_TEST=false
declare -a EXTRA_BINDS=()

usage() {
  cat <<'EOF_USAGE'
Usage:
  run_qdnaseq_local_pon.sh --samplesheet FILE --outdir DIR [options]
  run_qdnaseq_local_pon.sh --self-test [--profile auto|host|conda|singularity|docker]

Build and apply a cohort-local qDNAseq panel of normals. The input CSV must
contain sample,bam,status, where status is tumor or normal. All BAMs are counted
together using the same MAPQ and paired-end settings. Only tumor profiles are
exported as SAMURAI-compatible downstream outputs.

Analysis options:
  --samplesheet FILE              CSV with sample,bam,status
  --outdir DIR                    qdnaseq_local_pon output directory
  --binsize N                     qDNAseq bin size in kbp [100]
  --genome NAME                   qDNAseq genome annotation [hg38]
  --min-mapq N                    minimum mapping quality [37]
  --min-normals N                 required number of normal BAMs [2]
  --paired-ends true|false        paired-end counting [true]
  --pon-name NAME                 PoN/provenance name [Illumina_local_PoN]
  --qdnaseq-bin-data PATH         optional QDNAseq annotation path

Runtime options:
  --profile NAME                  auto, host, conda, singularity, docker [auto]
  --lpwgs-root DIR                common data/cache root
  --container IMAGE               local SIF/IMG or container URI
  --qdnaseq-r-container IMAGE     alias for --container
  --bind DIR                      additional container bind; repeatable

Runtime selection:
  auto prefers a host R with QDNAseq, then Singularity/Apptainer, then Docker.
  Singularity/Apptainer first reuses qDNAseq_1.30.0-a28ebc1 from the local SIF
  cache and only falls back to the container URI when no cached image exists.

Validation:
  --self-test                     run deterministic PoN mathematics without BAMs
  -h, --help                      show this help
EOF_USAGE
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

normalize_bool() {
  local value="${1,,}"
  case "$value" in
    true|t|1|yes|y|on) printf 'true\n' ;;
    false|f|0|no|n|off) printf 'false\n' ;;
    *) die "expected true or false, found: $1" ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --samplesheet) SAMPLESHEET="${2:-}"; shift 2 ;;
    --outdir) OUTDIR="${2:-}"; shift 2 ;;
    --binsize) BINSIZE="${2:-}"; shift 2 ;;
    --genome) GENOME="${2:-}"; shift 2 ;;
    --min-mapq) MIN_MAPQ="${2:-}"; shift 2 ;;
    --min-normals) MIN_NORMALS="${2:-}"; shift 2 ;;
    --paired-ends) PAIRED_ENDS="${2:-}"; shift 2 ;;
    --pon-name) PON_NAME="${2:-}"; shift 2 ;;
    --qdnaseq-bin-data) QDNASEQ_BIN_DATA="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --lpwgs-root) LPWGS_ROOT="${2:-}"; shift 2 ;;
    --container|--qdnaseq-r-container) CONTAINER_IMAGE="${2:-}"; shift 2 ;;
    --bind) EXTRA_BINDS+=("${2:-}"); shift 2 ;;
    --self-test) SELF_TEST=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -s "$R_SCRIPT" ]] || die "R helper not found: $R_SCRIPT"
case "$PROFILE" in
  auto|host|conda|singularity|docker) ;;
  *) die "--profile must be auto, host, conda, singularity, or docker" ;;
esac
[[ "$BINSIZE" =~ ^[1-9][0-9]*$ ]] || die "--binsize must be a positive integer"
[[ "$MIN_MAPQ" =~ ^[0-9]+$ ]] || die "--min-mapq must be a non-negative integer"
[[ "$MIN_NORMALS" =~ ^[0-9]+$ && "$MIN_NORMALS" -ge 2 ]] || {
  die "--min-normals must be an integer >= 2 for leave-one-out normal QC"
}
[[ -n "$GENOME" ]] || die "--genome cannot be empty"
[[ "$PON_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || {
  die "--pon-name must match ^[A-Za-z0-9][A-Za-z0-9_.-]*$"
}
PAIRED_ENDS="$(normalize_bool "$PAIRED_ENDS")"
LPWGS_ROOT="$(readlink -m -- "$LPWGS_ROOT")"

if [[ "$SELF_TEST" != "true" ]]; then
  [[ -n "$SAMPLESHEET" ]] || die "--samplesheet is required"
  [[ -n "$OUTDIR" ]] || die "--outdir is required"
  [[ -s "$SAMPLESHEET" ]] || die "samplesheet not found or empty: $SAMPLESHEET"
  SAMPLESHEET="$(readlink -m -- "$SAMPLESHEET")"
  OUTDIR="$(readlink -m -- "$OUTDIR")"
  mkdir -p -- "$OUTDIR"
  if [[ -n "$QDNASEQ_BIN_DATA" ]]; then
    [[ -e "$QDNASEQ_BIN_DATA" ]] || die "qDNAseq bin data not found: $QDNASEQ_BIN_DATA"
    QDNASEQ_BIN_DATA="$(readlink -m -- "$QDNASEQ_BIN_DATA")"
  fi
fi

host_r_has_qdnaseq() {
  command -v Rscript >/dev/null 2>&1 || return 1
  Rscript -e 'quit(save="no", status=ifelse(requireNamespace("QDNAseq", quietly=TRUE), 0, 1))' \
    >/dev/null 2>&1
}

find_container_runner() {
  if command -v singularity >/dev/null 2>&1; then
    command -v singularity
  elif command -v apptainer >/dev/null 2>&1; then
    command -v apptainer
  else
    return 1
  fi
}

find_cached_qdnaseq_image() {
  local cache_dir="$LPWGS_ROOT/.singularity_cache"
  local candidate

  if [[ -n "$CONTAINER_IMAGE" && -f "$CONTAINER_IMAGE" ]]; then
    readlink -m -- "$CONTAINER_IMAGE"
    return 0
  fi

  for candidate in \
    "$cache_dir/qdnaseq_1.30.0-a28ebc1.sif" \
    "$cache_dir/quay.io-dincalcilab-qdnaseq-1.30.0-a28ebc1.img" \
    "$cache_dir/qdnaseq_1.30.0-a28ebc1.img"; do
    if [[ -s "$candidate" ]]; then
      readlink -m -- "$candidate"
      return 0
    fi
  done

  if [[ -d "$cache_dir" ]]; then
    candidate="$(find "$cache_dir" -maxdepth 2 -type f \
      \( -iname '*qdnaseq*1.30.0*.sif' -o -iname '*qdnaseq*1.30.0*.img' \) \
      -print -quit 2>/dev/null || true)"
    if [[ -n "$candidate" && -s "$candidate" ]]; then
      readlink -m -- "$candidate"
      return 0
    fi
  fi
  return 1
}

select_runtime() {
  case "$PROFILE" in
    host|conda)
      if [[ "$SELF_TEST" == "true" ]]; then
        command -v Rscript >/dev/null 2>&1 || die "Rscript is required for --profile $PROFILE"
      else
        host_r_has_qdnaseq || die "host Rscript with QDNAseq is required for --profile $PROFILE"
      fi
      printf 'host\n'
      ;;
    singularity)
      find_container_runner >/dev/null || die "neither singularity nor apptainer is available"
      printf 'singularity\n'
      ;;
    docker)
      command -v docker >/dev/null 2>&1 || die "docker is not available"
      printf 'docker\n'
      ;;
    auto)
      if [[ "$SELF_TEST" == "true" ]] && command -v Rscript >/dev/null 2>&1; then
        printf 'host\n'
      elif host_r_has_qdnaseq; then
        printf 'host\n'
      elif find_container_runner >/dev/null 2>&1; then
        printf 'singularity\n'
      elif command -v docker >/dev/null 2>&1; then
        printf 'docker\n'
      else
        die "no usable host R, Singularity/Apptainer, or Docker runtime found"
      fi
      ;;
  esac
}

declare -a R_ARGS=()
if [[ "$SELF_TEST" == "true" ]]; then
  R_ARGS=(--self-test)
else
  R_ARGS=(
    --samplesheet "$SAMPLESHEET"
    --outdir "$OUTDIR"
    --binsize "$BINSIZE"
    --genome "$GENOME"
    --min-mapq "$MIN_MAPQ"
    --min-normals "$MIN_NORMALS"
    --paired-ends "$PAIRED_ENDS"
    --pon-name "$PON_NAME"
  )
  [[ -n "$QDNASEQ_BIN_DATA" ]] && R_ARGS+=(--qdnaseq-bin-data "$QDNASEQ_BIN_DATA")
fi

declare -a BIND_DIRS=()
add_bind_dir() {
  local candidate="${1:-}" resolved existing
  [[ -n "$candidate" ]] || return 0
  resolved="$(readlink -m -- "$candidate")"
  if [[ -e "$resolved" && ! -d "$resolved" ]]; then
    resolved="$(dirname -- "$resolved")"
  fi
  [[ -d "$resolved" ]] || return 0
  if (( ${#BIND_DIRS[@]} > 0 )); then
    for existing in "${BIND_DIRS[@]}"; do
      [[ "$existing" == "$resolved" ]] && return 0
    done
  fi
  BIND_DIRS+=("$resolved")
}

add_bind_dir "$LPWGS_ROOT"
add_bind_dir "$SCRIPT_DIR"
if [[ "$SELF_TEST" != "true" ]]; then
  add_bind_dir "$(dirname -- "$SAMPLESHEET")"
  add_bind_dir "$OUTDIR"
  [[ -z "$QDNASEQ_BIN_DATA" ]] || add_bind_dir "$QDNASEQ_BIN_DATA"
fi
if (( ${#EXTRA_BINDS[@]} > 0 )); then
  for bind_dir in "${EXTRA_BINDS[@]}"; do
    [[ -d "$bind_dir" ]] || die "--bind directory not found: $bind_dir"
    add_bind_dir "$bind_dir"
  done
fi

runtime="$(select_runtime)"
declare -a COMMAND=()

case "$runtime" in
  host)
    COMMAND=(Rscript "$R_SCRIPT" "${R_ARGS[@]}")
    ;;
  singularity)
    runner="$(find_container_runner)"
    if cached_image="$(find_cached_qdnaseq_image)"; then
      image="$cached_image"
      echo "Using cached qDNAseq image: $image" >&2
    else
      image="${CONTAINER_IMAGE:-$DEFAULT_CONTAINER}"
      echo "No cached qDNAseq SIF/IMG found; using: $image" >&2
    fi
    COMMAND=("$runner" exec)
    for bind_dir in "${BIND_DIRS[@]}"; do
      COMMAND+=(--bind "$bind_dir:$bind_dir")
    done
    COMMAND+=("$image" Rscript "$R_SCRIPT" "${R_ARGS[@]}")
    ;;
  docker)
    image="${CONTAINER_IMAGE:-$DEFAULT_CONTAINER}"
    image="${image#docker://}"
    [[ "$image" != /* ]] || die "Docker profile requires an image name/URI, not a local SIF/IMG"
    COMMAND=(docker run --rm --entrypoint Rscript)
    if command -v id >/dev/null 2>&1; then
      COMMAND+=(--user "$(id -u):$(id -g)")
    fi
    for bind_dir in "${BIND_DIRS[@]}"; do
      COMMAND+=(--volume "$bind_dir:$bind_dir")
    done
    COMMAND+=("$image" "$R_SCRIPT" "${R_ARGS[@]}")
    ;;
esac

echo "qDNAseq local PoN runtime: $runtime" >&2
printf 'Running:' >&2
printf ' %q' "${COMMAND[@]}" >&2
printf '\n' >&2
"${COMMAND[@]}"
