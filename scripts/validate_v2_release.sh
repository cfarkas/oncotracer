#!/usr/bin/env bash
# Complete server-side validation for the native OncoTracer v2 release.
#
# The script never removes data.  A non-empty validation root is accepted only
# with --resume, and stage reuse is gated by content-derived signatures plus
# output verification.  Nextflow appears only in the frozen v1.1 comparator
# stages; every candidate analysis is launched through the copied v2 zipapp.
set -Eeuo pipefail
umask 022

readonly V1_TAG="v1.1"
readonly V1_COMMIT_EXPECTED="032c1268fa7fdcadc48087055066d7a9fc59bd89"
readonly V1_DOCKER_IMAGE="carlosfarkas/oncotracer@sha256:4856aed020e1102f891b91de54d6acf365d6b8a57e2283a4f7b670b0bd5b07ed"
readonly SAMURAI_REVISION="v1.4.0"
readonly SAMURAI_COMMIT_EXPECTED="6a901940288b008237703c6b181d447e7dee4fcf"
readonly SAMURAI_PREPARED_MAIN_SHA256="d566f98d117dc50d1c1cde95861c0da479c3dda4e40c7482c6b0f41436b3b62a"
readonly NEXTFLOW_VERSION="26.04.6"
readonly NEXTFLOW_URL="https://github.com/nextflow-io/nextflow/releases/download/v26.04.6/nextflow-26.04.6-dist"
readonly NEXTFLOW_SHA256="182a63c74074e2dc7956ffa3c8cd59de952ed2c44394e21faf5e1736b945444c"
readonly NEXTFLOW_SIZE="42355106"
readonly DRIVER_SCHEMA="oncotracer-v2-release-validation-v1"

usage() {
  cat <<'EOF'
Usage:
  scripts/validate_v2_release.sh \
    --validation-root /large/dedicated/oncotracer-v2-validation \
    --threads 16 \
    --shared-reference /path/to/references/samurai_hg38 \
    [--resume]

Required:
  --validation-root DIR  Dedicated directory for environments, inputs, runs,
                         logs, and audits. Empty, /, the repository checkout,
                         and every path inside that checkout are rejected.
  --threads N            Positive analysis thread count.
  --shared-reference DIR Complete hg38 cache containing genome.fa, faidx,
                         Picard dictionary, BWA index, and minimap2 index.

Optional:
  --resume               Reuse only stages whose source, command, inputs,
                         environment identity, and verified outputs match.
  -h, --help             Show this help.

Run long validations in a dedicated tmux session, for example:
  tmux new-session -s oncotracer-v2-validation
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

require_value() {
  local option="$1" value="${2-}"
  [[ -n "$value" ]] || die "$option requires a non-empty value"
}

VALIDATION_ROOT_ARG=""
THREADS=""
SHARED_REFERENCE_ARG="${ONCOTRACER_SHARED_REFERENCE:-}"
RESUME=false
while (($#)); do
  case "$1" in
    --validation-root)
      require_value "$1" "${2-}"
      VALIDATION_ROOT_ARG="$2"
      shift 2
      ;;
    --threads)
      require_value "$1" "${2-}"
      THREADS="$2"
      shift 2
      ;;
    --shared-reference)
      require_value "$1" "${2-}"
      SHARED_REFERENCE_ARG="$2"
      shift 2
      ;;
    --resume)
      RESUME=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

[[ -n "$VALIDATION_ROOT_ARG" ]] || die "--validation-root is required and cannot be empty"
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] || die "--threads must be a positive integer"
[[ -n "$SHARED_REFERENCE_ARG" ]] || die "--shared-reference is required"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
VALIDATION_ROOT="$(readlink -m -- "$VALIDATION_ROOT_ARG")"
SHARED_REFERENCE="$(readlink -m -- "$SHARED_REFERENCE_ARG")"

[[ "$VALIDATION_ROOT" != "/" ]] || die "the filesystem root cannot be a validation root"
case "$VALIDATION_ROOT/" in
  "$REPOSITORY_ROOT/"*)
    die "the validation root cannot be the repository checkout or a path inside it"
    ;;
esac
case "$VALIDATION_ROOT/" in
  "$SHARED_REFERENCE/"*)
    die "the validation root cannot be inside the shared reference"
    ;;
esac
case "$SHARED_REFERENCE/" in
  "$VALIDATION_ROOT/"*)
    die "the shared reference cannot be inside the validation root"
    ;;
esac
[[ -d "$SHARED_REFERENCE" ]] || die "shared reference directory does not exist: $SHARED_REFERENCE"

if [[ -d "$VALIDATION_ROOT" ]] && [[ "$RESUME" != true ]] &&
   [[ -n "$(find "$VALIDATION_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  die "validation root is not empty; use a new directory or pass --resume: $VALIDATION_ROOT"
fi
if [[ -d "$VALIDATION_ROOT" ]] && [[ "$RESUME" == true ]] &&
   [[ -n "$(find "$VALIDATION_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  [[ -s "$VALIDATION_ROOT/.oncotracer-v2-release-validation-root" ]] ||
    die "refusing to resume an unrecognized non-empty directory: $VALIDATION_ROOT"
  grep -Fx "schema=$DRIVER_SCHEMA" \
    "$VALIDATION_ROOT/.oncotracer-v2-release-validation-root" >/dev/null ||
    die "validation-root sentinel has an incompatible schema: $VALIDATION_ROOT"
fi

for command_name in \
  awk bash chmod cmp conda cp curl date df docker find git grep gzip hostname java md5sum mktemp mv \
  nproc python3 readlink sed sha256sum sort stat tar tee uname xargs; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command not found: $command_name"
done

git -C "$REPOSITORY_ROOT" diff --quiet -- || die "tracked source changes are present; commit them before release validation"
git -C "$REPOSITORY_ROOT" diff --cached --quiet -- || die "staged source changes are present; commit them before release validation"
[[ -z "$(git -C "$REPOSITORY_ROOT" status --porcelain=v1 --untracked-files=all)" ]] ||
  die "the release-validation checkout must be completely clean"

SOURCE_COMMIT="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
SOURCE_SHA256="$(git -C "$REPOSITORY_ROOT" -c tar.umask=0002 archive --format=tar "$SOURCE_COMMIT" | sha256sum | awk '{print $1}')"
V1_COMMIT="$(git -C "$REPOSITORY_ROOT" rev-parse --verify "refs/tags/${V1_TAG}^{commit}")"
[[ -n "$V1_COMMIT" ]] || die "cannot resolve frozen comparator tag: $V1_TAG"
[[ "$V1_COMMIT" == "$V1_COMMIT_EXPECTED" ]] ||
  die "frozen comparator tag $V1_TAG resolved to $V1_COMMIT, expected $V1_COMMIT_EXPECTED"
V1_SOURCE_SHA256="$(git -C "$REPOSITORY_ROOT" -c tar.umask=0002 archive --format=tar "$V1_COMMIT" | sha256sum | awk '{print $1}')"

verify_source_checkout() {
  local observed_commit observed_sha256
  observed_commit="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
  [[ "$observed_commit" == "$SOURCE_COMMIT" ]] || {
    printf 'Release checkout HEAD changed during validation: expected %s, observed %s\n' \
      "$SOURCE_COMMIT" "$observed_commit" >&2
    return 1
  }
  git -C "$REPOSITORY_ROOT" diff --quiet -- || return 1
  git -C "$REPOSITORY_ROOT" diff --cached --quiet -- || return 1
  [[ -z "$(git -C "$REPOSITORY_ROOT" status --porcelain=v1 --untracked-files=all)" ]] || return 1
  observed_sha256="$(
    git -C "$REPOSITORY_ROOT" -c tar.umask=0002 archive --format=tar "$observed_commit" |
      sha256sum | awk '{print $1}'
  )"
  [[ "$observed_sha256" == "$SOURCE_SHA256" ]] || {
    printf 'Release checkout source SHA-256 changed during validation.\n' >&2
    return 1
  }
}

readonly CONTEXT_DIR="$VALIDATION_ROOT/context"
readonly LOG_DIR="$VALIDATION_ROOT/logs"
readonly STATE_DIR="$VALIDATION_ROOT/state"
readonly TMP_DIR="$VALIDATION_ROOT/tmp"
readonly RELEASE_CANDIDATE_DIR="$VALIDATION_ROOT/release-candidate"
# A source-bound root prevents a resumed validation from adopting or mutating
# legacy unmarked environments created by an earlier candidate.
readonly ENV_ROOT="$VALIDATION_ROOT/managed-envs-by-source/$SOURCE_COMMIT"
readonly INPUT_ROOT="$VALIDATION_ROOT/inputs"
readonly ANALYSIS_ROOT="$VALIDATION_ROOT/analysis"
readonly CONFIG_DIR="$ANALYSIS_ROOT/configs"
readonly REPORT_DIR="$ANALYSIS_ROOT/reports"
readonly WORK_DIR="$ANALYSIS_ROOT/work"
readonly V1_SOURCE_DIR="$VALIDATION_ROOT/frozen-v1.1"
readonly TOOL_ROOT="$VALIDATION_ROOT/tools"
readonly TOOL_BIN="$TOOL_ROOT/bin"
readonly NEXTFLOW="$TOOL_BIN/nextflow"
readonly SAMURAI_SOURCE_DIR="$ANALYSIS_ROOT/.oncotracer/samurai/$SAMURAI_REVISION"
readonly AUDIT_ROOT="$VALIDATION_ROOT/audit"
readonly BUNDLE_DIR="$VALIDATION_ROOT/bundles"
readonly BINARY="$RELEASE_CANDIDATE_DIR/oncotracer"
readonly LEDGER="$STATE_DIR/stage-ledger.tsv"

mkdir -p \
  "$CONTEXT_DIR" "$LOG_DIR" "$STATE_DIR/stages" "$TMP_DIR" \
  "$RELEASE_CANDIDATE_DIR" "$ENV_ROOT" "$INPUT_ROOT" \
  "$CONFIG_DIR" "$REPORT_DIR" "$WORK_DIR" "$TOOL_BIN" "$AUDIT_ROOT" "$BUNDLE_DIR"
if [[ ! -e "$VALIDATION_ROOT/.oncotracer-v2-release-validation-root" ]]; then
  printf 'schema=%s\ncreated_by=%s\n' \
    "$DRIVER_SCHEMA" "scripts/validate_v2_release.sh" \
    > "$VALIDATION_ROOT/.oncotracer-v2-release-validation-root"
fi

export TMPDIR="$TMP_DIR"
export XDG_CONFIG_HOME="$VALIDATION_ROOT/config"
export XDG_DATA_HOME="$VALIDATION_ROOT/data"
# Release parity is CPU-only on the validation server. Do not discover,
# configure, reset, or place load on GPUs used by active sequencing services.
export CUDA_VISIBLE_DEVICES=""
export NVIDIA_VISIBLE_DEVICES="void"
export CONDA_PKGS_DIRS="$VALIDATION_ROOT/conda-package-cache"
export XDG_CACHE_HOME="$VALIDATION_ROOT/cache"
export HF_HOME="$VALIDATION_ROOT/cache/huggingface"
export MPLCONFIGDIR="$VALIDATION_ROOT/cache/matplotlib"
export NXF_HOME="$VALIDATION_ROOT/nextflow-home"
export NXF_ANSI_LOG=false
export NXF_DISABLE_CHECK_LATEST=true
export NXF_OPTS="${NXF_OPTS:--Xms256m -Xmx8g}"

if [[ ! -s "$LEDGER" ]]; then
  printf 'schema\tstage\tstarted_at\tfinished_at\tstatus\texit_code\tsignature\tlog\tcommand\n' > "$LEDGER"
fi

session_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
readonly SESSION_ID="${session_stamp}.$$"
printf '%s\n' "$session_stamp" > "$CONTEXT_DIR/session-${SESSION_ID}.start"
if [[ ! -e "$CONTEXT_DIR/initial-source-commit.txt" ]]; then
  printf '%s\n' "$SOURCE_COMMIT" > "$CONTEXT_DIR/initial-source-commit.txt"
  printf '%s\n' "$SOURCE_SHA256" > "$CONTEXT_DIR/initial-source-sha256.txt"
fi
printf '%s\n' "$SOURCE_COMMIT" > "$CONTEXT_DIR/current-source-commit.txt"
printf '%s\n' "$SOURCE_SHA256" > "$CONTEXT_DIR/current-source-sha256.txt"
printf '%s\n' "$V1_COMMIT" > "$CONTEXT_DIR/v1.1-commit.txt"
printf '%s\n' "$V1_SOURCE_SHA256" > "$CONTEXT_DIR/v1.1-source-sha256.txt"
printf '%s\n' "$V1_DOCKER_IMAGE" > "$CONTEXT_DIR/v1.1-container.txt"
cat > "$CONTEXT_DIR/nextflow-expected.txt" <<EOF
version=$NEXTFLOW_VERSION
url=$NEXTFLOW_URL
sha256=$NEXTFLOW_SHA256
size=$NEXTFLOW_SIZE
role=outer-and-nested-frozen-v1.1-comparator-launcher
path=$NEXTFLOW
EOF
cat > "$CONTEXT_DIR/samurai-expected.txt" <<EOF
revision=$SAMURAI_REVISION
commit=$SAMURAI_COMMIT_EXPECTED
prepared_main_nf_sha256=$SAMURAI_PREPARED_MAIN_SHA256
repository=https://github.com/DIncalciLab/samurai.git
EOF
nested_task_cpu_limit="$THREADS"
if ((nested_task_cpu_limit > 4)); then
  nested_task_cpu_limit=4
fi
samurai_config_template="$(mktemp "$TMP_DIR/samurai-nextflow-audit.$SESSION_ID.XXXXXX.config")"
cat > "$samurai_config_template" <<EOF
params.oncotracer_nested_audit_policy_sha256 = '__ONCOTRACER_AUDIT_POLICY_SHA256__'
executor.queueSize = 4
process {
  resourceLimits = [cpus: $nested_task_cpu_limit, memory: '96.GB', time: '48.h']
  withName: ICHORCNA_RUN {
    cache = false
    containerOptions = '-v $REPOSITORY_ROOT/bin/scripts:/opt/oncotracer/scripts:ro -v $REPOSITORY_ROOT/bin/scripts/v1_ichorcna_profile.R:/.Rprofile:ro'
  }
}
conda { useMamba = false }
trace {
  fields = 'task_id,hash,native_id,name,status,exit,submit,duration,realtime,%cpu,peak_rss,peak_vmem,rchar,wchar,container'
}
EOF
samurai_policy_sha="$(
  {
    printf 'config-template\0'
    cat "$samurai_config_template"
    for source in \
      "$REPOSITORY_ROOT/bin/scripts/ichorcna_plot_compat.R" \
      "$REPOSITORY_ROOT/bin/scripts/v1_ichorcna_profile.R"; do
      digest="$(sha256sum "$source" | awk '{print $1}')"
      printf 'source\0%s\0%s\0' "$(basename "$source")" "$digest"
    done
  } | sha256sum | awk '{print $1}'
)"
[[ "$samurai_policy_sha" =~ ^[0-9a-f]{64}$ ]]
sed "s/__ONCOTRACER_AUDIT_POLICY_SHA256__/$samurai_policy_sha/" \
  "$samurai_config_template" > "$CONTEXT_DIR/samurai-nextflow-audit.config"
rm -f -- "$samurai_config_template"
grep -Fxq \
  "params.oncotracer_nested_audit_policy_sha256 = '$samurai_policy_sha'" \
  "$CONTEXT_DIR/samurai-nextflow-audit.config"
(
  cd "$REPOSITORY_ROOT"
  sha256sum \
    bin/scripts/ichorcna_plot_compat.R \
    bin/scripts/v1_ichorcna_profile.R
) > "$CONTEXT_DIR/v1-ichorcna-plot-compat-SHA256SUMS"

git -C "$REPOSITORY_ROOT" status --short > "$CONTEXT_DIR/git-status-${SESSION_ID}.txt"
git -C "$REPOSITORY_ROOT" log -1 --format=fuller "$SOURCE_COMMIT" > "$CONTEXT_DIR/source-commit-${SESSION_ID}.txt"

{
  printf 'schema=%s\n' "$DRIVER_SCHEMA"
  printf 'session_start_utc=%s\n' "$session_stamp"
  printf 'repository=%s\n' "$REPOSITORY_ROOT"
  printf 'validation_root=%s\n' "$VALIDATION_ROOT"
  printf 'shared_reference=%s\n' "$SHARED_REFERENCE"
  printf 'threads=%s\n' "$THREADS"
  printf 'compute_policy=cpu-only; CUDA_VISIBLE_DEVICES is empty; NVIDIA_VISIBLE_DEVICES=void\n'
  printf 'resume=%s\n' "$RESUME"
  printf 'tmux_session=%s\n' "${TMUX:-not-running-under-tmux}"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'kernel=%s\n' "$(uname -a)"
  printf 'cpus=%s\n' "$(nproc)"
  printf 'source_commit=%s\n' "$SOURCE_COMMIT"
  printf 'source_sha256=%s\n' "$SOURCE_SHA256"
  printf 'v1_commit=%s\n' "$V1_COMMIT"
  printf 'v1_source_sha256=%s\n' "$V1_SOURCE_SHA256"
  printf 'v1_container=%s\n' "$V1_DOCKER_IMAGE"
  printf 'nextflow_version=%s\n' "$NEXTFLOW_VERSION"
  printf 'nextflow_url=%s\n' "$NEXTFLOW_URL"
  printf 'nextflow_sha256=%s\n' "$NEXTFLOW_SHA256"
  printf 'samurai_revision=%s\n' "$SAMURAI_REVISION"
  printf 'samurai_commit=%s\n' "$SAMURAI_COMMIT_EXPECTED"
} > "$CONTEXT_DIR/run-context-${SESSION_ID}.txt"

{
  printf 'python_path=%s\n' "$(command -v python3)"
  printf 'python='; python3 --version 2>&1
  printf 'conda_path=%s\n' "$(command -v conda)"
  printf 'conda='; conda --version 2>&1
  printf 'docker_path=%s\n' "$(command -v docker)"
  printf 'docker='; docker --version 2>&1
  printf 'nextflow_path=%s\n' "$NEXTFLOW"
  printf 'nextflow_version=%s\n' "$NEXTFLOW_VERSION"
  printf 'nextflow_url=%s\n' "$NEXTFLOW_URL"
  printf 'nextflow_expected_sha256=%s\n' "$NEXTFLOW_SHA256"
  printf 'NXF_HOME=%s\n' "$NXF_HOME"
  printf 'NXF_OPTS=%s\n' "$NXF_OPTS"
  printf 'CUDA_VISIBLE_DEVICES=%s\n' "$CUDA_VISIBLE_DEVICES"
  printf 'NVIDIA_VISIBLE_DEVICES=%s\n' "$NVIDIA_VISIBLE_DEVICES"
} > "$CONTEXT_DIR/environment-fingerprint.txt"

{
  uname -a
  printf '\nCPU\n'
  command -v lscpu >/dev/null 2>&1 && lscpu || true
  printf '\nMEMORY\n'
  command -v free >/dev/null 2>&1 && free -h || true
  printf '\nDISK\n'
  df -h "$VALIDATION_ROOT" "$SHARED_REFERENCE"
  printf '\nDOCKER\n'
  docker info
  printf '\nCONDA\n'
  conda info
} > "$CONTEXT_DIR/host-${SESSION_ID}.txt" 2>&1

if [[ -z "${TMUX:-}" ]]; then
  printf 'WARNING: long release validation is not running under tmux. Start a dedicated tmux session before the complete run.\n' >&2
fi

CURRENT_STAGE="initialization"
VALIDATION_COMPLETE=false
finish_session() {
  local rc=$?
  local status="failed"
  [[ "$VALIDATION_COMPLETE" == true && "$rc" -eq 0 ]] && status="passed"
  {
    printf 'session_end_utc=%s\n' "$(date -u +%Y%m%dT%H%M%SZ)"
    printf 'status=%s\n' "$status"
    printf 'exit_code=%s\n' "$rc"
    printf 'last_stage=%s\n' "$CURRENT_STAGE"
    printf 'source_commit=%s\n' "$SOURCE_COMMIT"
    printf 'source_sha256=%s\n' "$SOURCE_SHA256"
  } > "$CONTEXT_DIR/session-${SESSION_ID}.result"
}
trap finish_session EXIT

hash_path() {
  local path="$1"
  if [[ -f "$path" ]]; then
    sha256sum "$path" | awk '{print $1}'
  elif [[ -d "$path" ]]; then
    (
      cd "$path"
      find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
    ) | sha256sum | awk '{print $1}'
  elif [[ -L "$path" ]]; then
    printf 'symlink:%s' "$(readlink "$path")" | sha256sum | awk '{print $1}'
  else
    printf 'MISSING' | sha256sum | awk '{print $1}'
  fi
}

stage_signature() {
  local stage="$1" description="$2"
  shift 2
  {
    printf 'schema=%s\n' "$DRIVER_SCHEMA"
    printf 'stage=%s\n' "$stage"
    printf 'source_commit=%s\n' "$SOURCE_COMMIT"
    printf 'source_sha256=%s\n' "$SOURCE_SHA256"
    printf 'validation_root=%s\n' "$VALIDATION_ROOT"
    printf 'shared_reference=%s\n' "$SHARED_REFERENCE"
    printf 'threads=%s\n' "$THREADS"
    printf 'cpu_policy=CUDA_VISIBLE_DEVICES-empty,NVIDIA_VISIBLE_DEVICES-void\n'
    printf 'description=%s\n' "$description"
    printf 'environment_fingerprint=%s\n' "$(hash_path "$CONTEXT_DIR/environment-fingerprint.txt")"
    local input
    for input in "$@"; do
      printf 'input=%s\t%s\n' "$input" "$(hash_path "$input")"
    done
  } | sha256sum | awk '{print $1}'
}

ledger_row() {
  local stage="$1" started="$2" finished="$3" status="$4" rc="$5" signature="$6" log="$7" description="$8"
  description="${description//$'\t'/ }"
  description="${description//$'\n'/ }"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$DRIVER_SCHEMA" "$stage" "$started" "$finished" "$status" "$rc" \
    "$signature" "$log" "$description" >> "$LEDGER"
}

run_stage() {
  local stage="$1" description="$2" action="$3" verifier="$4"
  shift 4
  local signature marker recorded started finished log rc verify_rc marker_tmp
  verify_source_checkout
  signature="$(stage_signature "$stage" "$description" "$@")"
  marker="$STATE_DIR/stages/${stage}.complete"
  recorded=""
  [[ -s "$marker" ]] && IFS= read -r recorded < "$marker"
  CURRENT_STAGE="$stage"

  if [[ "$RESUME" == true && "$recorded" == "$signature" ]]; then
    set +e
    (set -Eeuo pipefail; "$verifier")
    verify_rc=$?
    set -e
    if [[ "$verify_rc" -eq 0 ]]; then
      started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      ledger_row "$stage" "$started" "$started" "reused" 0 "$signature" "$marker" "$description"
      printf 'REUSE: %s (%s)\n' "$stage" "$signature"
      return 0
    fi
    printf 'INVALIDATE: %s output verification failed; rerunning.\n' "$stage" >&2
  elif [[ "$RESUME" == true && -n "$recorded" ]]; then
    printf 'INVALIDATE: %s signature changed (%s -> %s).\n' "$stage" "$recorded" "$signature" >&2
  fi

  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log="$LOG_DIR/${stage}.${SESSION_ID}.log"
  printf 'RUN: %s\nSIGNATURE: %s\nLOG: %s\n' "$stage" "$signature" "$log"
  set +e
  (
    set -Eeuo pipefail
    export PS4='+ ${BASH_SOURCE}:${LINENO}: '
    set -x
    "$action"
  ) 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  set -e
  if [[ "$rc" -eq 0 ]]; then
    set +e
    verify_source_checkout
    verify_rc=$?
    set -e
    [[ "$verify_rc" -eq 0 ]] || rc="$verify_rc"
  fi
  if [[ "$rc" -eq 0 ]]; then
    set +e
    (set -Eeuo pipefail; "$verifier") 2>&1 | tee -a "$log"
    verify_rc=${PIPESTATUS[0]}
    set -e
    [[ "$verify_rc" -eq 0 ]] || rc="$verify_rc"
  fi
  finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ "$rc" -ne 0 ]]; then
    ledger_row "$stage" "$started" "$finished" "failed" "$rc" "$signature" "$log" "$description"
    printf 'FAILED: %s (exit %s; log %s)\n' "$stage" "$rc" "$log" >&2
    return "$rc"
  fi
  marker_tmp="$marker.tmp.$$"
  printf '%s\ncompleted_at=%s\nlog=%s\n' "$signature" "$finished" "$log" > "$marker_tmp"
  mv -f -- "$marker_tmp" "$marker"
  ledger_row "$stage" "$started" "$finished" "passed" 0 "$signature" "$log" "$description"
}

