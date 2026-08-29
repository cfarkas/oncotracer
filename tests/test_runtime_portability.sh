#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

# The Docker runtime must follow the invoking host UID:GID rather than a fixed
# account from the image or from one developer workstation. The shell
# substitutions are expanded in the generated Docker command, which keeps the
# Nextflow config compatible with the strict parser.
grep -F "System.getenv('ONCOTRACER_DOCKER_USER')" "$ROOT/nextflow.config" >/dev/null
grep -F "'\$(id -u):\$(id -g)'" "$ROOT/nextflow.config" >/dev/null
if grep -F "docker_user = '1000:1000'" "$ROOT/nextflow.config" >/dev/null; then
  echo "ERROR: nextflow.config still hard-codes Docker UID:GID 1000:1000" >&2
  exit 1
fi
if grep -E '^(def|[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=)' "$ROOT/nextflow.config" >/dev/null; then
  echo "ERROR: nextflow.config contains a top-level variable declaration rejected by the strict parser" >&2
  exit 1
fi

# Both scientific launchers and the installation check must use the pinned
# local source cache rather than Nextflow's unauthenticated GitHub API client.
grep -F 'nextflow run "$SAMURAI_SOURCE"' "$ROOT/bin/scripts/run_illumina_samurai_fastq.sh" >/dev/null
grep -F 'nextflow run "$SAMURAI_SOURCE"' "$ROOT/bin/scripts/run_ont_samurai_barcodes.sh" >/dev/null
if grep -F 'nextflow run dincalcilab/samurai' \
    "$ROOT/bin/scripts/run_illumina_samurai_fastq.sh" \
    "$ROOT/bin/scripts/run_ont_samurai_barcodes.sh" >/dev/null; then
  echo "ERROR: a launcher still runs SAMURAI through the GitHub project resolver" >&2
  exit 1
fi
if grep -F 'nextflow pull dincalcilab/samurai' "$ROOT/bin/scripts/install_oncotracer.sh" >/dev/null; then
  echo "ERROR: the installer still uses Nextflow's GitHub project resolver" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
mkdir -p "$TMP/fake-bin"
export FAKE_GIT_LOG="$TMP/git.log"

cat > "$TMP/fake-bin/git" <<'FAKE_GIT'
#!/usr/bin/env bash
set -Eeuo pipefail
printf '%q ' "$@" >> "$FAKE_GIT_LOG"
printf '\n' >> "$FAKE_GIT_LOG"

if [[ "${1:-}" == "clone" ]]; then
  destination="${!#}"
  mkdir -p "$destination/.git" "$destination/assets/ichorcna"
  cat > "$destination/main.nf" <<'NF'
dict = params.dict ? channel.fromPath(params.fai).map { it -> [[id: it.baseName], it] }.collect() : channel.empty()
NF
  printf 'test asset\n' > "$destination/assets/ichorcna/gc_hg38_500kb.wig"
  exit 0
fi

if [[ "${1:-}" == "-C" && "${3:-}" == "rev-parse" ]]; then
  printf '%s\n' '0123456789abcdef0123456789abcdef01234567'
  exit 0
fi

echo "fake git received an unsupported invocation: $*" >&2
exit 2
FAKE_GIT
chmod +x "$TMP/fake-bin/git"

PROJECT_ROOT="$TMP/project"
SOURCE_ONE="$(PATH="$TMP/fake-bin:$PATH" bash "$ROOT/bin/scripts/prepare_samurai_source.sh" \
  --lpwgs-root "$PROJECT_ROOT" \
  --revision v1.4.0)"
SOURCE_TWO="$(PATH="$TMP/fake-bin:$PATH" bash "$ROOT/bin/scripts/prepare_samurai_source.sh" \
  --lpwgs-root "$PROJECT_ROOT" \
  --revision v1.4.0)"

[[ "$SOURCE_ONE" == "$SOURCE_TWO" ]]
[[ -s "$SOURCE_ONE/main.nf" ]]
[[ -d "$SOURCE_ONE/.git" ]]
grep -F 'channel.fromPath(params.dict)' "$SOURCE_ONE/main.nf" >/dev/null
grep -qx 'cache_format=2' "$SOURCE_ONE/.oncotracer-source"
grep -qx 'revision=v1.4.0' "$SOURCE_ONE/.oncotracer-source"
grep -qx 'commit=0123456789abcdef0123456789abcdef01234567' "$SOURCE_ONE/.oncotracer-source"

CLONES="$(grep -c '^clone ' "$FAKE_GIT_LOG")"
[[ "$CLONES" -eq 1 ]] || {
  echo "ERROR: expected one cached SAMURAI clone, observed $CLONES" >&2
  cat "$FAKE_GIT_LOG" >&2
  exit 1
}

# The same CI path that protects runtime portability also executes the
# first-time-user dry-run/resume regression suite. Running it as a module keeps
# the repository root on sys.path, matching the permanent Native v2 CI job.
(
  cd "$ROOT"
  python3 -m unittest -v \
    tests/test_beginner_runtime.py \
    tests/test_install_safety.py \
    tests/test_payload_cache.py \
    tests/test_standalone_dry_run.py
)

echo "PASS: runtime portability, local SAMURAI cache, and beginner CLI tests"
