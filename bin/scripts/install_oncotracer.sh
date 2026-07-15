#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: install_oncotracer.sh \
  --runtime docker|singularity|conda \
  --project-dir DIR \
  --lpwgs-root DIR \
  --docker-image IMAGE \
  --singularity-image URI \
  --samurai-revision REVISION \
  --manifest FILE

Prepare and verify one OncoTracer runtime, cache the pinned SAMURAI workflow,
and write an installation manifest. This command does not download sequencing
reads or hg38 and does not start an analysis.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 ||
    die "required command not found: $command_name"
}

first_line() {
  "$@" 2>&1 | awk 'NF && !seen { print; seen=1 }'
}

RUNTIME=""
PROJECT_DIR=""
LPWGS_ROOT=""
DOCKER_IMAGE=""
SINGULARITY_IMAGE=""
SAMURAI_REVISION="v1.4.0"
MANIFEST="install_manifest.txt"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime) RUNTIME="$2"; shift 2 ;;
    --project-dir) PROJECT_DIR="$2"; shift 2 ;;
    --lpwgs-root) LPWGS_ROOT="$2"; shift 2 ;;
    --docker-image) DOCKER_IMAGE="$2"; shift 2 ;;
    --singularity-image) SINGULARITY_IMAGE="$2"; shift 2 ;;
    --samurai-revision) SAMURAI_REVISION="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$RUNTIME" in
  docker|singularity|conda) ;;
  *) die "--runtime must be docker, singularity, or conda" ;;
esac
[[ -n "$PROJECT_DIR" ]] || die "--project-dir is required"
[[ -n "$LPWGS_ROOT" ]] || die "--lpwgs-root is required"
[[ -d "$PROJECT_DIR" ]] || die "project directory does not exist: $PROJECT_DIR"
[[ -f "$PROJECT_DIR/environment.yml" ]] ||
  die "missing environment.yml under: $PROJECT_DIR"

PROJECT_DIR="$(readlink -m "$PROJECT_DIR")"
LPWGS_ROOT="$(readlink -m "$LPWGS_ROOT")"
MANIFEST="$(readlink -m "$MANIFEST")"
INSTALL_ROOT="$LPWGS_ROOT/.oncotracer"
NXF_ASSETS="$INSTALL_ROOT/nxf-assets"
NXF_HOME="$INSTALL_ROOT/nxf-home"
SINGULARITY_CACHE="$LPWGS_ROOT/.singularity_cache"
mkdir -p "$INSTALL_ROOT" "$NXF_ASSETS" "$NXF_HOME" "$(dirname "$MANIFEST")"
test -w "$INSTALL_ROOT" ||
  die "installation cache is not writable: $INSTALL_ROOT"

for command_name in java nextflow git python3 samtools minimap2 pigz bwa \
  awk readlink sed; do
  require_command "$command_name"
done
if ! command -v curl >/dev/null 2>&1 &&
   ! command -v wget >/dev/null 2>&1; then
  die "curl or wget is required for reference downloads during later analyses"
fi

java_banner="$(first_line java -version)"
java_major="$(printf '%s\n' "$java_banner" |
  sed -nE 's/.*version "([0-9]+).*/\1/p')"
if [[ -z "$java_major" ]]; then
  java_major="$(printf '%s\n' "$java_banner" |
    sed -nE 's/.*openjdk ([0-9]+).*/\1/p')"
fi
[[ "$java_major" =~ ^[0-9]+$ ]] ||
  die "could not determine the Java version from: $java_banner"
(( java_major >= 17 )) ||
  die "Java 17 or newer is required; found: $java_banner"

read -r -d '' SMOKE_COMMAND <<'EOF_SMOKE' || true
set -Eeuo pipefail
java -version
nextflow -version
python -c "import matplotlib, numpy, pandas, pysam, scipy"
samtools --version | sed -n "1p"
minimap2 --version
pigz --version
EOF_SMOKE

runtime_identity=""
runtime_location=""