write_tree_manifest() {
  local root="$1" destination="$2" exclude_relative="${3-}"
  local destination_tmp root_canonical destination_canonical destination_relative=""
  root_canonical="$(readlink -m -- "$root")"
  destination_canonical="$(readlink -m -- "$destination")"
  if [[ "$destination_canonical" == "$root_canonical/"* ]]; then
    destination_relative="./${destination_canonical#"$root_canonical/"}"
  fi
  mkdir -p "$(dirname "$destination")"
  destination_tmp="$(mktemp "$TMP_DIR/tree-manifest.XXXXXX")"
  if ! (
      set -Eeuo pipefail
      cd "$root_canonical"
      find . -type f -print0 | LC_ALL=C sort -z |
        while IFS= read -r -d '' relative; do
          [[ -n "$destination_relative" && "$relative" == "$destination_relative" ]] && continue
          [[ -n "$exclude_relative" && "$relative" == "./$exclude_relative" ]] && continue
          sha256sum "$relative"
        done
    ) > "$destination_tmp"; then
    rm -f -- "$destination_tmp"
    return 1
  fi
  mv -f -- "$destination_tmp" "$destination"
}

verify_tree_manifest() {
  local root="$1" manifest="$2"
  local root_canonical manifest_canonical exclude_relative="" observed
  [[ -s "$manifest" ]] || return 1
  if ! (cd "$root" && sha256sum -c "$manifest"); then
    return 1
  fi

  root_canonical="$(readlink -m -- "$root")"
  manifest_canonical="$(readlink -m -- "$manifest")"
  if [[ "$manifest_canonical" == "$root_canonical/"* ]]; then
    exclude_relative="${manifest_canonical#"$root_canonical/"}"
  fi
  observed="$(mktemp "$TMP_DIR/tree-manifest-verify.XXXXXX")"
  if ! write_tree_manifest "$root_canonical" "$observed" "$exclude_relative"; then
    rm -f -- "$observed"
    return 1
  fi
  if ! cmp -s -- "$manifest" "$observed"; then
    printf 'Tree contains files absent from its SHA-256 manifest: %s\n' "$root" >&2
    rm -f -- "$observed"
    return 1
  fi
  rm -f -- "$observed"
}

