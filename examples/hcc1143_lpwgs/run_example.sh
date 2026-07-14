#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'HELP'
Usage: bash examples/hcc1143_lpwgs/run_example.sh [--docker|--singularity|--conda] [--download-only|--prepare-only|--run]

--download-only  Download and validate the six FASTQs, then stop.
--prepare-only   Download data and generate/inspect YAML and samplesheet, then stop.
--run            Complete download, configuration, stub wiring check, real run, and output checks (default).
HELP
}

RUNTIME="--docker"
ACTION="--run"
for arg in "$@"; do
  case "$arg" in
    --docker|--singularity|--conda) RUNTIME="$arg" ;;
    --download-only|--prepare-only|--run) ACTION="$arg" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
COHORT_ROOT="${COHORT_ROOT:-$ROOT_DIR/test}"
READS_DIR="$COHORT_ROOT/public/hcc1143_lpwgs"
CONFIG_DIR="$COHORT_ROOT/configs/hcc1143_lpwgs"
OUTDIR="$COHORT_ROOT/runs/hcc1143_lpwgs"
TABLE="$READS_DIR/samples.csv"
YAML="$CONFIG_DIR/illumina.auto.yml"
source "$ROOT_DIR/bin/scripts/download_helpers.sh"

cd "$ROOT_DIR"
command -v java >/dev/null || { echo "ERROR: Java 17+ is required." >&2; exit 1; }
command -v nextflow >/dev/null || { echo "ERROR: Nextflow is required." >&2; exit 1; }
if [[ "$RUNTIME" == "--docker" ]]; then
  command -v docker >/dev/null || { echo "ERROR: Docker is required." >&2; exit 1; }
  docker pull carlosfarkas/oncotracer:latest
fi
mkdir -p "$READS_DIR" "$CONFIG_DIR" "$OUTDIR"

while IFS=$'\t' read -r sample treatment run read filename bytes md5 url; do
  [[ "$sample" == "sample_name" ]] && continue
  download_validated_fastq "$url" "$READS_DIR/$filename" "$bytes" "$md5"
done < "$SCRIPT_DIR/manifest.tsv"
cp "$SCRIPT_DIR/samples.csv" "$TABLE"

cat <<EOF_SUMMARY
Six validated public FASTQs are ready:
  reads:     $READS_DIR
  metadata:  $TABLE
  download:  1,158,812,143 bytes (about 1.08 GiB)
EOF_SUMMARY
[[ "$ACTION" == "--download-only" ]] && exit 0

nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$READS_DIR" \
  --sample_table "$TABLE" \
  --auto_config_dir "$CONFIG_DIR" \
  --auto_outdir "$OUTDIR"

echo "Generated YAML: $YAML"
sed -n '1,120p' "$YAML"
echo "Generated samplesheet: $CONFIG_DIR/illumina.samplesheet.csv"
sed -n '1,20p' "$CONFIG_DIR/illumina.samplesheet.csv"
[[ "$ACTION" == "--prepare-only" ]] && exit 0

nextflow run main.nf -stub-run "$RUNTIME" -params-file "$YAML"
nextflow run main.nf "$RUNTIME" -params-file "$YAML" -resume

BAM_COUNT="$(find "$OUTDIR/01_samurai_illumina/alignment" -maxdepth 1 -type f -name '*.bam' | wc -l)"
[[ "$BAM_COUNT" -eq 3 ]] || { echo "ERROR: expected 3 BAMs, found $BAM_COUNT" >&2; exit 1; }
for sample in HCC1143_DMSO HCC1143_BEZ235 HCC1143_TRAMETINIB; do
  grep -q "$sample" "$OUTDIR/01_samurai_illumina/qdnaseq/all_segments.seg" || {
    echo "ERROR: $sample is missing from all_segments.seg" >&2; exit 1;
  }
done
for expected in \
  "$OUTDIR/06_workflow_summary/workflow_summary.txt" \
  "$OUTDIR/03_cna_codification/cna_events.tsv" \
  "$OUTDIR/04_cna_custom_plots/cna_per_sample_pages.pdf" \
  "$OUTDIR/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf"; do
  [[ -s "$expected" ]] || { echo "ERROR: expected output missing: $expected" >&2; exit 1; }
done

cat <<EOF_SUCCESS
SUCCESS: the three-sample, six-FASTQ HCC1143 cohort completed.
Summary: $OUTDIR/06_workflow_summary/workflow_summary.txt
Gallery PDF: $OUTDIR/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf
Resume command:
nextflow run main.nf $RUNTIME -params-file $YAML -resume
EOF_SUCCESS
