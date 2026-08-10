#!/usr/bin/env bash
# Complete GitHub Actions parity driver for OncoTracer native v2.
set -Eeuo pipefail
umask 022

usage() {
  echo "Usage: $0 quickstart1|quickstart2" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
SUITE="$1"
[[ "$SUITE" == quickstart1 || "$SUITE" == quickstart2 ]] || usage

: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${CANDIDATE_SHA:?CANDIDATE_SHA is required}"

readonly V1_1_COMMIT="032c1268fa7fdcadc48087055066d7a9fc59bd89"
readonly V1_DOCKER_IMAGE="carlosfarkas/oncotracer@sha256:4856aed020e1102f891b91de54d6acf365d6b8a57e2283a4f7b670b0bd5b07ed"
readonly SAMURAI_COMMIT="6a901940288b008237703c6b181d447e7dee4fcf"
readonly NEXTFLOW_VERSION="26.04.6"
readonly NEXTFLOW_DIST_URL="https://github.com/nextflow-io/nextflow/releases/download/v26.04.6/nextflow-26.04.6-dist"
readonly NEXTFLOW_DIST_SHA256="182a63c74074e2dc7956ffa3c8cd59de952ed2c44394e21faf5e1736b945444c"
readonly REPO="$GITHUB_WORKSPACE/v2"
readonly V1_REPO="$GITHUB_WORKSPACE/v1"
readonly TEST_ROOT="$GITHUB_WORKSPACE/parity-$SUITE"
readonly INPUT_ROOT="$TEST_ROOT/input"
readonly REPORT_ROOT="$TEST_ROOT/reports"
readonly AUDIT_ROOT="$TEST_ROOT/audit"
readonly CONTEXT="$AUDIT_ROOT/context"
readonly V2_ENV_PREFIX="/tmp/oncotracer-v2-envs-$SUITE"
readonly PAYLOAD_CACHE="/tmp/oncotracer-v2-payload-$SUITE"
readonly CONFIG_HOME="/tmp/oncotracer-v2-config-$SUITE"
readonly PINNED_NEXTFLOW_DIR="$TEST_ROOT/.release-tools/bin"
readonly NEXTFLOW="$PINNED_NEXTFLOW_DIR/nextflow"
readonly PINS="$RUNNER_TEMP/$SUITE-samurai-container-pins.tsv"
readonly PREFLIGHT="$RUNNER_TEMP/$SUITE-samurai-container-preflight.tsv"
readonly RUNTIME="$CONTEXT/nested-v1-container-runtime.tsv"
readonly SELECTION="$CONTEXT/nested-v1-trace-selection.tsv"

export XDG_CONFIG_HOME="$CONFIG_HOME"
export ONCOTRACER_PAYLOAD_CACHE="$PAYLOAD_CACHE"
export PIP_NO_CACHE_DIR=1
export NXF_ANSI_LOG=false
export NXF_DISABLE_CHECK_LATEST=true
export NXF_OPTS="-Xms256m -Xmx2g"
export NXF_GITHUB_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"

log() {
  printf '\n===== %s =====\n' "$*"
}

require_file() {
  [[ -s "$1" ]] || { echo "missing or empty file: $1" >&2; exit 1; }
}

mkdir -p "$TEST_ROOT/configs" "$REPORT_ROOT" "$CONTEXT/manifests" \
  "$CONTEXT/configs/parity" "$CONTEXT/configs/input" "$CONTEXT/qdnaseq-annotation"

log "Free runner disk and install frozen-comparator prerequisites"
sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc /opt/hostedtoolcache/CodeQL || true
docker system prune --all --force || true
sudo apt-get update
sudo apt-get install -y --no-install-recommends samtools bwa minimap2 pigz curl wget git

df -h

log "Install and authenticate Nextflow $NEXTFLOW_VERSION"
mkdir -p "$PINNED_NEXTFLOW_DIR"
curl --fail --location --retry 5 --retry-all-errors \
  --output "$RUNNER_TEMP/nextflow-$NEXTFLOW_VERSION-dist" \
  "$NEXTFLOW_DIST_URL"
printf '%s  %s\n' "$NEXTFLOW_DIST_SHA256" \
  "$RUNNER_TEMP/nextflow-$NEXTFLOW_VERSION-dist" | sha256sum --strict -c -
install -m 0755 "$RUNNER_TEMP/nextflow-$NEXTFLOW_VERSION-dist" "$NEXTFLOW"
printf '%s  %s\n' "$NEXTFLOW_DIST_SHA256" "$NEXTFLOW" | sha256sum --strict -c -
"$NEXTFLOW" -version 2>&1 | tee "$RUNNER_TEMP/nextflow-version.txt"
grep -E "version[[:space:]]+$NEXTFLOW_VERSION([[:space:]]|$)" "$RUNNER_TEMP/nextflow-version.txt"
printf 'version=%s\nurl=%s\nsha256=%s\n' \
  "$NEXTFLOW_VERSION" "$NEXTFLOW_DIST_URL" "$NEXTFLOW_DIST_SHA256" \
  > "$RUNNER_TEMP/nextflow-identity.txt"

log "Authenticate frozen v1.1 container"
docker info
docker pull "$V1_DOCKER_IMAGE"
docker image inspect "$V1_DOCKER_IMAGE" \
  --format '{{range .RepoDigests}}{{println .}}{{end}}' \
  | tee "$RUNNER_TEMP/v1-docker-repodigests.txt"
grep -Fx "$V1_DOCKER_IMAGE" "$RUNNER_TEMP/v1-docker-repodigests.txt"
printf '%s\n' "$V1_DOCKER_IMAGE" > "$RUNNER_TEMP/v1-docker-digest.txt"

log "Add addressable-memory headroom"
if [[ ! -e "/swapfile.oncotracer-$SUITE" ]]; then
  sudo fallocate -l 32G "/swapfile.oncotracer-$SUITE"
  sudo chmod 600 "/swapfile.oncotracer-$SUITE"
  sudo mkswap "/swapfile.oncotracer-$SUITE"
fi
sudo swapon "/swapfile.oncotracer-$SUITE" || true
sudo sysctl -w vm.swappiness=100 || true
free -h

log "Build copied native v2 executable and install isolated Conda backend"
conda config --set channel_priority strict
conda config --set solver libmamba
SOURCE_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
test "$SOURCE_COMMIT" = "$CANDIDATE_SHA"
SOURCE_SHA256="$(git -C "$REPO" -c tar.umask=0002 archive --format=tar "$SOURCE_COMMIT" | sha256sum | awk '{print $1}')"
printf '%s\n' "$SOURCE_COMMIT" > "$RUNNER_TEMP/v2-source-commit.txt"
printf '%s\n' "$SOURCE_SHA256" > "$RUNNER_TEMP/v2-source-sha256.txt"
python3 "$REPO/scripts/build_native_binary.py" \
  --output "$REPO/dist/oncotracer" \
  --source-commit "$SOURCE_COMMIT" \
  --source-sha256 "$SOURCE_SHA256"
chmod 0755 "$REPO/dist/oncotracer"
"$REPO/dist/oncotracer" --version
sha256sum "$REPO/dist/oncotracer" | tee "$RUNNER_TEMP/native-binary.sha256"
BINARY_SHA256="$(awk 'NR == 1 {print $1}' "$RUNNER_TEMP/native-binary.sha256")"
"$REPO/dist/oncotracer" provenance --json > "$RUNNER_TEMP/native-binary-provenance.json"
jq -e --arg commit "$SOURCE_COMMIT" --arg sha256 "$SOURCE_SHA256" --arg binary "$BINARY_SHA256" '
  .source_commit == $commit and .source_sha256 == $sha256 and
  .source_tree_dirty == false and .binary_sha256 == $binary
' "$RUNNER_TEMP/native-binary-provenance.json"

V1_BASELINE_COMMIT="$(git -C "$V1_REPO" rev-parse HEAD)"
test "$V1_BASELINE_COMMIT" = "$V1_1_COMMIT"
V1_TAG_COMMIT="$(git -C "$V1_REPO" rev-list -n 1 v1.1)"
test "$V1_TAG_COMMIT" = "$V1_1_COMMIT"
printf '%s\n' "$V1_BASELINE_COMMIT" > "$RUNNER_TEMP/v1-baseline-commit.txt"
printf '%s\n' "$V1_TAG_COMMIT" > "$RUNNER_TEMP/v1-tag-commit.txt"

"$REPO/dist/oncotracer" install --conda --prefix "$V2_ENV_PREFIX" \
  > "$RUNNER_TEMP/native-install.json" 2> "$RUNNER_TEMP/native-install.stderr.log"
jq -e '.backend == "conda"' "$RUNNER_TEMP/native-install.json"
"$REPO/dist/oncotracer" doctor --backend conda \
  > "$RUNNER_TEMP/native-doctor.json" 2> "$RUNNER_TEMP/native-doctor.stderr.log"
jq -e '.success == true' "$RUNNER_TEMP/native-doctor.json"
for environment in core qdnaseq ichorcna classifier gistic; do
  conda list --explicit --prefix "$V2_ENV_PREFIX/$environment" \
    > "$RUNNER_TEMP/native-$environment.explicit.txt"
done

log "Prepare public inputs and isolated v1/v2 configs"
if [[ "$SUITE" == quickstart1 ]]; then
  "$REPO/dist/oncotracer" quickstart 1 --test-root "$INPUT_ROOT" --download-only
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/illumina.quickstart.yml" \
    --destination "$TEST_ROOT/configs/v1-illumina.yml" \
    --lpwgs-root "$TEST_ROOT" --outdir "$TEST_ROOT/v1/illumina"
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/illumina.quickstart.yml" \
    --destination "$TEST_ROOT/configs/v2-illumina.yml" \
    --lpwgs-root "$TEST_ROOT" --outdir "$TEST_ROOT/v2/illumina"
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/ont.quickstart.yml" \
    --destination "$TEST_ROOT/configs/v1-ont.yml" \
    --lpwgs-root "$TEST_ROOT" --outdir "$TEST_ROOT/v1/ont"
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/ont.quickstart.yml" \
    --destination "$TEST_ROOT/configs/v2-ont.yml" \
    --lpwgs-root "$TEST_ROOT" --outdir "$TEST_ROOT/v2/ont"
else
  "$REPO/dist/oncotracer" quickstart 2 --test-root "$INPUT_ROOT" --download-only
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml" \
    --destination "$TEST_ROOT/configs/v1-hcc1143.yml" \
    --lpwgs-root "$TEST_ROOT" --outdir "$TEST_ROOT/v1/hcc1143"
  python3 "$REPO/tests/make_parity_config.py" \
    --source "$INPUT_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml" \
    --destination "$TEST_ROOT/configs/v2-hcc1143.yml" \
    --lpwgs-root "$TEST_ROOT" --outdir "$TEST_ROOT/v2/hcc1143"
fi

log "Pin every nested SAMURAI runtime image by immutable digest"
if [[ "$SUITE" == quickstart1 ]]; then
  cat > "$PINS" <<'EOF'
container	manifest_digest
quay.io/biocontainers/samtools:1.22.1--h96c455f_0	sha256:23dc2c29f457a448a0d341fb97b2632a2c8004925214cb6420562a5b12adf8a2
community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6	sha256:e269216786463d44f9d83a0d6e877b34bca2c7b4d35211b4b369fe98e39ef1a5
quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1	sha256:fb6135876beca3059ed1414d5082833d5bbf1fb3f0f64e51ca8b29fb47adaa75
docker.io/t0shy/qpdf-docker:11.3.0	sha256:744f00189f4b0f3f1273073212102b32e0505fea528c9516e4252b9345e482d3
community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf	sha256:677f4c8e38cfd741926e5bd1e80d96b756540bc6a9e9c5ed520aa7a98358d11d
community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2	sha256:209b7aeca568155a099873da6e830427bf0a9d5418426b39f913db736d53e20b
community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4	sha256:c6240b1bcc57de07d9a92373f6fad080870bba0075be6cd25c6d37179d928c72
quay.io/einar_rainhart/pandas-pandera:1.5.3	sha256:39fae3f3a2edb8cb174b3ffade1741b6b1ec850a323b4f7a0dca6908f2e49cf8
community.wave.seqera.io/library/polars_procps-ng_typer:d1a53d7945a021e3	sha256:3b7464a65a9b23f0969b19767303b8727ab9f7dce83b1885cd8f6334d75ed59e
community.wave.seqera.io/library/procps-ng_r-argparser_r-dplyr_r-ggplot2_pruned:10da72fa04bcba1a	sha256:28626b999449abe6ddc2167228023ec93e90d109a540be97d0a789d2093b4e8b
EOF
else
  cat > "$PINS" <<'EOF'
container	manifest_digest
quay.io/biocontainers/samtools:1.22.1--h96c455f_0	sha256:23dc2c29f457a448a0d341fb97b2632a2c8004925214cb6420562a5b12adf8a2
community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6	sha256:e269216786463d44f9d83a0d6e877b34bca2c7b4d35211b4b369fe98e39ef1a5
quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1	sha256:fb6135876beca3059ed1414d5082833d5bbf1fb3f0f64e51ca8b29fb47adaa75
docker.io/t0shy/qpdf-docker:11.3.0	sha256:744f00189f4b0f3f1273073212102b32e0505fea528c9516e4252b9345e482d3
community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf	sha256:677f4c8e38cfd741926e5bd1e80d96b756540bc6a9e9c5ed520aa7a98358d11d
EOF
fi
printf 'container\tmanifest_digest\timage_id\n' > "$PREFLIGHT"
while IFS=$'\t' read -r container digest; do
  [[ "$container" == container ]] && continue
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
  repository="${container%:*}"
  immutable="$repository@$digest"
  docker pull "$immutable"
  expected_id="$(docker image inspect "$immutable" --format '{{.Id}}')"
  if docker image inspect "$container" >/dev/null 2>&1; then
    existing_id="$(docker image inspect "$container" --format '{{.Id}}')"
    test "$existing_id" = "$expected_id" || {
      echo "mutable SAMURAI tag $container resolves to unexpected $existing_id" >&2
      exit 1
    }
  else
    docker tag "$immutable" "$container"
  fi
  observed_id="$(docker image inspect "$container" --format '{{.Id}}')"
  test "$observed_id" = "$expected_id"
  docker image inspect "$container" --format '{{range .RepoDigests}}{{println .}}{{end}}' \
    | grep -E "@${digest}$"
  printf '%s\t%s\t%s\n' "$container" "$digest" "$observed_id" >> "$PREFLIGHT"
done < "$PINS"

log "Configure nested SAMURAI tracing and comparator resources"
cat > "$TEST_ROOT/configs/v1-pinned-nextflow.config" <<EOF
process {
  withName: RUN_ILLUMINA_SAMURAI {
    env.PATH = '$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
  }
  withName: RUN_ONT_SAMURAI {
    env.PATH = '$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
  }
}
EOF

nested_config() {
  local home="$1"
  mkdir -p "$home"
  cat > "$home/config" <<'EOF'
process {
  resourceLimits = [cpus: 4, memory: '14.GB', time: '6.h']
}
conda { useMamba = false }
trace.fields = 'task_id,hash,name,status,exit,container'
EOF
}

if [[ "$SUITE" == quickstart1 ]]; then
  nested_config "$TEST_ROOT/v1/illumina/01_samurai_illumina/.nextflow"
  nested_config "$TEST_ROOT/v1/ont/01_samurai_ont/.nextflow"
  cat >> "$TEST_ROOT/v1/ont/01_samurai_ont/.nextflow/config" <<EOF
process {
  withName: ICHORCNA_RUN {
    containerOptions = '-v $REPO/bin/scripts:/opt/oncotracer/scripts:ro -v $REPO/bin/scripts/v1_ichorcna_profile.R:/.Rprofile:ro'
  }
}
EOF
else
  nested_config "$TEST_ROOT/v1/hcc1143/01_samurai_illumina/.nextflow"
fi

log "Run complete frozen v1.1 baseline"
if [[ "$SUITE" == quickstart1 ]]; then
  env PATH="$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$NEXTFLOW" run "$V1_REPO/main.nf" \
    -c "$TEST_ROOT/configs/v1-pinned-nextflow.config" --docker \
    --docker_image "$V1_DOCKER_IMAGE" \
    -params-file "$TEST_ROOT/configs/v1-illumina.yml" \
    -work-dir "$TEST_ROOT/work/v1-illumina" \
    -with-report "$REPORT_ROOT/v1-illumina.html" \
    -with-trace "$REPORT_ROOT/v1-illumina.tsv" \
    -resume 2>&1 | tee "$REPORT_ROOT/v1-illumina.log"
  env PATH="$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$NEXTFLOW" run "$V1_REPO/main.nf" \
    -c "$TEST_ROOT/configs/v1-pinned-nextflow.config" --docker \
    --docker_image "$V1_DOCKER_IMAGE" \
    -params-file "$TEST_ROOT/configs/v1-ont.yml" \
    -work-dir "$TEST_ROOT/work/v1-ont" \
    -with-report "$REPORT_ROOT/v1-ont.html" \
    -with-trace "$REPORT_ROOT/v1-ont.tsv" \
    -resume 2>&1 | tee "$REPORT_ROOT/v1-ont.log"
else
  env PATH="$PINNED_NEXTFLOW_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$NEXTFLOW" run "$V1_REPO/main.nf" \
    -c "$TEST_ROOT/configs/v1-pinned-nextflow.config" --docker \
    --docker_image "$V1_DOCKER_IMAGE" \
    -params-file "$TEST_ROOT/configs/v1-hcc1143.yml" \
    -work-dir "$TEST_ROOT/work/v1-hcc1143" \
    -with-report "$REPORT_ROOT/v1-hcc1143.html" \
    -with-trace "$REPORT_ROOT/v1-hcc1143.tsv" \
    -resume 2>&1 | tee "$REPORT_ROOT/v1-hcc1143.log"
fi

SAMURAI_SOURCE="$TEST_ROOT/.oncotracer/samurai/v1.4.0"
require_file "$SAMURAI_SOURCE/.oncotracer-source"
test "$(git -C "$SAMURAI_SOURCE" rev-parse HEAD)" = "$SAMURAI_COMMIT"
grep -Fx 'revision=v1.4.0' "$SAMURAI_SOURCE/.oncotracer-source"
grep -Fx "commit=$SAMURAI_COMMIT" "$SAMURAI_SOURCE/.oncotracer-source"
cp "$SAMURAI_SOURCE/.oncotracer-source" "$RUNNER_TEMP/samurai.oncotracer-source"

log "Select and authenticate completed nested SAMURAI traces"
if [[ "$SUITE" == quickstart1 ]]; then
  python3 "$REPO/tests/verify_nested_samurai.py" \
    --suite quickstart1 --pins "$PINS" --runtime-out "$RUNTIME" \
    --selected-dir "$CONTEXT" --selection-out "$SELECTION" \
    --illumina-root "$TEST_ROOT/v1/illumina/01_samurai_illumina" \
    --ont-root "$TEST_ROOT/v1/ont/01_samurai_ont"
else
  python3 "$REPO/tests/verify_nested_samurai.py" \
    --suite quickstart2 --pins "$PINS" --runtime-out "$RUNTIME" \
    --selected-dir "$CONTEXT" --selection-out "$SELECTION" \
    --hcc-root "$TEST_ROOT/v1/hcc1143/01_samurai_illumina"
fi

log "Run complete copied native v2 executable"
if [[ "$SUITE" == quickstart1 ]]; then
  "$REPO/dist/oncotracer" run --backend conda \
    --config "$TEST_ROOT/configs/v2-illumina.yml" --threads 4 \
    2>&1 | tee "$REPORT_ROOT/v2-illumina.log"
  "$REPO/dist/oncotracer" run --backend conda \
    --config "$TEST_ROOT/configs/v2-ont.yml" --threads 4 \
    2>&1 | tee "$REPORT_ROOT/v2-ont.log"
else
  "$REPO/dist/oncotracer" run --backend conda \
    --config "$TEST_ROOT/configs/v2-hcc1143.yml" --threads 4 \
    2>&1 | tee "$REPORT_ROOT/v2-hcc1143.log"
fi

log "Produce semantic parity reports"
if [[ "$SUITE" == quickstart1 ]]; then
  python3 "$REPO/tests/compare_native_parity.py" \
    --v1 "$TEST_ROOT/v1/illumina" --v2 "$TEST_ROOT/v2/illumina" \
    --outdir "$AUDIT_ROOT/illumina" --label "QuickStart 1 / Illumina" \
    --expected-samples ERR12341627
  python3 "$REPO/tests/compare_native_parity.py" \
    --v1 "$TEST_ROOT/v1/ont" --v2 "$TEST_ROOT/v2/ont" \
    --outdir "$AUDIT_ROOT/ont" --label "QuickStart 1 / ONT" \
    --expected-samples DRR165691
  python3 - "$AUDIT_ROOT" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
reports = [json.loads((root / name / 'parity_report.json').read_text()) for name in ('illumina', 'ont')]
overall = {
    'schema': 'oncotracer-quickstart1-parity-v1',
    'passed': all(report.get('passed') is True for report in reports),
    'reports': reports,
}
(root / 'quickstart1_parity.json').write_text(json.dumps(overall, indent=2, sort_keys=True) + '\n')
if not overall['passed']:
    raise SystemExit('QuickStart 1 parity failed')
PY
  mapfile -t native_markers < <(find "$TEST_ROOT/v2/ont" -type f \
    -name '*.ichorcna_plot_compat.tsv' -print | sort)
  test "${#native_markers[@]}" -eq 1
  cp "${native_markers[0]}" "$CONTEXT/v2-ont-ichorcna-plot-compat.tsv"
else
  python3 "$REPO/tests/compare_native_parity.py" \
    --v1 "$TEST_ROOT/v1/hcc1143" --v2 "$TEST_ROOT/v2/hcc1143" \
    --outdir "$AUDIT_ROOT" --label "QuickStart 2 / HCC1143" \
    --expected-samples HCC1143_DMSO,HCC1143_BEZ235,HCC1143_TRAMETINIB
fi

log "Collect immutable audit context and complete tree manifests"
printf '%s\n' "$CANDIDATE_SHA" > "$CONTEXT/workflow-event-sha.txt"
printf '%s\n' "$CANDIDATE_SHA" > "$CONTEXT/validated-candidate-sha.txt"
cp "$RUNNER_TEMP/v2-source-commit.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v2-source-sha256.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v1-baseline-commit.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v1-tag-commit.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v1-docker-digest.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/v1-docker-repodigests.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/nextflow-version.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/nextflow-identity.txt" "$CONTEXT/"
cp "$RUNNER_TEMP/samurai.oncotracer-source" "$CONTEXT/"
cp "$RUNNER_TEMP/native-install.json" "$CONTEXT/"
cp "$RUNNER_TEMP/native-install.stderr.log" "$CONTEXT/"
cp "$RUNNER_TEMP/native-doctor.json" "$CONTEXT/"
cp "$RUNNER_TEMP/native-doctor.stderr.log" "$CONTEXT/"
cp "$RUNNER_TEMP/native-binary.sha256" "$CONTEXT/"
cp "$RUNNER_TEMP/native-binary-provenance.json" "$CONTEXT/"
cp "$RUNNER_TEMP"/native-*.explicit.txt "$CONTEXT/"
cp "$PINS" "$CONTEXT/nested-v1-container-pins.tsv"
cp "$PREFLIGHT" "$CONTEXT/nested-v1-container-preflight.tsv"
cp "$TEST_ROOT/configs/v1-pinned-nextflow.config" "$CONTEXT/"

cp "$TEST_ROOT/configs"/*.yml "$CONTEXT/configs/parity/"
if [[ "$SUITE" == quickstart1 ]]; then
  cp "$INPUT_ROOT/configs"/*.yml "$CONTEXT/configs/input/"
  cp "$TEST_ROOT/v1/illumina/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v1-illumina-summary.txt"
  cp "$TEST_ROOT/v2/illumina/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v2-illumina-summary.txt"
  cp "$TEST_ROOT/v1/ont/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v1-ont-summary.txt"
  cp "$TEST_ROOT/v2/ont/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v2-ont-summary.txt"
  cp "$REPORT_ROOT/v1-illumina.tsv" "$CONTEXT/v1-illumina-trace.tsv"
  cp "$REPORT_ROOT/v1-ont.tsv" "$CONTEXT/v1-ont-trace.tsv"
  cp "$TEST_ROOT/v2/illumina/.oncotracer-native/trace.tsv" "$CONTEXT/v2-illumina-trace.tsv"
  cp "$TEST_ROOT/v2/ont/.oncotracer-native/trace.tsv" "$CONTEXT/v2-ont-trace.tsv"
  cp "$TEST_ROOT/v1/illumina/01_samurai_illumina/.nextflow/config" "$CONTEXT/nested-v1-illumina-nextflow.config"
  cp "$TEST_ROOT/v1/ont/01_samurai_ont/.nextflow/config" "$CONTEXT/nested-v1-ont-nextflow.config"
  cp "$TEST_ROOT/v1/illumina/01_samurai_illumina/nextflow_launch/.nextflow.log" "$CONTEXT/nested-v1-illumina-nextflow.log"
  cp "$TEST_ROOT/v1/ont/01_samurai_ont/nextflow_launch/.nextflow.log" "$CONTEXT/nested-v1-ont-nextflow.log"
  sha256sum "$REPO/bin/scripts/v1_ichorcna_profile.R" > "$CONTEXT/v1-ichorcna-profile.sha256"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$INPUT_ROOT/public" "$CONTEXT/manifests/public-input-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v1/illumina" "$CONTEXT/manifests/v1-illumina-output-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v2/illumina" "$CONTEXT/manifests/v2-illumina-output-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v1/ont" "$CONTEXT/manifests/v1-ont-output-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v2/ont" "$CONTEXT/manifests/v2-ont-output-manifest.tsv"
else
  cp "$INPUT_ROOT/configs/hcc1143_lpwgs"/* "$CONTEXT/configs/input/"
  cp "$INPUT_ROOT/public/hcc1143_lpwgs/samples.csv" "$CONTEXT/"
  cp "$REPO/examples/hcc1143_lpwgs/manifest.tsv" "$CONTEXT/"
  cp "$TEST_ROOT/v1/hcc1143/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v1-summary.txt"
  cp "$TEST_ROOT/v2/hcc1143/06_workflow_summary/workflow_summary.txt" "$CONTEXT/v2-summary.txt"
  cp "$REPORT_ROOT/v1-hcc1143.tsv" "$CONTEXT/v1-trace.tsv"
  cp "$TEST_ROOT/v2/hcc1143/.oncotracer-native/trace.tsv" "$CONTEXT/v2-trace.tsv"
  cp "$TEST_ROOT/v1/hcc1143/01_samurai_illumina/.nextflow/config" "$CONTEXT/nested-v1-hcc1143-nextflow.config"
  cp "$TEST_ROOT/v1/hcc1143/01_samurai_illumina/nextflow_launch/.nextflow.log" "$CONTEXT/nested-v1-hcc1143-nextflow.log"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$INPUT_ROOT/public/hcc1143_lpwgs" "$CONTEXT/manifests/public-input-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v1/hcc1143" "$CONTEXT/manifests/v1-hcc1143-output-manifest.tsv"
  python3 "$REPO/tests/parity_audit.py" manifest \
    "$TEST_ROOT/v2/hcc1143" "$CONTEXT/manifests/v2-hcc1143-output-manifest.tsv"
fi

python3 "$REPO/tests/parity_audit.py" manifest \
  "$TEST_ROOT/references/samurai_hg38" "$CONTEXT/manifests/shared-reference-manifest.tsv"
cp -a "$TEST_ROOT/.oncotracer/qdnaseq-bin-data/." "$CONTEXT/qdnaseq-annotation/"
python3 "$REPO/tests/parity_audit.py" manifest \
  "$CONTEXT/qdnaseq-annotation" "$CONTEXT/manifests/qdnaseq-annotation-manifest.tsv"

log "Enforce audit contract, finalize checksums, and verify from artifact shape"
python3 "$REPO/tests/parity_audit.py" verify \
  --suite "$SUITE" --audit "$AUDIT_ROOT" \
  --candidate-sha "$CANDIDATE_SHA" --source-sha256 "$SOURCE_SHA256" \
  --binary-sha256 "$BINARY_SHA256" --skip-checksums --write-summary
python3 "$REPO/tests/parity_audit.py" checksums "$AUDIT_ROOT"
python3 "$REPO/tests/parity_audit.py" verify \
  --suite "$SUITE" --audit "$AUDIT_ROOT" \
  --candidate-sha "$CANDIDATE_SHA" --source-sha256 "$SOURCE_SHA256" \
  --binary-sha256 "$BINARY_SHA256"

log "$SUITE parity and audit completed"