write_git_commit_tree_manifest() {
  local repository="$1" commit="$2" destination="$3" temporary
  temporary="$(mktemp "$TMP_DIR/git-tree-manifest.XXXXXX")"
  python3 - "$repository" "$commit" "$temporary" <<'PY'
import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

repository, commit, destination = sys.argv[1:4]
process = subprocess.Popen(
    [
        "git", "-C", repository, "-c", "tar.umask=0002", "archive",
        "--format=tar", commit,
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if process.stdout is None or process.stderr is None:
    raise SystemExit("could not read git archive")
records = []
with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
    for member in archive:
        if not member.isfile():
            continue
        if "\n" in member.name or "\\" in member.name:
            raise SystemExit(f"unsupported source path: {member.name!r}")
        handle = archive.extractfile(member)
        if handle is None:
            raise SystemExit(f"could not read source member: {member.name}")
        records.append((member.name, hashlib.sha256(handle.read()).hexdigest()))
stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
if process.wait() != 0:
    raise SystemExit(stderr or "git archive failed")
Path(destination).write_text(
    "".join(f"{digest}  ./{name}\n" for name, digest in sorted(records)),
    encoding="utf-8",
)
PY
  mv -- "$temporary" "$destination"
}

write_git_archive_structure_manifest() {
  local repository="$1" commit="$2" destination="$3" temporary
  temporary="$(mktemp "$TMP_DIR/git-archive-structure.XXXXXX")"
  python3 - "$repository" "$commit" "$temporary" <<'PY'
import json
import subprocess
import sys
import tarfile
from pathlib import Path

repository, commit, destination = sys.argv[1:4]
process = subprocess.Popen(
    [
        "git", "-C", repository, "-c", "tar.umask=0002", "archive",
        "--format=tar", commit,
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if process.stdout is None or process.stderr is None:
    raise SystemExit("could not read git archive")
records = []
with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
    for member in archive:
        if member.isdir():
            continue
        if "\n" in member.name or "\\" in member.name:
            raise SystemExit(f"unsupported source path: {member.name!r}")
        if member.isfile():
            records.append(
                {
                    "path": member.name,
                    "type": "file",
                    "mode": f"{member.mode & 0o777:04o}",
                }
            )
        elif member.issym():
            records.append(
                {
                    "path": member.name,
                    "type": "symlink",
                    "mode": "0777",
                    "target": member.linkname,
                }
            )
        else:
            raise SystemExit(f"unsupported git archive entry: {member.name!r}")
stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
if process.wait() != 0:
    raise SystemExit(stderr or "git archive failed")
Path(destination).write_text(
    json.dumps(sorted(records, key=lambda item: item["path"]), indent=2, sort_keys=True)
    + "\n",
    encoding="utf-8",
)
PY
  mv -- "$temporary" "$destination"
}

write_filesystem_structure_manifest() {
  local root="$1" destination="$2" temporary
  temporary="$(mktemp "$TMP_DIR/filesystem-structure.XXXXXX")"
  python3 - "$root" "$temporary" <<'PY'
import json
import os
import stat
import sys
from pathlib import Path

root, destination = Path(sys.argv[1]), Path(sys.argv[2])
records = []
for path in sorted(
    root.rglob("*"), key=lambda candidate: candidate.relative_to(root).as_posix()
):
    metadata = path.lstat()
    if stat.S_ISDIR(metadata.st_mode):
        continue
    record = {
        "path": path.relative_to(root).as_posix(),
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }
    if stat.S_ISREG(metadata.st_mode):
        record["type"] = "file"
    elif stat.S_ISLNK(metadata.st_mode):
        record["type"] = "symlink"
        record["target"] = os.readlink(path)
    else:
        raise SystemExit(f"unsupported filesystem entry: {path}")
    records.append(record)
destination.write_text(
    json.dumps(records, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
  mv -- "$temporary" "$destination"
}

verify_reference_directory() {
  local root="$1"
  local required=(
    genome.fa genome.fa.fai genome.dict genome.fa.map-ont.mmi
    bwa/genome.amb bwa/genome.ann bwa/genome.bwt bwa/genome.pac bwa/genome.sa
  )
  local relative
  for relative in "${required[@]}"; do
    [[ -s "$root/$relative" ]] || {
      printf 'Missing shared reference component: %s\n' "$root/$relative" >&2
      return 1
    }
  done
  [[ "$(sed -n '1s/^>\([^[:space:]]*\).*/\1/p' "$root/genome.fa")" == chr* ]]
}

verify_reference_directory "$SHARED_REFERENCE"
shared_reference_session_manifest="$CONTEXT_DIR/shared-reference-SHA256SUMS.${SESSION_ID}"
write_tree_manifest "$SHARED_REFERENCE" "$shared_reference_session_manifest"
if [[ ! -e "$CONTEXT_DIR/initial-shared-reference-SHA256SUMS" ]]; then
  cp "$shared_reference_session_manifest" "$CONTEXT_DIR/initial-shared-reference-SHA256SUMS"
fi
cp "$shared_reference_session_manifest" "$CONTEXT_DIR/shared-reference-SHA256SUMS"

run_copied_binary() (
  set -Eeuo pipefail
  cd "$TMP_DIR"
  env -u PYTHONHOME -u PYTHONPATH "$BINARY" "$@"
)

action_prepare_pinned_nextflow() {
  local preserved temporary
  if [[ -e "$NEXTFLOW" ]] &&
     ! printf '%s  %s\n' "$NEXTFLOW_SHA256" "$NEXTFLOW" | sha256sum -c -; then
    preserved="${NEXTFLOW}.invalid.${SESSION_ID}"
    [[ ! -e "$preserved" ]] || die "preservation path already exists: $preserved"
    mv -- "$NEXTFLOW" "$preserved"
    printf 'Preserved invalid Nextflow executable at %s\n' "$preserved" >&2
  fi
  if [[ ! -e "$NEXTFLOW" ]]; then
    temporary="$(mktemp "$TOOL_BIN/nextflow-download.XXXXXX")"
    curl --fail --location --retry 5 --retry-all-errors \
      --output "$temporary" "$NEXTFLOW_URL"
    printf '%s  %s\n' "$NEXTFLOW_SHA256" "$temporary" | sha256sum -c -
    chmod 0755 "$temporary"
    mv -- "$temporary" "$NEXTFLOW"
  fi
  chmod 0755 "$NEXTFLOW"
  printf '%s  %s\n' "$NEXTFLOW_SHA256" "$NEXTFLOW" | sha256sum -c -
  [[ "$(stat -c '%s' "$NEXTFLOW")" == "$NEXTFLOW_SIZE" ]]
  PATH="$TOOL_BIN:$PATH" "$NEXTFLOW" -version \
    > "$CONTEXT_DIR/nextflow-version.txt" 2>&1
  grep -E "version[[:space:]]+$NEXTFLOW_VERSION([[:space:]]|$)" \
    "$CONTEXT_DIR/nextflow-version.txt"
  java -version > "$CONTEXT_DIR/nextflow-java-version.txt" 2>&1
  stat --printf='path=%n\nsize=%s\nmode=%a\n' "$NEXTFLOW" \
    > "$CONTEXT_DIR/nextflow-actual.txt"
  printf 'sha256=%s\n' "$(sha256sum "$NEXTFLOW" | awk '{print $1}')" \
    >> "$CONTEXT_DIR/nextflow-actual.txt"
  printf 'version=%s\nurl=%s\nrole=%s\nresolved_from_v1_path=%s\n' \
    "$NEXTFLOW_VERSION" "$NEXTFLOW_URL" \
    'outer-and-nested-frozen-v1.1-comparator-launcher' \
    "$(PATH="$TOOL_BIN:$PATH" command -v nextflow)" \
    >> "$CONTEXT_DIR/nextflow-actual.txt"
}

verify_prepare_pinned_nextflow() {
  [[ -x "$NEXTFLOW" ]] || return 1
  printf '%s  %s\n' "$NEXTFLOW_SHA256" "$NEXTFLOW" | sha256sum -c - || return 1
  [[ "$(stat -c '%s' "$NEXTFLOW")" == "$NEXTFLOW_SIZE" ]] || return 1
  [[ "$(stat -c '%a' "$NEXTFLOW")" == 755 ]] || return 1
  [[ "$(PATH="$TOOL_BIN:$PATH" command -v nextflow)" == "$NEXTFLOW" ]] || return 1
  grep -Fx "path=$NEXTFLOW" "$CONTEXT_DIR/nextflow-actual.txt" >/dev/null || return 1
  grep -Fx "size=$NEXTFLOW_SIZE" "$CONTEXT_DIR/nextflow-actual.txt" >/dev/null || return 1
  grep -Fx 'mode=755' "$CONTEXT_DIR/nextflow-actual.txt" >/dev/null || return 1
  grep -Fx "sha256=$NEXTFLOW_SHA256" "$CONTEXT_DIR/nextflow-actual.txt" >/dev/null || return 1
  grep -E "version[[:space:]]+$NEXTFLOW_VERSION([[:space:]]|$)" \
    "$CONTEXT_DIR/nextflow-version.txt" >/dev/null || return 1
  [[ -s "$CONTEXT_DIR/nextflow-java-version.txt" ]] || return 1
  return 0
}

run_stage \
  "prepare-pinned-nextflow" \
  "download the official self-contained Nextflow $NEXTFLOW_VERSION distribution and require its exact published SHA-256; use it only for frozen-v1.1 comparator runs" \
  action_prepare_pinned_nextflow verify_prepare_pinned_nextflow \
  "$CONTEXT_DIR/nextflow-expected.txt"

action_build_binary() {
  python3 "$REPOSITORY_ROOT/scripts/build_native_binary.py" \
    --output "$BINARY" \
    --source-commit "$SOURCE_COMMIT" \
    --source-sha256 "$SOURCE_SHA256"
  chmod 0755 "$BINARY"
  run_copied_binary --version | tee "$RELEASE_CANDIDATE_DIR/oncotracer.version.txt"
  grep -Fx 'OncoTracer 2.0.0' "$RELEASE_CANDIDATE_DIR/oncotracer.version.txt"
  run_copied_binary --help > "$RELEASE_CANDIDATE_DIR/oncotracer.help.txt"
  run_copied_binary provenance --json > "$RELEASE_CANDIDATE_DIR/oncotracer.provenance.json"
  (
    cd "$RELEASE_CANDIDATE_DIR"
    sha256sum \
      oncotracer oncotracer.provenance.json oncotracer.version.txt oncotracer.help.txt \
      > SHA256SUMS
  )
}

verify_build_binary() {
  [[ -x "$BINARY" ]]
  (cd "$RELEASE_CANDIDATE_DIR" && sha256sum -c SHA256SUMS)
  python3 - \
    "$RELEASE_CANDIDATE_DIR/oncotracer.provenance.json" \
    "$SOURCE_COMMIT" "$SOURCE_SHA256" "$BINARY" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

record = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
binary = Path(sys.argv[4]).resolve()
expected = {
    "schema": "oncotracer-provenance-v1",
    "oncotracer_version": "2.0.0",
    "source_commit": sys.argv[2],
    "source_sha256": sys.argv[3],
    "source_sha256_definition": "sha256(git -c tar.umask=0002 archive --format=tar COMMIT)",
    "source_metadata_origin": "embedded",
    "source_tree_dirty": False,
    "binary_path": str(binary),
    "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
}
for key, value in expected.items():
    if record.get(key) != value:
        raise SystemExit(
            f"copied executable provenance mismatch for {key}: "
            f"expected {value!r}, observed {record.get(key)!r}"
        )
historical = record.get("historical_sources", {})
expected_historical = {
    "native_source_input_archive": "f374c8f80dd320ab7de0be13bb6ad6ad9a82dfdb725f318893322efee6deb4a1",
    "classifier_overlay": "75fc9bf97e0312e3aa550fa8290e0e1aa8c8a4127f842ba1718a0239aff956e6",
}
for name, digest in expected_historical.items():
    if historical.get(name, {}).get("sha256") != digest:
        raise SystemExit(f"copied executable lacks the expected historical {name} hash")
PY
}

run_stage \
  "build-copied-binary" \
  "python3 scripts/build_native_binary.py --output VALIDATION_ROOT/release-candidate/oncotracer --source-commit SOURCE_COMMIT --source-sha256 SOURCE_SHA256; execute version, help, and provenance outside checkout" \
  action_build_binary verify_build_binary \
  "$REPOSITORY_ROOT/scripts/build_native_binary.py" "$REPOSITORY_ROOT/oncotracer_cli" "$REPOSITORY_ROOT/bin" "$REPOSITORY_ROOT/environments"

action_prepare_v1() {
  local preserved
  write_git_commit_tree_manifest \
    "$REPOSITORY_ROOT" "$V1_COMMIT" \
    "$CONTEXT_DIR/v1.1-expected-source-SHA256SUMS"
  write_git_archive_structure_manifest \
    "$REPOSITORY_ROOT" "$V1_COMMIT" \
    "$CONTEXT_DIR/v1.1-expected-source-structure.json"
  if [[ -d "$V1_SOURCE_DIR" ]] &&
     [[ -n "$(find "$V1_SOURCE_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    write_filesystem_structure_manifest \
      "$V1_SOURCE_DIR" "$CONTEXT_DIR/v1.1-source-structure.json"
    if [[ -s "$CONTEXT_DIR/v1.1-source-SHA256SUMS" ]] &&
       verify_tree_manifest "$V1_SOURCE_DIR" "$CONTEXT_DIR/v1.1-source-SHA256SUMS" &&
       cmp -s "$CONTEXT_DIR/v1.1-source-SHA256SUMS" \
         "$CONTEXT_DIR/v1.1-expected-source-SHA256SUMS" &&
       cmp -s "$CONTEXT_DIR/v1.1-source-structure.json" \
         "$CONTEXT_DIR/v1.1-expected-source-structure.json"; then
      printf 'Frozen v1.1 source tree is already exact; retaining it.\n'
    else
      preserved="$VALIDATION_ROOT/frozen-v1.1.invalid.${SESSION_ID}"
      [[ ! -e "$preserved" ]] || die "preservation path already exists: $preserved"
      mv -- "$V1_SOURCE_DIR" "$preserved"
      printf 'Preserved invalid frozen v1.1 tree at %s\n' "$preserved" >&2
    fi
  fi
  mkdir -p "$V1_SOURCE_DIR"
  if [[ -z "$(find "$V1_SOURCE_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    (
      umask 0002
      git -C "$REPOSITORY_ROOT" -c tar.umask=0002 archive --format=tar "$V1_COMMIT" |
        tar -xf - -C "$V1_SOURCE_DIR"
    )
    write_tree_manifest "$V1_SOURCE_DIR" "$CONTEXT_DIR/v1.1-source-SHA256SUMS"
  fi
  write_filesystem_structure_manifest \
    "$V1_SOURCE_DIR" "$CONTEXT_DIR/v1.1-source-structure.json"
  cmp "$CONTEXT_DIR/v1.1-expected-source-SHA256SUMS" \
    "$CONTEXT_DIR/v1.1-source-SHA256SUMS"
  cmp "$CONTEXT_DIR/v1.1-expected-source-structure.json" \
    "$CONTEXT_DIR/v1.1-source-structure.json"
  docker pull "$V1_DOCKER_IMAGE"
  docker image inspect "$V1_DOCKER_IMAGE" --format '{{range .RepoDigests}}{{println .}}{{end}}' \
    > "$CONTEXT_DIR/v1.1-container-repodigests.txt"
  grep -Fx "$V1_DOCKER_IMAGE" "$CONTEXT_DIR/v1.1-container-repodigests.txt"
  verify_tree_manifest "$V1_SOURCE_DIR" "$CONTEXT_DIR/v1.1-source-SHA256SUMS"
}

verify_prepare_v1() {
  local expected expected_structure observed_structure
  [[ -s "$V1_SOURCE_DIR/main.nf" ]]
  verify_tree_manifest "$V1_SOURCE_DIR" "$CONTEXT_DIR/v1.1-source-SHA256SUMS"
  expected="$(mktemp "$TMP_DIR/v1.1-expected-manifest.XXXXXX")"
  expected_structure="$(mktemp "$TMP_DIR/v1.1-expected-structure.XXXXXX")"
  observed_structure="$(mktemp "$TMP_DIR/v1.1-observed-structure.XXXXXX")"
  write_git_commit_tree_manifest "$REPOSITORY_ROOT" "$V1_COMMIT" "$expected"
  write_git_archive_structure_manifest "$REPOSITORY_ROOT" "$V1_COMMIT" "$expected_structure"
  write_filesystem_structure_manifest "$V1_SOURCE_DIR" "$observed_structure"
  cmp "$expected" "$CONTEXT_DIR/v1.1-source-SHA256SUMS"
  cmp "$expected_structure" "$CONTEXT_DIR/v1.1-source-structure.json"
  cmp "$expected_structure" "$observed_structure"
  rm -f -- "$expected" "$expected_structure" "$observed_structure"
  docker image inspect "$V1_DOCKER_IMAGE" >/dev/null
  grep -Fx "$V1_DOCKER_IMAGE" "$CONTEXT_DIR/v1.1-container-repodigests.txt"
}

run_stage \
  "prepare-frozen-v1.1" \
  "extract immutable v1.1 git archive at V1_COMMIT and pull exact comparator image $V1_DOCKER_IMAGE" \
  action_prepare_v1 verify_prepare_v1 \
  "$CONTEXT_DIR/v1.1-commit.txt" "$CONTEXT_DIR/v1.1-source-sha256.txt" "$CONTEXT_DIR/v1.1-container.txt"

samurai_source_exact() {
  [[ -s "$SAMURAI_SOURCE_DIR/main.nf" ]] || return 1
  [[ -d "$SAMURAI_SOURCE_DIR/.git" ]] || return 1
  [[ -s "$SAMURAI_SOURCE_DIR/.oncotracer-source" ]] || return 1
  [[ "$(git -C "$SAMURAI_SOURCE_DIR" rev-parse HEAD)" == "$SAMURAI_COMMIT_EXPECTED" ]] || return 1
  grep -Fx 'cache_format=2' "$SAMURAI_SOURCE_DIR/.oncotracer-source" >/dev/null || return 1
  grep -Fx "revision=$SAMURAI_REVISION" "$SAMURAI_SOURCE_DIR/.oncotracer-source" >/dev/null || return 1
  grep -Fx "commit=$SAMURAI_COMMIT_EXPECTED" "$SAMURAI_SOURCE_DIR/.oncotracer-source" >/dev/null || return 1
  grep -Fx 'repository=https://github.com/DIncalciLab/samurai.git' \
    "$SAMURAI_SOURCE_DIR/.oncotracer-source" >/dev/null || return 1
}

write_expected_samurai_tree_manifest() {
  local destination="$1" temporary
  temporary="$(mktemp "$TMP_DIR/samurai-expected-tree.XXXXXX")" || return 1
  if ! python3 - \
    "$SAMURAI_SOURCE_DIR" "$SAMURAI_COMMIT_EXPECTED" "$SAMURAI_REVISION" \
    "$SAMURAI_PREPARED_MAIN_SHA256" "$temporary" <<'PY'
import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

repository, commit, revision, expected_main_sha256, destination = sys.argv[1:6]
old = (
    "dict = params.dict ? channel.fromPath(params.fai).map { it -> "
    "[[id: it.baseName], it] }.collect() : channel.empty()"
)
new = (
    "dict = params.dict ? channel.fromPath(params.dict).map { it -> "
    "[[id: it.baseName], it] }.collect() : channel.empty()"
)
process = subprocess.Popen(
    [
        "git", "-C", repository, "-c", "tar.umask=0002", "archive",
        "--format=tar", commit,
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if process.stdout is None or process.stderr is None:
    raise SystemExit("could not read SAMURAI git archive")
files = {}
with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
    for member in archive:
        if not member.isfile():
            continue
        if "\n" in member.name or "\\" in member.name:
            raise SystemExit(f"unsupported SAMURAI source path: {member.name!r}")
        handle = archive.extractfile(member)
        if handle is None:
            raise SystemExit(f"could not read SAMURAI member: {member.name}")
        payload = handle.read()
        if member.name == "main.nf":
            text = payload.decode("utf-8")
            if text.count(old) != 1 or new in text:
                raise SystemExit("unexpected SAMURAI dictionary source pattern")
            payload = text.replace(old, new, 1).encode("utf-8")
        files[member.name] = payload
stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
if process.wait() != 0:
    raise SystemExit(stderr or "SAMURAI git archive failed")
metadata = (
    f"cache_format=2\nrevision={revision}\ncommit={commit}\n"
    "repository=https://github.com/DIncalciLab/samurai.git\n"
).encode("utf-8")
files[".oncotracer-source"] = metadata
main_sha256 = hashlib.sha256(files["main.nf"]).hexdigest()
if main_sha256 != expected_main_sha256:
    raise SystemExit(
        f"prepared SAMURAI main.nf SHA-256 mismatch: {main_sha256}"
    )
Path(destination).write_text(
    "".join(
        f"{hashlib.sha256(payload).hexdigest()}  ./{name}\n"
        for name, payload in sorted(files.items())
    ),
    encoding="utf-8",
)
PY
  then
    rm -f -- "$temporary"
    return 1
  fi
  mv -- "$temporary" "$destination" || return 1
}

write_expected_samurai_structure_manifest() {
  local destination="$1" temporary
  temporary="$(mktemp "$TMP_DIR/samurai-expected-structure.XXXXXX")" || return 1
  if ! python3 - \
    "$SAMURAI_SOURCE_DIR" "$SAMURAI_COMMIT_EXPECTED" "$temporary" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

repository, commit, destination = sys.argv[1:4]
result = subprocess.run(
    ["git", "-C", repository, "ls-tree", "-rz", "-r", "--full-tree", commit],
    check=True,
    capture_output=True,
)
records = []
for raw in result.stdout.split(b"\0"):
    if not raw:
        continue
    header, encoded_path = raw.split(b"\t", 1)
    mode, object_type, object_id = header.decode("ascii").split()
    path = encoded_path.decode("utf-8")
    if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
        raise SystemExit(f"unsupported SAMURAI Git entry: {raw!r}")
    if mode == "120000":
        target = subprocess.run(
            ["git", "-C", repository, "cat-file", "blob", object_id],
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8")
        records.append(
            {"path": path, "type": "symlink", "mode": "0777", "target": target}
        )
    else:
        records.append(
            {
                "path": path,
                "type": "file",
                "mode": "0755" if mode == "100755" else "0644",
            }
        )
records.append(
    {"path": ".oncotracer-source", "type": "file", "mode": "0644"}
)
Path(destination).write_text(
    json.dumps(sorted(records, key=lambda item: item["path"]), indent=2, sort_keys=True)
    + "\n",
    encoding="utf-8",
)
PY
  then
    rm -f -- "$temporary"
    return 1
  fi
  mv -- "$temporary" "$destination" || return 1
}

write_samurai_structure_manifest() {
  local destination="$1" temporary
  temporary="$(mktemp "$TMP_DIR/samurai-observed-structure.XXXXXX")" || return 1
  if ! python3 - "$SAMURAI_SOURCE_DIR" "$temporary" <<'PY'
import json
import os
import stat
import sys
from pathlib import Path

root, destination = Path(sys.argv[1]), Path(sys.argv[2])
records = []
for path in sorted(
    root.rglob("*"), key=lambda candidate: candidate.relative_to(root).as_posix()
):
    relative = path.relative_to(root)
    if ".git" in relative.parts:
        continue
    metadata = path.lstat()
    if stat.S_ISDIR(metadata.st_mode):
        continue
    record = {
        "path": relative.as_posix(),
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }
    if stat.S_ISREG(metadata.st_mode):
        record["type"] = "file"
    elif stat.S_ISLNK(metadata.st_mode):
        record["type"] = "symlink"
        record["target"] = os.readlink(path)
    else:
        raise SystemExit(f"unsupported prepared SAMURAI entry: {path}")
    records.append(record)
destination.write_text(
    json.dumps(records, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
  then
    rm -f -- "$temporary"
    return 1
  fi
  mv -- "$temporary" "$destination" || return 1
}

verify_samurai_structure_manifest() {
  local expected="$1" observed rc=0
  [[ -s "$expected" ]] || return 1
  observed="$(mktemp "$TMP_DIR/samurai-observed-structure-verify.XXXXXX")" || return 1
  write_samurai_structure_manifest "$observed" || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    rm -f -- "$observed"
    return "$rc"
  fi
  if ! cmp -s -- "$expected" "$observed"; then
    printf 'Prepared SAMURAI entry types, modes, or symlinks differ from the pinned Git tree.\n' >&2
    rm -f -- "$observed"
    return 1
  fi
  rm -f -- "$observed"
  return 0
}

write_samurai_tree_manifest() {
  local destination="$1" temporary
  temporary="$(mktemp "$TMP_DIR/samurai-tree-manifest.XXXXXX")" || return 1
  (
    cd "$SAMURAI_SOURCE_DIR"
    find . -path './.git' -prune -o -type f -print0 |
      LC_ALL=C sort -z | xargs -0 -r sha256sum
  ) > "$temporary" || return 1
  mv -- "$temporary" "$destination" || return 1
}

verify_samurai_tree_manifest() {
  local manifest="$1" observed rc=0
  [[ -s "$manifest" ]] || return 1
  (cd "$SAMURAI_SOURCE_DIR" && sha256sum -c "$manifest") || return 1
  observed="$(mktemp "$TMP_DIR/samurai-tree-verify.XXXXXX")" || return 1
  write_samurai_tree_manifest "$observed" || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    rm -f -- "$observed"
    return "$rc"
  fi
  if ! cmp -s -- "$manifest" "$observed"; then
    printf 'Prepared SAMURAI tree differs from its complete manifest.\n' >&2
    rm -f -- "$observed"
    return 1
  fi
  rm -f -- "$observed"
  return 0
}

samurai_prepared_exact() {
  local expected expected_structure rc=0
  samurai_source_exact || return 1
  expected="$(mktemp "$TMP_DIR/samurai-prepared-exact.XXXXXX")" || return 1
  expected_structure="$(mktemp "$TMP_DIR/samurai-structure-exact.XXXXXX")" || return 1
  write_expected_samurai_tree_manifest "$expected" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    verify_samurai_tree_manifest "$expected" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    write_expected_samurai_structure_manifest "$expected_structure" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    verify_samurai_structure_manifest "$expected_structure" || rc=$?
  fi
  rm -f -- "$expected" "$expected_structure"
  return "$rc"
}

action_prepare_pinned_samurai() {
  local preserved resolved source_sha256
  if [[ -e "$SAMURAI_SOURCE_DIR" ]] && ! samurai_prepared_exact; then
    preserved="${SAMURAI_SOURCE_DIR}.invalid.${SESSION_ID}"
    [[ ! -e "$preserved" ]] || die "preservation path already exists: $preserved"
    mv -- "$SAMURAI_SOURCE_DIR" "$preserved"
    printf 'Preserved non-matching SAMURAI cache at %s\n' "$preserved" >&2
  fi
  if [[ ! -e "$SAMURAI_SOURCE_DIR" ]]; then
    resolved="$(
      bash "$V1_SOURCE_DIR/bin/scripts/prepare_samurai_source.sh" \
        --lpwgs-root "$ANALYSIS_ROOT" --revision "$SAMURAI_REVISION"
    )"
    [[ "$(readlink -m -- "$resolved")" == "$SAMURAI_SOURCE_DIR" ]] ||
      die "SAMURAI helper returned unexpected source path: $resolved"
  fi
  samurai_source_exact || die "prepared SAMURAI source identity is not exact"
  write_expected_samurai_tree_manifest \
    "$CONTEXT_DIR/samurai-prepared-tree-SHA256SUMS"
  verify_samurai_tree_manifest "$CONTEXT_DIR/samurai-prepared-tree-SHA256SUMS"
  write_expected_samurai_structure_manifest \
    "$CONTEXT_DIR/samurai-prepared-structure.json"
  verify_samurai_structure_manifest "$CONTEXT_DIR/samurai-prepared-structure.json"
  source_sha256="$(
    git -C "$SAMURAI_SOURCE_DIR" -c tar.umask=0002 archive \
      --format=tar "$SAMURAI_COMMIT_EXPECTED" | sha256sum | awk '{print $1}'
  )"
  {
    printf 'revision=%s\n' "$SAMURAI_REVISION"
    printf 'commit=%s\n' "$SAMURAI_COMMIT_EXPECTED"
    printf 'source_sha256=%s\n' "$source_sha256"
    printf 'source_sha256_definition=sha256(git -c tar.umask=0002 archive --format=tar COMMIT)\n'
    printf 'prepared_main_nf_sha256=%s\n' "$SAMURAI_PREPARED_MAIN_SHA256"
    printf 'source_metadata_sha256=%s\n' "$(sha256sum "$SAMURAI_SOURCE_DIR/.oncotracer-source" | awk '{print $1}')"
    printf 'source_path=%s\n' "$SAMURAI_SOURCE_DIR"
  } > "$CONTEXT_DIR/samurai-provenance.txt"
  git -C "$SAMURAI_SOURCE_DIR" status --short \
    > "$CONTEXT_DIR/samurai-prepared-git-status.txt"
  cp "$SAMURAI_SOURCE_DIR/.oncotracer-source" \
    "$CONTEXT_DIR/samurai-source-metadata.txt"
}

verify_prepare_pinned_samurai() {
  local expected expected_structure
  samurai_source_exact || return 1
  expected="$(mktemp "$TMP_DIR/samurai-verifier-expected.XXXXXX")" || return 1
  expected_structure="$(mktemp "$TMP_DIR/samurai-verifier-structure.XXXXXX")" || return 1
  write_expected_samurai_tree_manifest "$expected" || return 1
  write_expected_samurai_structure_manifest "$expected_structure" || return 1
  cmp -s "$expected" "$CONTEXT_DIR/samurai-prepared-tree-SHA256SUMS" || return 1
  cmp -s "$expected_structure" "$CONTEXT_DIR/samurai-prepared-structure.json" || return 1
  rm -f -- "$expected" "$expected_structure"
  grep -Fx "revision=$SAMURAI_REVISION" "$CONTEXT_DIR/samurai-provenance.txt" >/dev/null || return 1
  grep -Fx "commit=$SAMURAI_COMMIT_EXPECTED" "$CONTEXT_DIR/samurai-provenance.txt" >/dev/null || return 1
  grep -Eq '^source_sha256=[0-9a-f]{64}$' "$CONTEXT_DIR/samurai-provenance.txt" || return 1
  grep -Fx \
    'source_sha256_definition=sha256(git -c tar.umask=0002 archive --format=tar COMMIT)' \
    "$CONTEXT_DIR/samurai-provenance.txt" >/dev/null || return 1
  cmp -s "$SAMURAI_SOURCE_DIR/.oncotracer-source" \
    "$CONTEXT_DIR/samurai-source-metadata.txt" || return 1
  grep -Fx \
    "prepared_main_nf_sha256=$SAMURAI_PREPARED_MAIN_SHA256" \
    "$CONTEXT_DIR/samurai-provenance.txt" >/dev/null || return 1
  [[ "$(sha256sum "$SAMURAI_SOURCE_DIR/main.nf" | awk '{print $1}')" == \
    "$SAMURAI_PREPARED_MAIN_SHA256" ]] || return 1
  grep -Fx \
    "source_sha256=$(git -C "$SAMURAI_SOURCE_DIR" -c tar.umask=0002 archive --format=tar "$SAMURAI_COMMIT_EXPECTED" | sha256sum | awk '{print $1}')" \
    "$CONTEXT_DIR/samurai-provenance.txt" >/dev/null || return 1
  verify_samurai_tree_manifest "$CONTEXT_DIR/samurai-prepared-tree-SHA256SUMS" || return 1
  verify_samurai_structure_manifest "$CONTEXT_DIR/samurai-prepared-structure.json" || return 1
  return 0
}

run_stage \
  "prepare-pinned-samurai" \
  "prepare frozen SAMURAI $SAMURAI_REVISION, require dereferenced commit $SAMURAI_COMMIT_EXPECTED before any baseline execution, and record its exact source identity" \
  action_prepare_pinned_samurai verify_prepare_pinned_samurai \
  "$CONTEXT_DIR/samurai-expected.txt" "$CONTEXT_DIR/v1.1-source-SHA256SUMS"
samurai_container_pins() {
  cat <<'EOF'
quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0	sha256:e194048df39c3145d9b4e0a14f4da20b59d59250465b6f2a9cb698445fd45900
community.wave.seqera.io/library/bwa_htslib_samtools:83b50ff84ead50d0	sha256:48812e48a9462145c065d1b8e15d996c4a2c4c69469f1249fb601f25939cd48e
community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6	sha256:e269216786463d44f9d83a0d6e877b34bca2c7b4d35211b4b369fe98e39ef1a5
quay.io/biocontainers/samtools:1.22.1--h96c455f_0	sha256:23dc2c29f457a448a0d341fb97b2632a2c8004925214cb6420562a5b12adf8a2
quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1	sha256:fb6135876beca3059ed1414d5082833d5bbf1fb3f0f64e51ca8b29fb47adaa75
docker.io/t0shy/qpdf-docker:11.3.0	sha256:744f00189f4b0f3f1273073212102b32e0505fea528c9516e4252b9345e482d3
community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf	sha256:677f4c8e38cfd741926e5bd1e80d96b756540bc6a9e9c5ed520aa7a98358d11d
community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2	sha256:209b7aeca568155a099873da6e830427bf0a9d5418426b39f913db736d53e20b
community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4	sha256:c6240b1bcc57de07d9a92373f6fad080870bba0075be6cd25c6d37179d928c72
community.wave.seqera.io/library/polars_procps-ng_typer:d1a53d7945a021e3	sha256:3b7464a65a9b23f0969b19767303b8727ab9f7dce83b1885cd8f6334d75ed59e
community.wave.seqera.io/library/procps-ng_r-argparser_r-dplyr_r-ggplot2_pruned:10da72fa04bcba1a	sha256:28626b999449abe6ddc2167228023ec93e90d109a540be97d0a789d2093b4e8b
quay.io/einar_rainhart/pandas-pandera:1.5.3	sha256:39fae3f3a2edb8cb174b3ffade1741b6b1ec850a323b4f7a0dca6908f2e49cf8
EOF
}

record_samurai_container_identities() {
  local destination="$1" temporary tag digest pinned pinned_id tag_id repo_digests
  temporary="$(mktemp "$TMP_DIR/samurai-container-identities.XXXXXX")"
  printf 'tag\tpinned_reference\timage_id\trepo_digests\n' > "$temporary"
  while IFS=$'\t' read -r tag digest; do
    [[ -n "$tag" && "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
    pinned="$tag@$digest"
    pinned_id="$(docker image inspect "$pinned" --format '{{.Id}}')" || return 1
    tag_id="$(docker image inspect "$tag" --format '{{.Id}}')" || return 1
    [[ "$tag_id" == "$pinned_id" ]] || {
      printf 'SAMURAI tag does not resolve to its pinned image: %s\n' "$tag" >&2
      return 1
    }
    repo_digests="$(
      docker image inspect "$pinned" \
        --format '{{range .RepoDigests}}{{println .}}{{end}}' |
        LC_ALL=C sort -u |
        awk 'BEGIN{first=1} {if(!first)printf ","; printf "%s",$0; first=0} END{print ""}'
    )"
    grep -E "@${digest}(,|$)" <<< "$repo_digests" >/dev/null || {
      printf 'Pinned SAMURAI image lacks its expected RepoDigest: %s\n' "$pinned" >&2
      return 1
    }
    printf '%s\t%s\t%s\t%s\n' "$tag" "$pinned" "$pinned_id" "$repo_digests" \
      >> "$temporary"
  done < <(samurai_container_pins)
  mv -- "$temporary" "$destination"
}

action_prepare_samurai_containers() {
  local tag digest pinned pinned_id tag_id
  samurai_container_pins > "$CONTEXT_DIR/samurai-container-pins.tsv"
  while IFS=$'\t' read -r tag digest; do
    pinned="$tag@$digest"
    docker pull "$pinned"
    pinned_id="$(docker image inspect "$pinned" --format '{{.Id}}')"
    if tag_id="$(docker image inspect "$tag" --format '{{.Id}}' 2>/dev/null)"; then
      [[ "$tag_id" == "$pinned_id" ]] ||
        die "existing Docker tag differs from the frozen SAMURAI pin: $tag"
    else
      docker image tag "$pinned" "$tag"
    fi
  done < "$CONTEXT_DIR/samurai-container-pins.tsv"
  record_samurai_container_identities \
    "$CONTEXT_DIR/samurai-container-identities.tsv"
}

verify_prepare_samurai_containers() {
  local expected observed
  expected="$(mktemp "$TMP_DIR/samurai-container-pins-expected.XXXXXX")"
  observed="$(mktemp "$TMP_DIR/samurai-container-identities-observed.XXXXXX")"
  samurai_container_pins > "$expected"
  cmp -s "$expected" "$CONTEXT_DIR/samurai-container-pins.tsv" || return 1
  record_samurai_container_identities "$observed" || return 1
  cmp -s "$observed" "$CONTEXT_DIR/samurai-container-identities.tsv" || return 1
  rm -f -- "$expected" "$observed"
}

run_stage \
  "prepare-pinned-samurai-containers" \
  "pull the 12 reachable frozen-SAMURAI Docker images only by immutable digest, bind missing local tags to those exact images, and record every RepoDigest" \
  action_prepare_samurai_containers verify_prepare_samurai_containers \
  "$CONTEXT_DIR/samurai-prepared-tree-SHA256SUMS"


probe_picard() {
  local probe rc
  probe="$(mktemp "$LOG_DIR/picard-semantic-probe.${SESSION_ID}.XXXXXX.txt")"
  set +e
  "$ENV_ROOT/core/bin/picard" -h > "$probe" 2>&1
  rc=$?
  set -e
  printf 'picard_help_exit_code=%s\n' "$rc" >> "$probe"
  if [[ "$rc" -ne 1 ]]; then
    cat "$probe" >&2
    return 1
  fi
  grep -Eiq 'Picard|USAGE|CommandLineProgram' "$probe"
}

probe_bwa() {
  local probe rc
  probe="$(mktemp "$LOG_DIR/bwa-semantic-probe.${SESSION_ID}.XXXXXX.txt")"
  set +e
  "$ENV_ROOT/core/bin/bwa" > "$probe" 2>&1
  rc=$?
  set -e
  printf 'bwa_help_exit_code=%s\n' "$rc" >> "$probe"
  if [[ "$rc" -ne 1 ]]; then
    cat "$probe" >&2
    return 1
  fi
  grep -F 'Program: bwa' "$probe"
}

probe_readcounter() {
  local probe rc
  probe="$(mktemp "$LOG_DIR/readCounter-semantic-probe.${SESSION_ID}.XXXXXX.txt")"
  set +e
  "$ENV_ROOT/ichorcna/bin/readCounter" > "$probe" 2>&1
  rc=$?
  set -e
  printf 'readcounter_help_exit_code=%s\n' "$rc" >> "$probe"
  if [[ "$rc" -ne 255 ]]; then
    cat "$probe" >&2
    return 1
  fi
  grep -Fx 'Please specify a BAM file.' "$probe"
  grep -Eq '^Usage: .*/readCounter \[options\] <BAM file>$' "$probe"
}

probe_gistic() {
  local prefix="$ENV_ROOT/gistic" executable raw_probe mcr_probe raw_rc rc mcr_root
  local -a mcr_roots
  executable="$prefix/bin/gistic2"
  raw_probe="$(mktemp "$LOG_DIR/gistic-raw-probe.${SESSION_ID}.XXXXXX.txt")"
  mcr_probe="$(mktemp "$LOG_DIR/gistic-mcr-probe.${SESSION_ID}.XXXXXX.txt")"
  [[ -x "$executable" ]]
  shopt -s nullglob
  mcr_roots=("$prefix"/share/mcr-*/v*)
  shopt -u nullglob
  if [[ "${#mcr_roots[@]}" -ne 1 ]]; then
    printf 'Expected exactly one GISTIC MCR root below %s/share/mcr-*/v*; found %s\n' \
      "$prefix" "${#mcr_roots[@]}" >&2
    return 1
  fi
  mcr_root="${mcr_roots[0]}"
  test -d "$mcr_root/runtime/glnxa64"
  test -d "$mcr_root/bin/glnxa64"
  test -d "$mcr_root/sys/os/glnxa64"

  # Preserve the raw launch result as diagnostics. It is not accepted as the
  # semantic probe because the launcher requires exact-prefix MCR libraries.
  set +e
  env -u LD_LIBRARY_PATH -u LD_LIBRARY_PATH_MCR \
    "$executable" -h > "$raw_probe" 2>&1
  raw_rc=$?
  printf 'gistic_raw_help_exit_code=%s\n' "$raw_rc" >> "$raw_probe"

  env -u LD_LIBRARY_PATH -u LD_LIBRARY_PATH_MCR \
    CUDA_VISIBLE_DEVICES='' NVIDIA_VISIBLE_DEVICES=void \
    LD_LIBRARY_PATH="$mcr_root/runtime/glnxa64:$mcr_root/bin/glnxa64:$mcr_root/sys/os/glnxa64" \
    LD_LIBRARY_PATH_MCR='' \
    "$executable" -h > "$mcr_probe" 2>&1
  rc=$?
  set -e
  printf 'gistic_mcr_help_exit_code=%s\n' "$rc" >> "$mcr_probe"
  if [[ "$rc" -ne 0 ]]; then
    cat "$raw_probe" >&2
    cat "$mcr_probe" >&2
    return 1
  fi
  grep -Eq '^Usage: gp_gistic2_from_seg -b base_dir -seg segmentation_file$' \
    "$mcr_probe"
}

verify_qdnaseq_cache() {
  local relative annotation cache
  relative="$(<"$CONTEXT_DIR/qdnaseq-annotation-relative-path.txt")"
  [[ "$relative" == .oncotracer/reference-cache/qdnaseq-hg38-100kb-*/generations/generation-*/QDNAseq.hg38.100kbp.SR50.rds ]] || {
    echo "ERROR: invalid qDNAseq annotation relative path: $relative" >&2
    return 1
  }
  annotation="$VALIDATION_ROOT/$relative"
  cache="$(dirname -- "$annotation")"
  [[ "$(readlink -f -- "$annotation")" == "$annotation" ]] || {
    echo "ERROR: qDNAseq annotation path is not physical: $annotation" >&2
    return 1
  }
  local source_rda="$cache/QDNAseq.hg38.100kbp.SR50.source.rda"
  local provenance="$annotation.provenance.tsv"
  verify_tree_manifest "$cache" "$CONTEXT_DIR/qdnaseq-bin-data-SHA256SUMS"
  python3 - "$REPOSITORY_ROOT" "$source_rda" "$annotation" "$provenance" <<'PY'
import csv
import hashlib
import sys
from pathlib import Path

repository = Path(sys.argv[1])
source, annotation, provenance = map(Path, sys.argv[2:])
sys.path.insert(0, str(repository))
from oncotracer_cli.engine import (  # noqa: E402
    _qdnaseq_generation_from_pointer,
    _reference_identity,
)

cache = annotation.parent.parent.parent
validated_generation = _qdnaseq_generation_from_pointer(
    cache, 100, _reference_identity("qdnaseq-hg38-100kb")
)
if validated_generation != annotation.parent:
    raise SystemExit("qDNAseq annotation failed exact current-pointer validation")
expected_files = {source.name, annotation.name, provenance.name}
observed_files = {path.name for path in source.parent.iterdir()}
if observed_files != expected_files:
    raise SystemExit(
        "qDNAseq annotation cache inventory differs from the three recorded files: "
        f"expected {sorted(expected_files)!r}, observed {sorted(observed_files)!r}"
    )
with provenance.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.reader(handle, delimiter="\t"))
if not rows or rows[0] != ["field", "value"]:
    raise SystemExit("qDNAseq annotation provenance has an invalid header")
record = dict(rows[1:])
commit = "cf7c07e39de0ac64a9c38cb030cba4626e2aae83"
expected = {
    "source_commit": commit,
    "source_url": (
        "https://raw.githubusercontent.com/asntech/QDNAseq.hg38/"
        f"{commit}/data/hg38.100kbp.SR50.rda"
    ),
    "object": "hg38.100kbp.SR50",
    "source_rda_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "rds_sha256": hashlib.sha256(annotation.read_bytes()).hexdigest(),
}
for key, value in expected.items():
    if record.get(key) != value:
        raise SystemExit(
            f"qDNAseq annotation provenance mismatch for {key}: "
            f"expected {value!r}, observed {record.get(key)!r}"
        )
PY
  env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
    "$ENV_ROOT/qdnaseq/bin/Rscript" --vanilla -e \
    'library(Biobase); library(QDNAseq); x <- readRDS(commandArgs(TRUE)[1]); stopifnot(inherits(x, "AnnotatedDataFrame")); cat("QDNASEQ_CACHE_OK\n")' \
    "$annotation"
}

verify_doctor_record() {
  local record="$1"
  python3 - "$record" "$SOURCE_COMMIT" "$SOURCE_SHA256" "$ENV_ROOT" <<'PY'
import json
import sys
from pathlib import Path

record = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
source_commit, source_sha256 = sys.argv[2:4]
root = Path(sys.argv[4]).resolve()
if record.get("schema") != "oncotracer-doctor-v1":
    raise SystemExit("oncotracer doctor record has the wrong schema")
if record.get("oncotracer_version") != "2.0.0":
    raise SystemExit("oncotracer doctor did not run v2.0.0")
if record.get("backend") != "conda" or record.get("nextflow_required") is not False:
    raise SystemExit("oncotracer doctor did not validate the native Conda backend")
if record.get("success") is not True:
    raise SystemExit("oncotracer doctor did not pass")
source = record.get("source", {})
expected_source = {
    "source_commit": source_commit,
    "source_sha256": source_sha256,
    "source_sha256_definition": "sha256(git -c tar.umask=0002 archive --format=tar COMMIT)",
    "source_metadata_origin": "embedded",
    "source_tree_dirty": False,
    "success": True,
}
for key, value in expected_source.items():
    if source.get(key) != value:
        raise SystemExit(f"oncotracer doctor source mismatch for {key}")
groups = ("core", "qdnaseq", "ichorcna", "classifier", "gistic")
prefixes = record.get("prefixes", {})
environments = record.get("environments", {})
if set(prefixes) != set(groups) or set(environments) != set(groups):
    raise SystemExit("oncotracer doctor did not report all five native environments")
for group in groups:
    expected_path = root / group
    prefix = prefixes[group]
    if Path(str(prefix.get("path", ""))).resolve() != expected_path:
        raise SystemExit(f"oncotracer doctor used the wrong {group} prefix")
    if prefix.get("configured") is not True or prefix.get("exists") is not True:
        raise SystemExit(f"oncotracer doctor did not find the {group} prefix")
    if environments[group].get("success") is not True:
        raise SystemExit(f"oncotracer doctor semantic probe failed for {group}")
core_probes = set(environments["core"].get("probes", {}))
if core_probes != {"bwa", "samtools", "minimap2", "pigz", "picard"}:
    raise SystemExit("oncotracer doctor core probe matrix is incomplete")
PY
}

action_install_environments() {
  local install_stderr doctor_stderr
  install_stderr="$(mktemp "$LOG_DIR/native-install.${SESSION_ID}.XXXXXX.stderr.log")"
  doctor_stderr="$(mktemp "$LOG_DIR/native-doctor.${SESSION_ID}.XXXXXX.stderr.log")"
  run_copied_binary install --conda --prefix "$ENV_ROOT" \
    > "$CONTEXT_DIR/native-install.json" \
    2> "$install_stderr"
  local name
  for name in core qdnaseq ichorcna classifier gistic; do
    conda list --explicit --prefix "$ENV_ROOT/$name" > "$CONTEXT_DIR/native-${name}.explicit.txt"
  done

  test -x "$ENV_ROOT/core/bin/bwa"
  test -x "$ENV_ROOT/core/bin/samtools"
  test -x "$ENV_ROOT/core/bin/minimap2"
  test -x "$ENV_ROOT/core/bin/pigz"
  test -x "$ENV_ROOT/core/bin/picard"
  probe_bwa
  "$ENV_ROOT/core/bin/samtools" --version
  "$ENV_ROOT/core/bin/minimap2" --version
  "$ENV_ROOT/core/bin/pigz" --version
  probe_picard

  env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
    "$ENV_ROOT/qdnaseq/bin/Rscript" --vanilla -e \
    'library(Biobase); library(QDNAseq); cat("QDNASEQ_R_OK\n")'
  local annotation annotation_relative
  annotation="$(env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
    bash "$REPOSITORY_ROOT/bin/scripts/prepare_qdnaseq_bin_data.sh" \
      --binsize 100 \
      --rscript "$ENV_ROOT/qdnaseq/bin/Rscript" \
      --project-root "$VALIDATION_ROOT")"
  annotation_relative="${annotation#"$VALIDATION_ROOT"/}"
  [[ "$annotation_relative" != "$annotation" ]]
  printf '%s\n' "$annotation_relative" > "$CONTEXT_DIR/qdnaseq-annotation-relative-path.txt"
  env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
    "$ENV_ROOT/qdnaseq/bin/Rscript" --vanilla -e \
    'x <- readRDS(commandArgs(TRUE)[1]); stopifnot(inherits(x, "AnnotatedDataFrame")); cat("QDNASEQ_RDS_OK\n")' \
    "$annotation"
  test -s "$annotation.provenance.tsv"
  grep -F $'source_commit\t' "$annotation.provenance.tsv"
  grep -F $'source_rda_sha256\t' "$annotation.provenance.tsv"
  grep -F $'rds_sha256\t' "$annotation.provenance.tsv"
  write_tree_manifest \
    "$(dirname -- "$annotation")" \
    "$CONTEXT_DIR/qdnaseq-bin-data-SHA256SUMS"

  env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
    "$ENV_ROOT/ichorcna/bin/Rscript" --vanilla -e \
    'library(ichorCNA); cat("ICHORCNA_R_OK\n")'
  env -u R_HOME -u R_LIBS -u R_LIBS_USER -u R_LIBS_SITE \
    "$ENV_ROOT/ichorcna/bin/Rscript" --vanilla \
    "$REPOSITORY_ROOT/tests/test_ichorcna_plot_compat.R" \
    "$CONTEXT_DIR/ichorcna-zero-median-guard.pdf" \
    | tee "$CONTEXT_DIR/ichorcna-plot-compat-probe.txt"
  test -x "$ENV_ROOT/ichorcna/bin/readCounter"
  probe_readcounter

  CUDA_VISIBLE_DEVICES='' NVIDIA_VISIBLE_DEVICES=void \
  PYTHONDONTWRITEBYTECODE=1 \
    "$ENV_ROOT/classifier/bin/python" -c \
    'import huggingface_hub, jinja2, matplotlib, numpy, openpyxl, pandas, pypdf, reportlab, requests, safetensors, scipy, sklearn, torch, transformers; print("CLASSIFIER_PYTHON_CPU_OK")'
  test -x "$ENV_ROOT/gistic/bin/gistic2"
  probe_gistic

  run_copied_binary doctor --backend conda \
    > "$CONTEXT_DIR/native-doctor.json" \
    2> "$doctor_stderr"
  verify_doctor_record "$CONTEXT_DIR/native-doctor.json"
}

verify_install_environments() {
  local name current current_doctor
  for name in core qdnaseq ichorcna classifier gistic; do
    [[ -d "$ENV_ROOT/$name" ]]
    [[ -s "$CONTEXT_DIR/native-${name}.explicit.txt" ]]
    current="$TMP_DIR/native-${name}.explicit.current.txt"
    conda list --explicit --prefix "$ENV_ROOT/$name" > "$current"
    cmp -s "$current" "$CONTEXT_DIR/native-${name}.explicit.txt"
  done
  python3 - "$CONTEXT_DIR/native-install.json" "$ENV_ROOT" <<'PY'
import json
import sys
from pathlib import Path

install = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = Path(sys.argv[2]).resolve()
expected = {
    "core_prefix": root / "core",
    "qdnaseq_prefix": root / "qdnaseq",
    "ichorcna_prefix": root / "ichorcna",
    "classifier_prefix": root / "classifier",
    "gistic_prefix": root / "gistic",
}
if install.get("backend") != "conda":
    raise SystemExit("native install record is not the Conda backend")
for key, path in expected.items():
    if Path(str(install.get(key, ""))).resolve() != path:
        raise SystemExit(f"native install record has wrong {key}")
PY
  test -x "$ENV_ROOT/core/bin/bwa"
  test -x "$ENV_ROOT/core/bin/samtools"
  test -x "$ENV_ROOT/core/bin/minimap2"
  test -x "$ENV_ROOT/core/bin/pigz"
  test -x "$ENV_ROOT/core/bin/picard"
  test -x "$ENV_ROOT/qdnaseq/bin/Rscript"
  test -x "$ENV_ROOT/ichorcna/bin/Rscript"
  test -x "$ENV_ROOT/ichorcna/bin/readCounter"
  test -s "$CONTEXT_DIR/ichorcna-zero-median-guard.pdf"
  grep -Fx "ICHORCNA_ZERO_MEDIAN_PLOT_GUARD_OK" \
    "$CONTEXT_DIR/ichorcna-plot-compat-probe.txt"
  test -x "$ENV_ROOT/classifier/bin/python"
  test -x "$ENV_ROOT/gistic/bin/gistic2"
  probe_bwa
  probe_picard
  "$ENV_ROOT/core/bin/samtools" --version
  "$ENV_ROOT/core/bin/minimap2" --version
  "$ENV_ROOT/core/bin/pigz" --version
  probe_readcounter
  probe_gistic
  verify_qdnaseq_cache
  verify_doctor_record "$CONTEXT_DIR/native-doctor.json"
  current_doctor="$(mktemp "$TMP_DIR/native-doctor-current.XXXXXX.json")"
  run_copied_binary doctor --backend conda > "$current_doctor"
  verify_doctor_record "$current_doctor"
}

run_stage \
  "install-and-probe-five-environments" \
  "copied oncotracer install --conda --prefix ENV_ROOT; export five explicit specs; direct semantic BWA/samtools/minimap2/pigz/Picard/qDNAseq/Biobase/ichorCNA/classifier/GISTIC probes; oncotracer doctor --backend conda" \
  action_install_environments verify_install_environments \
  "$BINARY" "$REPOSITORY_ROOT/environments" "$REPOSITORY_ROOT/bin/scripts/prepare_qdnaseq_bin_data.sh"

verify_fastq() {
  local path="$1" expected_bytes="$2" expected_md5="$3"
  [[ -s "$path" ]]
  [[ "$(stat -c '%s' "$path")" == "$expected_bytes" ]]
  [[ "$(md5sum "$path" | awk '{print $1}')" == "$expected_md5" ]]
  gzip -t "$path"
}

verify_public_inputs() {
  verify_fastq \
    "$INPUT_ROOT/public/illumina_ERR12341627/ERR12341627_1.fastq.gz" \
    105996523 4c96d551152694b3893ea98b7781a3ae
  verify_fastq \
    "$INPUT_ROOT/public/illumina_ERR12341627/ERR12341627_2.fastq.gz" \
    23748473 1b20d9eb98f755244f6383ea1354bd40
  verify_fastq \
    "$INPUT_ROOT/public/ont_DRR165691/fastq_pass/barcode01/DRR165691_1.fastq.gz" \
    101734666 55a3984cb0334aa4cb0a38255cb71c06
  python3 - "$REPOSITORY_ROOT/examples/hcc1143_lpwgs/manifest.tsv" "$INPUT_ROOT/public/hcc1143_lpwgs" <<'PY'
import csv
import hashlib
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
reads = Path(sys.argv[2])
with manifest.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))
if len(rows) != 6:
    raise SystemExit(f"expected six HCC1143 FASTQs, found {len(rows)} manifest rows")
for row in rows:
    path = reads / row["filename"]
    if not path.is_file() or path.stat().st_size != int(row["bytes"]):
        raise SystemExit(f"HCC1143 size mismatch: {path}")
    digest_object = hashlib.md5()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest_object.update(block)
    digest = digest_object.hexdigest()
    if digest != row["md5"]:
        raise SystemExit(f"HCC1143 MD5 mismatch: {path}")
PY
  find "$INPUT_ROOT/public/hcc1143_lpwgs" -type f -name '*.fastq.gz' -print0 |
    xargs -0 -r -n1 gzip -t
  [[ -s "$INPUT_ROOT/configs/illumina.quickstart.yml" ]]
  [[ -s "$INPUT_ROOT/configs/ont.quickstart.yml" ]]
  [[ -s "$INPUT_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml" ]]
  verify_tree_manifest "$INPUT_ROOT" "$CONTEXT_DIR/public-input-SHA256SUMS"
}

action_prepare_inputs() {
  run_copied_binary quickstart 1 --test-root "$INPUT_ROOT" --download-only
  run_copied_binary quickstart 2 --test-root "$INPUT_ROOT" --download-only
  write_tree_manifest "$INPUT_ROOT" "$CONTEXT_DIR/public-input-SHA256SUMS"
}

run_stage \
  "prepare-checksummed-public-inputs" \
  "copied oncotracer quickstart 1 and 2 --download-only; validate exact public byte counts, MD5, gzip streams, and record SHA-256" \
  action_prepare_inputs verify_public_inputs \
  "$BINARY" "$REPOSITORY_ROOT/examples/hcc1143_lpwgs/manifest.tsv"

readonly REFERENCE_DEST="$ANALYSIS_ROOT/references/samurai_hg38"
action_prepare_reference() {
  mkdir -p "$REFERENCE_DEST"
  cp -a --reflink=auto "$SHARED_REFERENCE/." "$REFERENCE_DEST/"
  write_tree_manifest "$REFERENCE_DEST" "$CONTEXT_DIR/validation-reference-SHA256SUMS"
  cmp "$CONTEXT_DIR/shared-reference-SHA256SUMS" "$CONTEXT_DIR/validation-reference-SHA256SUMS"
}

verify_prepare_reference() {
  verify_reference_directory "$REFERENCE_DEST"
  verify_tree_manifest "$REFERENCE_DEST" "$CONTEXT_DIR/validation-reference-SHA256SUMS"
  cmp "$CONTEXT_DIR/shared-reference-SHA256SUMS" "$CONTEXT_DIR/validation-reference-SHA256SUMS"
}

run_stage \
  "prepare-shared-reference" \
  "copy/reflink checksum-verified shared hg38 FASTA, faidx, dictionary, BWA index, and minimap2 index into validation root" \
  action_prepare_reference verify_prepare_reference \
  "$CONTEXT_DIR/shared-reference-SHA256SUMS"

action_prepare_configs() {
  python3 "$REPOSITORY_ROOT/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/illumina.quickstart.yml" \
    --destination "$CONFIG_DIR/v1-illumina.yml" \
    --lpwgs-root "$ANALYSIS_ROOT" \
    --outdir "$ANALYSIS_ROOT/v1/illumina"
  python3 "$REPOSITORY_ROOT/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/illumina.quickstart.yml" \
    --destination "$CONFIG_DIR/v2-illumina.yml" \
    --lpwgs-root "$ANALYSIS_ROOT" \
    --outdir "$ANALYSIS_ROOT/v2/illumina"
  python3 "$REPOSITORY_ROOT/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/ont.quickstart.yml" \
    --destination "$CONFIG_DIR/v1-ont.yml" \
    --lpwgs-root "$ANALYSIS_ROOT" \
    --outdir "$ANALYSIS_ROOT/v1/ont"
  python3 "$REPOSITORY_ROOT/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/ont.quickstart.yml" \
    --destination "$CONFIG_DIR/v2-ont.yml" \
    --lpwgs-root "$ANALYSIS_ROOT" \
    --outdir "$ANALYSIS_ROOT/v2/ont"
  python3 "$REPOSITORY_ROOT/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml" \
    --destination "$CONFIG_DIR/v1-hcc1143.yml" \
    --lpwgs-root "$ANALYSIS_ROOT" \
    --outdir "$ANALYSIS_ROOT/v1/hcc1143"
  python3 "$REPOSITORY_ROOT/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml" \
    --destination "$CONFIG_DIR/v2-hcc1143.yml" \
    --lpwgs-root "$ANALYSIS_ROOT" \
    --outdir "$ANALYSIS_ROOT/v2/hcc1143"

  local nested_home
  for nested_home in \
    "$ANALYSIS_ROOT/v1/illumina/01_samurai_illumina/.nextflow" \
    "$ANALYSIS_ROOT/v1/ont/01_samurai_ont/.nextflow" \
    "$ANALYSIS_ROOT/v1/hcc1143/01_samurai_illumina/.nextflow"; do
    mkdir -p "$nested_home"
    cp "$CONTEXT_DIR/samurai-nextflow-audit.config" "$nested_home/config"
  done
  write_tree_manifest "$CONFIG_DIR" "$CONTEXT_DIR/parity-config-SHA256SUMS"
}

verify_prepare_configs() {
  local name nested_home
  for name in v1-illumina v2-illumina v1-ont v2-ont v1-hcc1143 v2-hcc1143; do
    [[ -s "$CONFIG_DIR/$name.yml" ]]
    grep -F "lpwgs_root: $ANALYSIS_ROOT" "$CONFIG_DIR/$name.yml"
    grep -F 'force: true' "$CONFIG_DIR/$name.yml"
  done
  for nested_home in \
    "$ANALYSIS_ROOT/v1/illumina/01_samurai_illumina/.nextflow" \
    "$ANALYSIS_ROOT/v1/ont/01_samurai_ont/.nextflow" \
    "$ANALYSIS_ROOT/v1/hcc1143/01_samurai_illumina/.nextflow"; do
    cmp -s "$nested_home/config" "$CONTEXT_DIR/samurai-nextflow-audit.config"
  done
  (cd "$REPOSITORY_ROOT" &&
    sha256sum -c "$CONTEXT_DIR/v1-ichorcna-plot-compat-SHA256SUMS")
  verify_tree_manifest "$CONFIG_DIR" "$CONTEXT_DIR/parity-config-SHA256SUMS"
}

run_stage \
  "prepare-isolated-parity-configs" \
  "rewrite only lpwgs_root/outdir/force for separate frozen-v1.1 and copied-native-v2 QuickStart configurations; create nested v1 resource limits and container-aware trace fields" \
  action_prepare_configs verify_prepare_configs \
  "$CONTEXT_DIR/public-input-SHA256SUMS" "$CONTEXT_DIR/samurai-nextflow-audit.config" \
  "$CONTEXT_DIR/v1-ichorcna-plot-compat-SHA256SUMS" \
  "$REPOSITORY_ROOT/tests/make_parity_config.py"

required_output_paths() {
  local root="$1"
  printf '%s\n' \
    "$root/06_workflow_summary/workflow_summary.txt" \
    "$root/03_cna_codification/cna_events.tsv" \
    "$root/03_cna_codification/cna_cytogenomic_notation.tsv" \
    "$root/04_cna_custom_plots/cna_per_sample_pages.pdf" \
    "$root/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf"
}

verify_analysis_outputs() {
  local root="$1" manifest="$2" native="$3" path
  while IFS= read -r path; do
    [[ -s "$path" ]] || {
      printf 'Missing or empty required analysis output: %s\n' "$path" >&2
      return 1
    }
  done < <(required_output_paths "$root")
  [[ -n "$(find "$root/02_bam_refinement" -path '*/01_tables/refined_bins.tsv.gz' -type f -size +0c -print -quit)" ]]
  if [[ "$native" == true ]]; then
    [[ -s "$root/.oncotracer-native/trace.tsv" ]]
    if grep -qi 'nextflow' "$root/.oncotracer-native/trace.tsv"; then
      printf 'Native trace contains a forbidden Nextflow command: %s\n' "$root/.oncotracer-native/trace.tsv" >&2
      return 1
    fi
    grep -Fx 'engine=native' "$root/06_workflow_summary/workflow_summary.txt"
    grep -Fx 'nextflow_used=false' "$root/06_workflow_summary/workflow_summary.txt"
  fi
  verify_tree_manifest "$root" "$manifest"
}

install_samurai_nextflow_audit_config() {
  local run_root="$1" target preserved
  target="$run_root/.nextflow/config"
  mkdir -p "$(dirname "$target")"
  if [[ -e "$target" ]] &&
     ! cmp -s "$target" "$CONTEXT_DIR/samurai-nextflow-audit.config"; then
    preserved="${target}.invalid.${SESSION_ID}"
    [[ ! -e "$preserved" ]] || die "preservation path already exists: $preserved"
    mv -- "$target" "$preserved"
  fi
  if [[ ! -e "$target" ]]; then
    cp "$CONTEXT_DIR/samurai-nextflow-audit.config" "$target"
  fi
  cmp -s "$target" "$CONTEXT_DIR/samurai-nextflow-audit.config"
}

verify_samurai_nextflow_audit_config() {
  local run_root="$1"
  cmp -s "$run_root/.nextflow/config" \
    "$CONTEXT_DIR/samurai-nextflow-audit.config"
}

capture_samurai_trace_inventory() {
  local root="$1" destination="$2"
  python3 "$REPOSITORY_ROOT/tests/verify_nested_samurai.py" \
    --snapshot-root "$root" \
    --snapshot-out "$destination"
  [[ -s "$destination" ]]
}

generate_samurai_trace_audit() {
  local root="$1" mode="$2" expected_rows="$3" destination="$4" trace_copy="$5"
  local pre_inventory="$6" post_inventory="$7" delta_inventory="$8"
  local artifact_prefix="$9" selected="${10-}" source_manifest_copy="${11-}"
  local raw_source_dir="${12-}" marker_copy="${13-}"
  local temporary selected_output copy_tmp
  temporary="$(mktemp "$TMP_DIR/samurai-trace-audit.XXXXXX")"
  if ! selected_output="$(
    python3 - \
      "$root" "$mode" "$expected_rows" \
      "$CONTEXT_DIR/samurai-container-pins.tsv" "$temporary" \
      "$pre_inventory" "$post_inventory" "$delta_inventory" \
      "$artifact_prefix" "$selected" "$source_manifest_copy" \
      "$raw_source_dir" "$marker_copy" "$REPOSITORY_ROOT/tests" <<'PY'
import csv
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

root = Path(sys.argv[1]).resolve()
mode = sys.argv[2]
expected_rows = int(sys.argv[3])
pins_path = Path(sys.argv[4])
destination = Path(sys.argv[5])
pre_inventory = Path(sys.argv[6])
post_inventory = Path(sys.argv[7])
delta_inventory = Path(sys.argv[8])
artifact_prefix = sys.argv[9]
selected_arg = sys.argv[10]
source_manifest_copy_arg = sys.argv[11]
raw_source_dir_arg = sys.argv[12]
marker_copy_arg = sys.argv[13]
tests_dir = Path(sys.argv[14]).resolve()
sys.path.insert(0, str(tests_dir))
from combine_nested_samurai_traces import combine_root  # noqa: E402
from combine_nested_samurai_traces import materialize_trace_sources  # noqa: E402
from combine_nested_samurai_traces import recompute_preserved_trace_artifact  # noqa: E402
from verify_nested_samurai import find_compat_marker  # noqa: E402
from verify_nested_samurai import verify_current_trace_invocation  # noqa: E402

expected_processes = {
    "illumina": {
        "SAMURAI:FASTQ_TRIM_FASTP_FASTQC:FASTQC_RAW",
        "SAMURAI:FASTQ_ALIGN_DNA:BWAMEM1_MEM",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:PICARD_MARKDUPLICATES",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:SAMTOOLS_INDEX",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:BAM_STATS_SAMTOOLS:SAMTOOLS_STATS",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:BAM_STATS_SAMTOOLS:SAMTOOLS_FLAGSTAT",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:BAM_STATS_SAMTOOLS:SAMTOOLS_IDXSTATS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
        "SAMURAI:SOLID_BIOPSY:QDNASEQ",
        "SAMURAI:SOLID_BIOPSY:CONCATENATE_QDNASEQ_PLOTS",
        "SAMURAI:MULTIQC",
    },
    "ont": {
        "SAMURAI:SAMTOOLS_INDEX",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:HMMCOPY_READCOUNTER_ICHORCNA",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:ICHORCNA_RUN",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:AGGREGATE_ICHORCNA_TABLE",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:CORRECT_LOGR_ICHORCNA",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:PLOT_ICHORCNA",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:CONCATENATE_BIN_PLOTS",
        "SAMURAI:MULTIQC",
    },
}
expected_images = {
    "illumina": {
        "quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0",
        "community.wave.seqera.io/library/bwa_htslib_samtools:83b50ff84ead50d0",
        "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
        "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
        "quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1",
        "docker.io/t0shy/qpdf-docker:11.3.0",
        "community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf",
    },
    "ont": {
        "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
        "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
        "community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2",
        "community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4",
        "quay.io/einar_rainhart/pandas-pandera:1.5.3",
        "community.wave.seqera.io/library/polars_procps-ng_typer:d1a53d7945a021e3",
        "community.wave.seqera.io/library/procps-ng_r-argparser_r-dplyr_r-ggplot2_pruned:10da72fa04bcba1a",
        "docker.io/t0shy/qpdf-docker:11.3.0",
        "community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf",
    },
}
if mode not in expected_processes:
    raise SystemExit(f"unsupported SAMURAI trace mode: {mode}")
if re.fullmatch(r"v1-(?:illumina|ont|hcc1143)-samurai", artifact_prefix) is None:
    raise SystemExit(f"unsafe trace artifact prefix: {artifact_prefix!r}")
trace_artifact = f"{artifact_prefix}-execution-trace.txt"
manifest_artifact = f"{artifact_prefix}-trace-sources.tsv"
source_files_artifact = f"{artifact_prefix}-trace-source-files"
marker_artifact = f"{artifact_prefix}-ichorcna-plot-compat.tsv"

pins = {}
with pins_path.open(encoding="utf-8") as handle:
    for line in handle:
        tag, digest = line.rstrip("\n").split("\t")
        aliases = {tag, f"{tag}@{digest}"}
        if tag.startswith("quay.io/biocontainers/"):
            aliases.add(tag.removeprefix("quay.io/"))
        if tag.startswith("docker.io/"):
            aliases.add(tag.removeprefix("docker.io/"))
        for alias in aliases:
            if alias in pins and pins[alias] != (tag, digest):
                raise SystemExit(f"ambiguous SAMURAI image alias: {alias}")
            pins[alias] = (tag, digest)

with redirect_stdout(sys.stderr):
    selected, source_manifest, _ = combine_root(root)
selected = selected.resolve()
source_manifest = source_manifest.resolve()
if selected_arg and selected_arg != trace_artifact:
    raise SystemExit(
        f"recorded combined SAMURAI artifact changed: expected {selected_arg!r}, "
        f"observed {trace_artifact!r}"
    )
if bool(source_manifest_copy_arg) != bool(raw_source_dir_arg):
    raise SystemExit("source-manifest copy and raw-source directory must be supplied together")
if source_manifest_copy_arg:
    source_manifest_copy = Path(source_manifest_copy_arg)
    raw_source_dir = Path(raw_source_dir_arg)
    if source_manifest_copy.name != manifest_artifact or raw_source_dir.name != source_files_artifact:
        raise SystemExit("trace artifact output names do not match the deterministic contract")
    source_manifest_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_manifest, source_manifest_copy)
    materialize_trace_sources(root, source_manifest, raw_source_dir)
    with tempfile.TemporaryDirectory(prefix=f"oncotracer-{artifact_prefix}-recompute-") as directory:
        with redirect_stdout(sys.stderr):
            recomputed, regenerated, _ = recompute_preserved_trace_artifact(
                raw_source_dir, source_manifest_copy, Path(directory)
            )
        if recomputed.read_bytes() != selected.read_bytes():
            raise SystemExit("preserved raw traces do not reproduce the combined trace")
        if regenerated.read_bytes() != source_manifest_copy.read_bytes():
            raise SystemExit("preserved raw traces do not reproduce the source manifest")

rows = []
process_names = []
containers = set()
with selected.open(encoding="utf-8-sig", newline="") as handle:
    reader = csv.DictReader(handle, delimiter="\t")
    required = {"hash", "name", "status", "exit", "container"}
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise SystemExit(f"SAMURAI trace lacks required column(s): {sorted(missing)}")
    for row in reader:
        name = (row.get("name") or "").rstrip("\r")
        normalized = re.sub(
            r"\s+\([^()]*(?:\([^()]*\)[^()]*)*\)$", "", name.strip()
        )
        if ":SAMURAI:" in normalized:
            normalized = "SAMURAI:" + normalized.rsplit(":SAMURAI:", 1)[1]
        if normalized not in expected_processes[mode]:
            continue
        status = (row.get("status") or "").rstrip("\r").upper()
        exit_code = (row.get("exit") or "").rstrip("\r")
        container = (row.get("container") or "").rstrip("\r").removeprefix("docker://")
        task_hash = (row.get("hash") or "").rstrip("\r").lower()
        if status not in {"COMPLETED", "CACHED"} or exit_code != "0":
            raise SystemExit(
                f"non-passing contracted SAMURAI row: {name!r} "
                f"status={status!r} exit={exit_code!r}"
            )
        if re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{6,}", task_hash) is None:
            raise SystemExit(f"invalid contracted SAMURAI task hash: {task_hash!r}")
        if container in {"", "-", "null"} or container not in pins:
            raise SystemExit(f"unresolved or forbidden SAMURAI container: {container!r}")
        canonical, digest = pins[container]
        containers.add(canonical)
        process_names.append(normalized)
        rows.append(
            {
                "hash": task_hash,
                "name": name,
                "normalized_process": normalized,
                "status": status,
                "exit": exit_code,
                "container": container,
                "canonical_container": canonical,
                "repo_digest": digest,
                "source_trace": row.get("source_trace", ""),
                "source_row": row.get("source_row", ""),
            }
        )

processes = set(process_names)
complete = (
    len(rows) == expected_rows
    and processes == expected_processes[mode]
    and containers == expected_images[mode]
)
if not complete:
    raise SystemExit(
        "SAMURAI combined trace contract mismatch: "
        f"mode={mode!r} expected_rows={expected_rows} observed_rows={len(rows)} "
        f"counts={dict(Counter(process_names))!r} "
        f"missing_processes={sorted(expected_processes[mode] - processes)!r} "
        f"extra_processes={sorted(processes - expected_processes[mode])!r} "
        f"observed_containers={sorted(containers)!r}"
    )


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

with source_manifest.open(newline="", encoding="utf-8") as handle:
    source_rows = list(csv.DictReader(handle, delimiter="\t"))
available = []
for source in source_rows:
    source_path = (root / source["source_trace"]).resolve()
    source_path.relative_to(root)
    if sha256(source_path) != source["sha256"]:
        raise SystemExit(f"source trace checksum changed: {source_path}")
    available.append(
        {
            "relative_path": source["source_trace"],
            "artifact_path": f"{source_files_artifact}/{source['source_trace']}",
            "mtime_ns": int(source["mtime_ns"]),
            "bytes": int(source["bytes"]),
            "rows": int(source["rows"]),
            "successful_rows": int(source["successful_rows"]),
            "sha256": source["sha256"],
        }
    )


invocation = verify_current_trace_invocation(
    root,
    pre_inventory,
    source_manifest,
    rows,
    post_inventory,
    delta_inventory,
    require_ichorcna=mode == "ont",
)
compatibility = None
if mode == "ont":
    selected_marker, metadata, task_hash, marker_relative = find_compat_marker(
        root, rows
    )
    if marker_copy_arg:
        marker_copy = Path(marker_copy_arg)
        if marker_copy.name != marker_artifact:
            raise SystemExit("compatibility-marker artifact name is not deterministic")
        marker_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(selected_marker, marker_copy)
    compatibility = {
        "artifact": marker_artifact,
        "relative_path": marker_relative.as_posix(),
        "task_hash": task_hash,
        "sha256": sha256(selected_marker),
        "metadata": metadata,
    }
elif marker_copy_arg:
    raise SystemExit("a compatibility-marker artifact is valid only for ONT")

record = {
    "schema": "oncotracer-samurai-trace-audit-v1",
    "mode": mode,
    "evidence_mode": "complete-combined-trace",
    "source_trace": trace_artifact,
    "source_trace_sha256": sha256(selected),
    "source_manifest": manifest_artifact,
    "source_manifest_sha256": sha256(source_manifest),
    "source_files": source_files_artifact,
    "available_traces": available,
    "contract_row_count": expected_rows,
    "row_count": len(rows),
    "processes": sorted(processes),
    "contract_processes": sorted(expected_processes[mode]),
    "containers": sorted(containers),
    "contract_containers": sorted(expected_images[mode]),
    "ichorcna_plot_compat": compatibility,
    "rows": rows,
    "trace_invocation": invocation,
}
destination.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(selected)
PY
  )"; then
    rm -f -- "$temporary"
    return 1
  fi
  if [[ -z "$selected_output" || ! -f "$selected_output" || ! -s "$temporary" ]]; then
    rm -f -- "$temporary"
    return 1
  fi
  copy_tmp="$(mktemp "$TMP_DIR/samurai-trace-copy.XXXXXX")" || return 1
  if ! cp "$selected_output" "$copy_tmp"; then
    rm -f -- "$temporary" "$copy_tmp"
    return 1
  fi
  mv -- "$copy_tmp" "$trace_copy" || return 1
  mv -- "$temporary" "$destination" || return 1
}

verify_samurai_trace_audit() {
  local root="$1" mode="$2" expected_rows="$3" evidence="$4" trace_copy="$5"
  local pre_inventory="$6" post_inventory="$7" delta_inventory="$8"
  local artifact_prefix="$9" source_manifest="${10}" raw_sources="${11}"
  local marker_copy="${12-}" temporary temporary_trace temporary_post temporary_delta
  temporary="$(mktemp "$TMP_DIR/samurai-trace-verify.XXXXXX")"
  temporary_trace="$(mktemp "$TMP_DIR/samurai-trace-copy-verify.XXXXXX")"
  temporary_post="$(mktemp "$TMP_DIR/samurai-trace-post-verify.XXXXXX")"
  temporary_delta="$(mktemp "$TMP_DIR/samurai-trace-delta-verify.XXXXXX")"
  generate_samurai_trace_audit \
    "$root" "$mode" "$expected_rows" "$temporary" "$temporary_trace" \
    "$pre_inventory" "$temporary_post" "$temporary_delta" "$artifact_prefix" \
    "${artifact_prefix}-execution-trace.txt" "" "" ""
  cmp -s "$temporary" "$evidence"
  cmp -s "$temporary_trace" "$trace_copy"
  cmp -s "$temporary_post" "$post_inventory"
  cmp -s "$temporary_delta" "$delta_inventory"
  python3 "$REPOSITORY_ROOT/tests/verify_nested_samurai.py" \
    --verify-artifact-context "$CONTEXT_DIR" \
    --artifact-prefix "$artifact_prefix" \
    --artifact-mode "$mode" \
    --artifact-expected-rows "$expected_rows"
  python3 "$REPOSITORY_ROOT/tests/parity_audit.py" verify-trace-proof \
    --raw-root "$raw_sources" \
    --source-manifest "$source_manifest" \
    --combined-trace "$trace_copy"
  if [[ "$mode" == ont ]]; then
    [[ -s "$marker_copy" ]]
  fi
  rm -f -- "$temporary" "$temporary_trace" "$temporary_post" "$temporary_delta"
}

action_v1_quickstart1() {
  verify_prepare_pinned_nextflow
  verify_prepare_pinned_samurai
  verify_prepare_samurai_containers
  install_samurai_nextflow_audit_config \
    "$ANALYSIS_ROOT/v1/illumina/01_samurai_illumina"
  install_samurai_nextflow_audit_config \
    "$ANALYSIS_ROOT/v1/ont/01_samurai_ont"
  local report_session="$REPORT_DIR/frozen-v1.1-quickstart1-$SESSION_ID"
  mkdir -p "$WORK_DIR/v1-launch/quickstart1" "$report_session"
  (
    export PATH="$TOOL_BIN:$PATH"
    [[ "$(command -v nextflow)" == "$NEXTFLOW" ]]
    cd "$WORK_DIR/v1-launch/quickstart1"
    capture_samurai_trace_inventory \
      "$ANALYSIS_ROOT/v1/illumina/01_samurai_illumina" \
      "$CONTEXT_DIR/v1-illumina-samurai-trace-pre.tsv"
    "$NEXTFLOW" -log "$report_session/v1-illumina.nextflow.log" run \
      "$V1_SOURCE_DIR/main.nf" --docker \
      --docker_image "$V1_DOCKER_IMAGE" \
      -params-file "$CONFIG_DIR/v1-illumina.yml" \
      -work-dir "$WORK_DIR/v1-illumina" \
      -with-report "$report_session/v1-illumina.html" \
      -with-trace "$report_session/v1-illumina.tsv"
    capture_samurai_trace_inventory \
      "$ANALYSIS_ROOT/v1/ont/01_samurai_ont" \
      "$CONTEXT_DIR/v1-ont-samurai-trace-pre.tsv"
    "$NEXTFLOW" -log "$report_session/v1-ont.nextflow.log" run \
      "$V1_SOURCE_DIR/main.nf" --docker \
      --docker_image "$V1_DOCKER_IMAGE" \
      -params-file "$CONFIG_DIR/v1-ont.yml" \
      -work-dir "$WORK_DIR/v1-ont" \
      -with-report "$report_session/v1-ont.html" \
      -with-trace "$report_session/v1-ont.tsv"
  )
  generate_samurai_trace_audit \
    "$ANALYSIS_ROOT/v1/illumina/01_samurai_illumina" illumina 12 \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-audit.json" \
    "$CONTEXT_DIR/v1-illumina-samurai-execution-trace.txt" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-pre.tsv" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-post.tsv" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-delta.tsv" \
    "v1-illumina-samurai" "" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-sources.tsv" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-source-files" ""
  generate_samurai_trace_audit "$ANALYSIS_ROOT/v1/ont/01_samurai_ont" ont 10 \
    "$CONTEXT_DIR/v1-ont-samurai-trace-audit.json" \
    "$CONTEXT_DIR/v1-ont-samurai-execution-trace.txt" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-pre.tsv" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-post.tsv" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-delta.tsv" \
    "v1-ont-samurai" "" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-sources.tsv" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-source-files" \
    "$CONTEXT_DIR/v1-ont-samurai-ichorcna-plot-compat.tsv"
  write_tree_manifest "$ANALYSIS_ROOT/v1/illumina" "$CONTEXT_DIR/v1-illumina-output-SHA256SUMS"
  write_tree_manifest "$ANALYSIS_ROOT/v1/ont" "$CONTEXT_DIR/v1-ont-output-SHA256SUMS"
}

verify_v1_quickstart1() {
  verify_analysis_outputs "$ANALYSIS_ROOT/v1/illumina" "$CONTEXT_DIR/v1-illumina-output-SHA256SUMS" false
  verify_analysis_outputs "$ANALYSIS_ROOT/v1/ont" "$CONTEXT_DIR/v1-ont-output-SHA256SUMS" false
  verify_prepare_pinned_nextflow
  verify_prepare_pinned_samurai
  verify_prepare_samurai_containers
  verify_samurai_nextflow_audit_config \
    "$ANALYSIS_ROOT/v1/illumina/01_samurai_illumina"
  verify_samurai_nextflow_audit_config "$ANALYSIS_ROOT/v1/ont/01_samurai_ont"
  verify_samurai_trace_audit \
    "$ANALYSIS_ROOT/v1/illumina/01_samurai_illumina" illumina 12 \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-audit.json" \
    "$CONTEXT_DIR/v1-illumina-samurai-execution-trace.txt" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-pre.tsv" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-post.tsv" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-delta.tsv" \
    "v1-illumina-samurai" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-sources.tsv" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-source-files" ""
  verify_samurai_trace_audit "$ANALYSIS_ROOT/v1/ont/01_samurai_ont" ont 10 \
    "$CONTEXT_DIR/v1-ont-samurai-trace-audit.json" \
    "$CONTEXT_DIR/v1-ont-samurai-execution-trace.txt" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-pre.tsv" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-post.tsv" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-delta.tsv" \
    "v1-ont-samurai" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-sources.tsv" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-source-files" \
    "$CONTEXT_DIR/v1-ont-samurai-ichorcna-plot-compat.tsv"
}

run_stage \
  "frozen-v1.1-quickstart1" \
  "Nextflow comparator only: exact v1.1 main.nf with pinned Docker digest for complete ERR12341627 Illumina and DRR165691 ONT baselines" \
  action_v1_quickstart1 verify_v1_quickstart1 \
  "$CONTEXT_DIR/v1.1-source-SHA256SUMS" "$CONTEXT_DIR/v1.1-container-repodigests.txt" \
  "$CONTEXT_DIR/v1.1-source-structure.json" \
  "$CONTEXT_DIR/nextflow-actual.txt" "$CONTEXT_DIR/samurai-provenance.txt" \
  "$CONTEXT_DIR/samurai-prepared-tree-SHA256SUMS" \
  "$CONTEXT_DIR/samurai-prepared-structure.json" \
  "$CONTEXT_DIR/samurai-container-pins.tsv" \
  "$CONTEXT_DIR/samurai-container-identities.tsv" \
  "$CONTEXT_DIR/samurai-nextflow-audit.config" \
  "$CONTEXT_DIR/v1-ichorcna-plot-compat-SHA256SUMS" \
  "$CONTEXT_DIR/public-input-SHA256SUMS" "$CONTEXT_DIR/validation-reference-SHA256SUMS" \
  "$CONFIG_DIR/v1-illumina.yml" "$CONFIG_DIR/v1-ont.yml" \
  "$REPOSITORY_ROOT/tests/verify_nested_samurai.py" \
  "$REPOSITORY_ROOT/tests/combine_nested_samurai_traces.py" \
  "$REPOSITORY_ROOT/tests/parity_audit.py"

action_v1_quickstart2() {
  verify_prepare_pinned_nextflow
  verify_prepare_pinned_samurai
  verify_prepare_samurai_containers
  install_samurai_nextflow_audit_config \
    "$ANALYSIS_ROOT/v1/hcc1143/01_samurai_illumina"
  local report_session="$REPORT_DIR/frozen-v1.1-quickstart2-$SESSION_ID"
  mkdir -p "$WORK_DIR/v1-launch/quickstart2" "$report_session"
  (
    export PATH="$TOOL_BIN:$PATH"
    [[ "$(command -v nextflow)" == "$NEXTFLOW" ]]
    cd "$WORK_DIR/v1-launch/quickstart2"
    capture_samurai_trace_inventory \
      "$ANALYSIS_ROOT/v1/hcc1143/01_samurai_illumina" \
      "$CONTEXT_DIR/v1-hcc1143-samurai-trace-pre.tsv"
    "$NEXTFLOW" -log "$report_session/v1-hcc1143.nextflow.log" run \
      "$V1_SOURCE_DIR/main.nf" --docker \
      --docker_image "$V1_DOCKER_IMAGE" \
      -params-file "$CONFIG_DIR/v1-hcc1143.yml" \
      -work-dir "$WORK_DIR/v1-hcc1143" \
      -with-report "$report_session/v1-hcc1143.html" \
      -with-trace "$report_session/v1-hcc1143.tsv"
  )
  generate_samurai_trace_audit \
    "$ANALYSIS_ROOT/v1/hcc1143/01_samurai_illumina" illumina 32 \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-audit.json" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-execution-trace.txt" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-pre.tsv" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-post.tsv" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-delta.tsv" \
    "v1-hcc1143-samurai" "" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-sources.tsv" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-source-files" ""
  write_tree_manifest "$ANALYSIS_ROOT/v1/hcc1143" "$CONTEXT_DIR/v1-hcc1143-output-SHA256SUMS"
}

verify_v1_quickstart2() {
  verify_analysis_outputs "$ANALYSIS_ROOT/v1/hcc1143" "$CONTEXT_DIR/v1-hcc1143-output-SHA256SUMS" false
  verify_prepare_pinned_nextflow
  verify_prepare_pinned_samurai
  verify_prepare_samurai_containers
  verify_samurai_nextflow_audit_config \
    "$ANALYSIS_ROOT/v1/hcc1143/01_samurai_illumina"
  verify_samurai_trace_audit \
    "$ANALYSIS_ROOT/v1/hcc1143/01_samurai_illumina" illumina 32 \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-audit.json" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-execution-trace.txt" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-pre.tsv" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-post.tsv" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-delta.tsv" \
    "v1-hcc1143-samurai" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-sources.tsv" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-source-files" ""
}

run_stage \
  "frozen-v1.1-quickstart2" \
  "Nextflow comparator only: exact v1.1 main.nf with pinned Docker digest for complete six-FASTQ, three-library HCC1143 baseline" \
  action_v1_quickstart2 verify_v1_quickstart2 \
  "$CONTEXT_DIR/v1.1-source-SHA256SUMS" "$CONTEXT_DIR/v1.1-container-repodigests.txt" \
  "$CONTEXT_DIR/v1.1-source-structure.json" \
  "$CONTEXT_DIR/nextflow-actual.txt" "$CONTEXT_DIR/samurai-provenance.txt" \
  "$CONTEXT_DIR/samurai-prepared-tree-SHA256SUMS" \
  "$CONTEXT_DIR/samurai-prepared-structure.json" \
  "$CONTEXT_DIR/samurai-container-pins.tsv" \
  "$CONTEXT_DIR/samurai-container-identities.tsv" \
  "$CONTEXT_DIR/samurai-nextflow-audit.config" \
  "$CONTEXT_DIR/v1-ichorcna-plot-compat-SHA256SUMS" \
  "$CONTEXT_DIR/public-input-SHA256SUMS" "$CONTEXT_DIR/validation-reference-SHA256SUMS" \
  "$CONFIG_DIR/v1-hcc1143.yml" "$REPOSITORY_ROOT/tests/verify_nested_samurai.py" \
  "$REPOSITORY_ROOT/tests/combine_nested_samurai_traces.py" \
  "$REPOSITORY_ROOT/tests/parity_audit.py"

action_v2_quickstart1() {
  run_copied_binary run --backend conda --config "$CONFIG_DIR/v2-illumina.yml" --threads "$THREADS"
  run_copied_binary run --backend conda --config "$CONFIG_DIR/v2-ont.yml" --threads "$THREADS"
  write_tree_manifest "$ANALYSIS_ROOT/v2/illumina" "$CONTEXT_DIR/v2-illumina-output-SHA256SUMS"
  write_tree_manifest "$ANALYSIS_ROOT/v2/ont" "$CONTEXT_DIR/v2-ont-output-SHA256SUMS"
}

verify_v2_quickstart1() {
  verify_analysis_outputs "$ANALYSIS_ROOT/v2/illumina" "$CONTEXT_DIR/v2-illumina-output-SHA256SUMS" true
  verify_analysis_outputs "$ANALYSIS_ROOT/v2/ont" "$CONTEXT_DIR/v2-ont-output-SHA256SUMS" true
}

run_stage \
  "native-v2-quickstart1" \
  "copied native v2 executable, Conda backend, complete ERR12341627 Illumina and DRR165691 ONT candidates; no Nextflow command" \
  action_v2_quickstart1 verify_v2_quickstart1 \
  "$BINARY" "$CONTEXT_DIR/native-core.explicit.txt" "$CONTEXT_DIR/native-qdnaseq.explicit.txt" \
  "$CONTEXT_DIR/native-ichorcna.explicit.txt" "$CONTEXT_DIR/native-classifier.explicit.txt" \
  "$CONTEXT_DIR/native-gistic.explicit.txt" "$CONTEXT_DIR/public-input-SHA256SUMS" \
  "$CONTEXT_DIR/validation-reference-SHA256SUMS" "$CONFIG_DIR/v2-illumina.yml" "$CONFIG_DIR/v2-ont.yml"

action_v2_quickstart2() {
  run_copied_binary run --backend conda --config "$CONFIG_DIR/v2-hcc1143.yml" --threads "$THREADS"
  write_tree_manifest "$ANALYSIS_ROOT/v2/hcc1143" "$CONTEXT_DIR/v2-hcc1143-output-SHA256SUMS"
}

verify_v2_quickstart2() {
  verify_analysis_outputs "$ANALYSIS_ROOT/v2/hcc1143" "$CONTEXT_DIR/v2-hcc1143-output-SHA256SUMS" true
}

run_stage \
  "native-v2-quickstart2" \
  "copied native v2 executable, Conda backend, complete six-FASTQ three-library HCC1143 candidate; no Nextflow command" \
  action_v2_quickstart2 verify_v2_quickstart2 \
  "$BINARY" "$CONTEXT_DIR/native-core.explicit.txt" "$CONTEXT_DIR/native-qdnaseq.explicit.txt" \
  "$CONTEXT_DIR/native-ichorcna.explicit.txt" "$CONTEXT_DIR/native-classifier.explicit.txt" \
  "$CONTEXT_DIR/native-gistic.explicit.txt" "$CONTEXT_DIR/public-input-SHA256SUMS" \
  "$CONTEXT_DIR/validation-reference-SHA256SUMS" "$CONFIG_DIR/v2-hcc1143.yml"

assert_parity_report() {
  local report="$1"
  python3 - "$report" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "minimum_event_overlap": 0.80,
    "minimum_event_recall": 0.90,
    "minimum_event_precision": 0.90,
    "minimum_profile_correlation": 0.98,
    "maximum_profile_median_absolute_difference": 0.08,
    "minimum_shared_bin_fraction": 0.95,
}
if report.get("thresholds") != expected:
    raise SystemExit(f"parity thresholds differ from release policy: {report.get('thresholds')!r}")
if report.get("passed") is not True:
    raise SystemExit("parity report failed")
checks = report.get("checks", {})
if not checks or not all(value is True for value in checks.values()):
    raise SystemExit(f"one or more parity checks failed: {checks!r}")
PY
}

copy_immutable_audit_file() {
  local source="$1" destination="$2" source_metadata destination_metadata
  if [[ ! -f "$source" || -L "$source" ]]; then
    printf 'Immutable audit source is not a physical regular file: %s\n' "$source" >&2
    return 1
  fi
  source_metadata="$(stat -c '%a:%h' -- "$source")"
  if [[ "$source_metadata" != "444:1" ]]; then
    printf 'Immutable audit source has unsafe mode or link count (%s): %s\n' \
      "$source_metadata" "$source" >&2
    return 1
  fi
  if [[ -e "$destination" || -L "$destination" ]]; then
    if [[ ! -f "$destination" || -L "$destination" ]]; then
      printf 'Immutable audit destination is not a physical regular file: %s\n' "$destination" >&2
      return 1
    fi
    destination_metadata="$(stat -c '%a:%h' -- "$destination")"
    if [[ "$destination_metadata" != "444:1" ]]; then
      printf 'Immutable audit destination has unsafe mode or link count (%s): %s\n' \
        "$destination_metadata" "$destination" >&2
      return 1
    fi
    if ! cmp -s -- "$source" "$destination"; then
      printf 'Immutable audit destination differs from its source: %s\n' "$destination" >&2
      return 1
    fi
    return 0
  fi
  cp --no-dereference --update=none -- "$source" "$destination"
  if [[ ! -f "$destination" || -L "$destination" ]] ||
     [[ "$(stat -c '%a:%h' -- "$destination")" != "444:1" ]] ||
     ! cmp -s -- "$source" "$destination"; then
    printf 'Immutable audit copy could not be authenticated: %s\n' "$destination" >&2
    return 1
  fi
}

populate_audit_context() {
  local destination="$1"
  mkdir -p \
    "$destination/configs" \
    "$destination/context-records" \
    "$destination/environments" \
    "$destination/logs" \
    "$destination/nextflow-reports" \
    "$destination/qdnaseq-annotation"
  cp -a "$CONTEXT_DIR/." "$destination/context-records/"
  cp -a "$LOG_DIR/." "$destination/logs/"
  cp -a "$REPORT_DIR/." "$destination/nextflow-reports/"
  cp "$CONTEXT_DIR/current-source-commit.txt" "$destination/"
  cp "$CONTEXT_DIR/current-source-sha256.txt" "$destination/"
  cp "$CONTEXT_DIR/v1.1-commit.txt" "$destination/"
  cp "$CONTEXT_DIR/v1.1-source-sha256.txt" "$destination/"
  cp "$CONTEXT_DIR/v1.1-container.txt" "$destination/"
  cp "$CONTEXT_DIR/v1.1-container-repodigests.txt" "$destination/"
  cp "$CONTEXT_DIR/nextflow-expected.txt" "$destination/"
  cp "$CONTEXT_DIR/nextflow-actual.txt" "$destination/"
  cp "$CONTEXT_DIR/samurai-expected.txt" "$destination/"
  cp "$CONTEXT_DIR/samurai-provenance.txt" "$destination/"
  cp "$CONTEXT_DIR/samurai-source-metadata.txt" "$destination/"
  cp "$CONTEXT_DIR/native-install.json" "$destination/"
  cp "$CONTEXT_DIR/native-doctor.json" "$destination/"
  cp "$RELEASE_CANDIDATE_DIR/SHA256SUMS" "$destination/native-binary-SHA256SUMS"
  cp "$RELEASE_CANDIDATE_DIR/oncotracer.provenance.json" "$destination/"
  cp "$CONTEXT_DIR/public-input-SHA256SUMS" "$destination/"
  cp "$CONTEXT_DIR/validation-reference-SHA256SUMS" "$destination/"
  cp "$CONTEXT_DIR/qdnaseq-bin-data-SHA256SUMS" "$destination/qdnaseq-annotation/"
  local qdnaseq_annotation qdnaseq_provenance
  qdnaseq_annotation="$VALIDATION_ROOT/$(<"$CONTEXT_DIR/qdnaseq-annotation-relative-path.txt")"
  qdnaseq_provenance="$(dirname -- "$qdnaseq_annotation")/QDNAseq.hg38.100kbp.SR50.rds.provenance.tsv"
  [[ "$qdnaseq_provenance" == "$qdnaseq_annotation.provenance.tsv" ]]
  copy_immutable_audit_file \
    "$qdnaseq_provenance" \
    "$destination/qdnaseq-annotation/QDNAseq.hg38.100kbp.SR50.rds.provenance.tsv"
  cp "$CONTEXT_DIR"/*-output-SHA256SUMS "$destination/"
  cp "$CONTEXT_DIR"/native-*.explicit.txt "$destination/environments/"
  cp "$CONFIG_DIR"/*.yml "$destination/configs/"
  cp "$LEDGER" "$destination/stage-ledger.tsv"
}

action_compare_quickstart1() {
  mkdir -p "$AUDIT_ROOT/quickstart1/illumina" "$AUDIT_ROOT/quickstart1/ont" "$AUDIT_ROOT/quickstart1/context"
  python3 "$REPOSITORY_ROOT/tests/compare_native_parity.py" \
    --v1 "$ANALYSIS_ROOT/v1/illumina" \
    --v2 "$ANALYSIS_ROOT/v2/illumina" \
    --outdir "$AUDIT_ROOT/quickstart1/illumina" \
    --expected-samples ERR12341627 \
    --label "QuickStart 1 / Illumina"
  python3 "$REPOSITORY_ROOT/tests/compare_native_parity.py" \
    --v1 "$ANALYSIS_ROOT/v1/ont" \
    --v2 "$ANALYSIS_ROOT/v2/ont" \
    --outdir "$AUDIT_ROOT/quickstart1/ont" \
    --expected-samples DRR165691 \
    --label "QuickStart 1 / ONT"
  assert_parity_report "$AUDIT_ROOT/quickstart1/illumina/parity_report.json"
  assert_parity_report "$AUDIT_ROOT/quickstart1/ont/parity_report.json"
  python3 - "$AUDIT_ROOT/quickstart1" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
reports = [json.loads((root / name / "parity_report.json").read_text()) for name in ("illumina", "ont")]
record = {"schema": "oncotracer-quickstart1-parity-v1", "passed": all(r["passed"] for r in reports), "reports": reports}
(root / "quickstart1_parity.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
if not record["passed"]:
    raise SystemExit("QuickStart 1 parity failed")
PY
  populate_audit_context "$AUDIT_ROOT/quickstart1/context"
  cp "$ANALYSIS_ROOT/v1/illumina/06_workflow_summary/workflow_summary.txt" "$AUDIT_ROOT/quickstart1/context/v1-illumina-summary.txt"
  cp "$ANALYSIS_ROOT/v1/ont/06_workflow_summary/workflow_summary.txt" "$AUDIT_ROOT/quickstart1/context/v1-ont-summary.txt"
  cp "$ANALYSIS_ROOT/v2/illumina/06_workflow_summary/workflow_summary.txt" "$AUDIT_ROOT/quickstart1/context/v2-illumina-summary.txt"
  cp "$ANALYSIS_ROOT/v2/ont/06_workflow_summary/workflow_summary.txt" "$AUDIT_ROOT/quickstart1/context/v2-ont-summary.txt"
  cp "$ANALYSIS_ROOT/v2/illumina/.oncotracer-native/trace.tsv" "$AUDIT_ROOT/quickstart1/context/v2-illumina-trace.tsv"
  cp "$ANALYSIS_ROOT/v2/ont/.oncotracer-native/trace.tsv" "$AUDIT_ROOT/quickstart1/context/v2-ont-trace.tsv"
  write_tree_manifest "$AUDIT_ROOT/quickstart1" "$AUDIT_ROOT/quickstart1/SHA256SUMS" "SHA256SUMS"
}

verify_compare_quickstart1() {
  assert_parity_report "$AUDIT_ROOT/quickstart1/illumina/parity_report.json"
  assert_parity_report "$AUDIT_ROOT/quickstart1/ont/parity_report.json"
  verify_tree_manifest "$AUDIT_ROOT/quickstart1" "$AUDIT_ROOT/quickstart1/SHA256SUMS"
}

run_stage \
  "compare-quickstart1" \
  "semantic comparator with fixed release thresholds for complete Illumina and ONT results; aggregate audit context and SHA256SUMS" \
  action_compare_quickstart1 verify_compare_quickstart1 \
  "$REPOSITORY_ROOT/tests/compare_native_parity.py" \
  "$CONTEXT_DIR/v1-illumina-output-SHA256SUMS" "$CONTEXT_DIR/v1-ont-output-SHA256SUMS" \
  "$CONTEXT_DIR/v2-illumina-output-SHA256SUMS" "$CONTEXT_DIR/v2-ont-output-SHA256SUMS"

action_compare_quickstart2() {
  mkdir -p "$AUDIT_ROOT/quickstart2/hcc1143" "$AUDIT_ROOT/quickstart2/context"
  python3 "$REPOSITORY_ROOT/tests/compare_native_parity.py" \
    --v1 "$ANALYSIS_ROOT/v1/hcc1143" \
    --v2 "$ANALYSIS_ROOT/v2/hcc1143" \
    --outdir "$AUDIT_ROOT/quickstart2/hcc1143" \
    --expected-samples HCC1143_DMSO,HCC1143_BEZ235,HCC1143_TRAMETINIB \
    --label "QuickStart 2 / HCC1143"
  assert_parity_report "$AUDIT_ROOT/quickstart2/hcc1143/parity_report.json"
  populate_audit_context "$AUDIT_ROOT/quickstart2/context"
  cp "$REPOSITORY_ROOT/examples/hcc1143_lpwgs/manifest.tsv" "$AUDIT_ROOT/quickstart2/context/"
  cp "$ANALYSIS_ROOT/v1/hcc1143/06_workflow_summary/workflow_summary.txt" "$AUDIT_ROOT/quickstart2/context/v1-hcc1143-summary.txt"
  cp "$ANALYSIS_ROOT/v2/hcc1143/06_workflow_summary/workflow_summary.txt" "$AUDIT_ROOT/quickstart2/context/v2-hcc1143-summary.txt"
  cp "$ANALYSIS_ROOT/v2/hcc1143/.oncotracer-native/trace.tsv" "$AUDIT_ROOT/quickstart2/context/v2-hcc1143-trace.tsv"
  write_tree_manifest "$AUDIT_ROOT/quickstart2" "$AUDIT_ROOT/quickstart2/SHA256SUMS" "SHA256SUMS"
}

verify_compare_quickstart2() {
  assert_parity_report "$AUDIT_ROOT/quickstart2/hcc1143/parity_report.json"
  verify_tree_manifest "$AUDIT_ROOT/quickstart2" "$AUDIT_ROOT/quickstart2/SHA256SUMS"
}

run_stage \
  "compare-quickstart2" \
  "semantic comparator with fixed release thresholds for complete HCC1143 results; aggregate audit context and SHA256SUMS" \
  action_compare_quickstart2 verify_compare_quickstart2 \
  "$REPOSITORY_ROOT/tests/compare_native_parity.py" \
  "$CONTEXT_DIR/v1-hcc1143-output-SHA256SUMS" "$CONTEXT_DIR/v2-hcc1143-output-SHA256SUMS"

action_bundle_audits() {
  local q1_bundle q2_bundle combined_bundle
  # Refresh the per-QuickStart evidence now that both comparison stages and
  # their immutable logs have been recorded in the ledger.
  populate_audit_context "$AUDIT_ROOT/quickstart1/context"
  populate_audit_context "$AUDIT_ROOT/quickstart2/context"
  write_tree_manifest \
    "$AUDIT_ROOT/quickstart1" "$AUDIT_ROOT/quickstart1/SHA256SUMS" "SHA256SUMS"
  write_tree_manifest \
    "$AUDIT_ROOT/quickstart2" "$AUDIT_ROOT/quickstart2/SHA256SUMS" "SHA256SUMS"
  cp "$LEDGER" "$AUDIT_ROOT/stage-ledger.tsv"
  python3 - "$AUDIT_ROOT" "$SOURCE_COMMIT" "$SOURCE_SHA256" \
    "$V1_COMMIT" "$V1_SOURCE_SHA256" "$V1_DOCKER_IMAGE" "$BINARY" \
    "$CONTEXT_DIR/nextflow-expected.txt" "$CONTEXT_DIR/nextflow-actual.txt" \
    "$CONTEXT_DIR/samurai-provenance.txt" \
    "$CONTEXT_DIR/samurai-container-identities.tsv" \
    "$CONTEXT_DIR/v1-illumina-samurai-trace-audit.json" \
    "$CONTEXT_DIR/v1-ont-samurai-trace-audit.json" \
    "$CONTEXT_DIR/v1-hcc1143-samurai-trace-audit.json" <<'PY'
import csv
import hashlib
import json
import sys
from pathlib import Path

audit = Path(sys.argv[1])
q1 = json.loads((audit / "quickstart1/quickstart1_parity.json").read_text())
q2 = json.loads((audit / "quickstart2/hcc1143/parity_report.json").read_text())
if q1.get("passed") is not True or q2.get("passed") is not True:
    raise SystemExit("cannot bundle a failed parity result")
binary = Path(sys.argv[7])
ledger_rows = (audit / "stage-ledger.tsv").read_text(encoding="utf-8").splitlines()
last_ledger = ledger_rows[-1].split("\t") if len(ledger_rows) > 1 else []

def key_values(path):
    return dict(
        line.split("=", 1)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if "=" in line
    )

record = {
    "schema": "oncotracer-v2-server-release-validation-v1",
    "passed": True,
    "source_commit": sys.argv[2],
    "source_sha256": sys.argv[3],
    "v1_commit": sys.argv[4],
    "v1_source_sha256": sys.argv[5],
    "v1_container": sys.argv[6],
    "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    "nextflow": {**key_values(sys.argv[8]), **key_values(sys.argv[9])},
    "samurai": key_values(sys.argv[10]),
    "samurai_containers": list(
        csv.DictReader(
            Path(sys.argv[11]).open(encoding="utf-8"), delimiter="\t"
        )
    ),
    "samurai_trace_audits": {
        "quickstart1_illumina": json.loads(Path(sys.argv[12]).read_text()),
        "quickstart1_ont": json.loads(Path(sys.argv[13]).read_text()),
        "quickstart2_hcc1143": json.loads(Path(sys.argv[14]).read_text()),
    },
    "source_sha256_definition": "sha256(git -c tar.umask=0002 archive --format=tar COMMIT)",
    "ledger_snapshot_last_stage": last_ledger[1] if len(last_ledger) > 4 else None,
    "ledger_snapshot_last_status": last_ledger[4] if len(last_ledger) > 4 else None,
    "quickstart1": q1,
    "quickstart2": q2,
}
(audit / "release-validation-summary.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  write_tree_manifest "$AUDIT_ROOT" "$AUDIT_ROOT/SHA256SUMS" "SHA256SUMS"
  q1_bundle="$(mktemp "$TMP_DIR/quickstart1-parity-audit.XXXXXX.tar.gz")"
  q2_bundle="$(mktemp "$TMP_DIR/quickstart2-parity-audit.XXXXXX.tar.gz")"
  combined_bundle="$(mktemp "$TMP_DIR/combined-parity-audit.XXXXXX.tar.gz")"
  (
    cd "$AUDIT_ROOT"
    tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner -cf - quickstart1 |
      gzip -n > "$q1_bundle"
    tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner -cf - quickstart2 |
      gzip -n > "$q2_bundle"
    tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner -cf - \
      quickstart1 quickstart2 release-validation-summary.json SHA256SUMS stage-ledger.tsv |
      gzip -n > "$combined_bundle"
  )
  gzip -t "$q1_bundle"
  gzip -t "$q2_bundle"
  gzip -t "$combined_bundle"
  tar -tzf "$q1_bundle" | grep -Fx 'quickstart1/SHA256SUMS'
  tar -tzf "$q2_bundle" | grep -Fx 'quickstart2/SHA256SUMS'
  tar -tzf "$combined_bundle" | grep -Fx 'release-validation-summary.json'
  mv -f -- \
    "$q1_bundle" "$BUNDLE_DIR/oncotracer-v2.0.0-quickstart1-parity-audit.tar.gz"
  mv -f -- \
    "$q2_bundle" "$BUNDLE_DIR/oncotracer-v2.0.0-quickstart2-parity-audit.tar.gz"
  mv -f -- \
    "$combined_bundle" "$BUNDLE_DIR/oncotracer-v2.0.0-parity-audit.tar.gz"
  (cd "$BUNDLE_DIR" && sha256sum ./*.tar.gz > SHA256SUMS)
}

verify_preserved_trace_bundle_context() {
  local context="$1" prefix="$2" mode="$3" expected_rows="$4"
  python3 "$REPOSITORY_ROOT/tests/verify_nested_samurai.py" \
    --verify-artifact-context "$context" \
    --artifact-prefix "$prefix" \
    --artifact-mode "$mode" \
    --artifact-expected-rows "$expected_rows"
  python3 "$REPOSITORY_ROOT/tests/parity_audit.py" verify-trace-proof \
    --raw-root "$context/${prefix}-trace-source-files" \
    --source-manifest "$context/${prefix}-trace-sources.tsv" \
    --combined-trace "$context/${prefix}-execution-trace.txt"
}

verify_bundle_audits() {
  local extracted
  assert_parity_report "$AUDIT_ROOT/quickstart1/illumina/parity_report.json"
  assert_parity_report "$AUDIT_ROOT/quickstart1/ont/parity_report.json"
  assert_parity_report "$AUDIT_ROOT/quickstart2/hcc1143/parity_report.json"
  python3 - "$AUDIT_ROOT/release-validation-summary.json" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
if record.get("passed") is not True:
    raise SystemExit("release validation summary is not passing")
if len(record.get("samurai_containers", [])) != 12:
    raise SystemExit("release validation summary does not contain 12 pinned SAMURAI images")
expected = {
    "quickstart1_illumina": 12,
    "quickstart1_ont": 10,
    "quickstart2_hcc1143": 32,
}
observed = record.get("samurai_trace_audits", {})
for name, rows in expected.items():
    if observed.get(name, {}).get("row_count") != rows:
        raise SystemExit(f"SAMURAI trace summary mismatch for {name}")
PY
  verify_tree_manifest "$AUDIT_ROOT" "$AUDIT_ROOT/SHA256SUMS"
  (cd "$BUNDLE_DIR" && sha256sum -c SHA256SUMS)
  tar -tzf "$BUNDLE_DIR/oncotracer-v2.0.0-parity-audit.tar.gz" >/dev/null
  extracted="$(mktemp -d "$TMP_DIR/bundle-verification.XXXXXX")"
  tar -xzf "$BUNDLE_DIR/oncotracer-v2.0.0-parity-audit.tar.gz" -C "$extracted"
  verify_tree_manifest "$extracted/quickstart1" "$extracted/quickstart1/SHA256SUMS"
  verify_tree_manifest "$extracted/quickstart2" "$extracted/quickstart2/SHA256SUMS"
  verify_tree_manifest "$extracted" "$extracted/SHA256SUMS"
  verify_preserved_trace_bundle_context \
    "$extracted/quickstart1/context/context-records" \
    v1-illumina-samurai illumina 12
  verify_preserved_trace_bundle_context \
    "$extracted/quickstart1/context/context-records" \
    v1-ont-samurai ont 10
  verify_preserved_trace_bundle_context \
    "$extracted/quickstart2/context/context-records" \
    v1-hcc1143-samurai illumina 32
}

run_stage \
  "bundle-release-audits" \
  "verify every parity report and unchanged threshold; create deterministic QuickStart and combined audit tarballs plus SHA256SUMS" \
  action_bundle_audits verify_bundle_audits \
  "$AUDIT_ROOT/quickstart1" "$AUDIT_ROOT/quickstart2" \
  "$RELEASE_CANDIDATE_DIR/SHA256SUMS" "$LEDGER" \
  "$REPOSITORY_ROOT/tests/verify_nested_samurai.py" \
  "$REPOSITORY_ROOT/tests/combine_nested_samurai_traces.py" \
  "$REPOSITORY_ROOT/tests/parity_audit.py"

# The generic stage runner records success only after its action and verifier.
# Repack once after that row exists so the released evidence contains the
# successful bundle-stage row and the now-complete bundle-stage log.
CURRENT_STAGE="finalize-release-audits"
action_bundle_audits
verify_bundle_audits

CURRENT_STAGE="complete"
VALIDATION_COMPLETE=true
printf 'OncoTracer v2 release validation passed.\n'
printf 'Source commit:  %s\n' "$SOURCE_COMMIT"
printf 'Source SHA-256: %s\n' "$SOURCE_SHA256"
printf 'Binary:         %s\n' "$BINARY"
printf 'Audit bundle:   %s\n' "$BUNDLE_DIR/oncotracer-v2.0.0-parity-audit.tar.gz"
printf 'Stage ledger:   %s\n' "$LEDGER"