case "$RUNTIME" in
  docker)
    [[ -n "$DOCKER_IMAGE" ]] ||
      die "--docker-image is required for the Docker runtime"
    require_command docker
    docker info >/dev/null 2>&1 ||
      die "Docker is installed, but this user cannot access a running Docker daemon"
    echo "Pulling Docker image: $DOCKER_IMAGE"
    docker pull "$DOCKER_IMAGE"
    echo "Testing the Docker analysis environment"
    docker run --rm --entrypoint bash "$DOCKER_IMAGE" -lc "$SMOKE_COMMAND"
    runtime_identity="$(docker image inspect "$DOCKER_IMAGE" \
      --format '{{index .RepoDigests 0}}')"
    if [[ -z "$runtime_identity" || "$runtime_identity" == '<no value>' ]]; then
      runtime_identity="$(docker image inspect "$DOCKER_IMAGE" \
        --format '{{.Id}}')"
    fi
    runtime_location="$DOCKER_IMAGE"
    ;;

  singularity)
    [[ -n "$SINGULARITY_IMAGE" ]] ||
      die "--singularity-image is required for the Singularity runtime"
    if command -v apptainer >/dev/null 2>&1; then
      singularity_command="apptainer"
    elif command -v singularity >/dev/null 2>&1; then
      singularity_command="singularity"
    else
      die "Apptainer or Singularity is required for --singularity"
    fi
    require_command sha256sum
    mkdir -p "$SINGULARITY_CACHE"
    image_token="$(printf '%s\n' "$SINGULARITY_IMAGE" |
      sed 's#^docker://##')"
    image_registry="$(printf '%s\n' "$image_token" | sed 's#/.*##')"
    if [[ "$image_token" != */* ]]; then
      image_token="docker.io/library/$image_token"
    elif [[ "$image_registry" != *.* &&
            "$image_registry" != *:* &&
            "$image_registry" != 'localhost' ]]; then
      image_token="docker.io/$image_token"
    fi
    cache_name="$(printf '%s\n' "$image_token" | sed 's#[/:@]#-#g').img"
    runtime_location="$SINGULARITY_CACHE/$cache_name"
    if [[ -s "$runtime_location" ]] &&
       "$singularity_command" inspect "$runtime_location" >/dev/null 2>&1; then
      echo "Reusing cached Singularity image: $runtime_location"
    else
      image_tmp="$runtime_location.tmp.$$"
      trap 'rm -f -- "$image_tmp"' EXIT
      echo "Pulling Singularity image: $SINGULARITY_IMAGE"
      "$singularity_command" pull "$image_tmp" "$SINGULARITY_IMAGE"
      "$singularity_command" inspect "$image_tmp" >/dev/null
      mv -f "$image_tmp" "$runtime_location"
      image_tmp=""
      trap - EXIT
    fi
    echo "Testing the Singularity analysis environment"
    "$singularity_command" exec "$runtime_location" \
      bash -lc "$SMOKE_COMMAND"
    runtime_identity="sha256:$(sha256sum "$runtime_location" |
      awk '{print $1}')"
    ;;

  conda)
    require_command conda
    conda_prefix="$(printenv CONDA_PREFIX || true)"
    [[ -n "$conda_prefix" ]] ||
      die "the Nextflow-managed OncoTracer Conda environment was not activated"
    echo "Testing the Conda analysis environment: $conda_prefix"
    bash -c "$SMOKE_COMMAND"
    require_command sha256sum
    conda_lock="$(mktemp)"
    trap 'rm -f -- "$conda_lock"' EXIT
    conda list --explicit > "$conda_lock"
    runtime_identity="explicit-spec-sha256:$(sha256sum "$conda_lock" |
      awk '{print $1}')"
    runtime_location="$conda_prefix"
    rm -f "$conda_lock"
    conda_lock=""
    trap - EXIT
    ;;
esac

echo "Caching SAMURAI $SAMURAI_REVISION under: $NXF_ASSETS"
export NXF_ASSETS NXF_HOME
nextflow pull dincalcilab/samurai -r "$SAMURAI_REVISION"
SAMURAI_DIR="$NXF_ASSETS/dincalcilab/samurai"
SAMURAI_BARE_REPO="$NXF_ASSETS/.repos/dincalcilab/samurai/bare"
if [[ -d "$SAMURAI_DIR/.git" ]]; then
  samurai_commit="$(git -C "$SAMURAI_DIR" rev-list -n 1 "$SAMURAI_REVISION")"
  samurai_cache_repository="$SAMURAI_DIR"
elif [[ -f "$SAMURAI_BARE_REPO/HEAD" ]]; then
  samurai_commit="$(git --git-dir "$SAMURAI_BARE_REPO" \
    rev-list -n 1 "$SAMURAI_REVISION")"
  samurai_cache_repository="$SAMURAI_BARE_REPO"
else
  die "SAMURAI cache was not created below: $NXF_ASSETS"
fi
[[ -n "$samurai_commit" ]] ||
  die "could not resolve cached SAMURAI revision: $SAMURAI_REVISION"
nextflow_banner="$(nextflow -version 2>&1 |
  awk '/version [0-9]/ && !seen { print; seen=1 }')"
[[ -n "$nextflow_banner" ]] || nextflow_banner="$(first_line nextflow -version)"

manifest_tmp="$MANIFEST.tmp.$$"
trap 'rm -f -- "$manifest_tmp"' EXIT
{
  echo "status=ok"
  echo "runtime=$RUNTIME"
  echo "runtime_location=$runtime_location"
  echo "runtime_identity=$runtime_identity"
  echo "java=$java_banner"
  echo "nextflow=$nextflow_banner"
  echo "python=$(first_line python3 --version)"
  echo "samtools=$(first_line samtools --version)"
  echo "samurai_revision=$SAMURAI_REVISION"
  echo "samurai_commit=$samurai_commit"
  echo "samurai_assets=$NXF_ASSETS"
  echo "samurai_cache_repository=$samurai_cache_repository"
  echo "hg38_prepared=false"
  echo "reads_downloaded=false"
  echo "analysis_started=false"
  echo "installed_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$manifest_tmp"
mv -f "$manifest_tmp" "$MANIFEST"
manifest_tmp=""
trap - EXIT

echo "OncoTracer installation check completed successfully."
echo "Manifest: $MANIFEST"
echo "No sequencing reads, reference genome, or analysis outputs were created."
