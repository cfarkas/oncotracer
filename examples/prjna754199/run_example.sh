#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'HELP'
Usage: bash examples/prjna754199/run_example.sh [--docker|--singularity|--conda] [--download-only|--prepare-only|--run]

--download-only  Download and validate all 12 currently public FASTQs, then stop.
--prepare-only   Download/validate the FASTQs and run Automatic Setup, then stop.
--run            Complete download, configuration, stub check, real run, and output checks (default).

Set COHORT_ROOT=/absolute/path to move downloads, configuration, references, work,
and results out of the repository's ignored test/ directory.
HELP
}

RUNTIME="--docker"
ACTION="--run"
RUNTIME_SEEN="false"
ACTION_SEEN="false"
for arg in "$@"; do
  case "$arg" in
    --docker|--singularity|--conda)
      if [[ "$RUNTIME_SEEN" == "true" && "$RUNTIME" != "$arg" ]]; then
        echo "ERROR: choose only one runtime flag" >&2
        exit 2
      fi
      RUNTIME="$arg"
      RUNTIME_SEEN="true"
      ;;
    --download-only|--prepare-only|--run)
      if [[ "$ACTION_SEEN" == "true" && "$ACTION" != "$arg" ]]; then
        echo "ERROR: choose only one action" >&2
        exit 2
      fi
      ACTION="$arg"
      ACTION_SEEN="true"
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
COHORT_ROOT="$(readlink -m "${COHORT_ROOT:-$ROOT_DIR/test}")"
READS_DIR="$COHORT_ROOT/public/prjna754199"
CONFIG_DIR="$COHORT_ROOT/configs/prjna754199"
OUTDIR="$COHORT_ROOT/runs/prjna754199"
WORK_DIR="$COHORT_ROOT/work/prjna754199"
STUB_WORK_DIR="$COHORT_ROOT/work/prjna754199_stub"
AUTO_WORK_DIR="$COHORT_ROOT/work/prjna754199_auto_params"
SAMPLESHEET="$CONFIG_DIR/illumina.samplesheet.csv"
YAML="$CONFIG_DIR/illumina.auto.yml"
RUN_PROVENANCE="$CONFIG_DIR/run_provenance.tsv"
MANIFEST="$SCRIPT_DIR/manifest.tsv"
SAMPLE_TABLE_SOURCE="$SCRIPT_DIR/samples.csv"
SAMPLE_TABLE="$READS_DIR/samples.csv"
VERIFIER="$SCRIPT_DIR/verify_outputs.py"

source "$ROOT_DIR/bin/scripts/download_helpers.sh"

for command_name in python3 curl gzip md5sum stat readlink; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $command_name" >&2
    exit 1
  }
done

# Refuse a silently truncated or edited manifest before transferring human data.
python3 - "$MANIFEST" "$SAMPLE_TABLE_SOURCE" <<'PY_VALIDATE_MANIFEST'
import csv
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
sample_table = Path(sys.argv[2])
required = {
    "sample_alias", "biosample_accession", "experiment_accession", "run_accession",
    "instrument_model", "library_layout", "read_length_bp", "read_count", "base_count",
    "archive_filename", "fastq_bytes", "fastq_md5", "https_url",
}
with manifest.open(newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))
if len(rows) != 12:
    raise SystemExit(f"ERROR: expected exactly 12 archived runs in {manifest}, found {len(rows)}")
missing = required - set(rows[0])
if missing:
    raise SystemExit(f"ERROR: manifest is missing column(s): {', '.join(sorted(missing))}")
for key in ("sample_alias", "biosample_accession", "experiment_accession", "run_accession"):
    values = [row[key] for row in rows]
    if len(values) != len(set(values)):
        raise SystemExit(f"ERROR: manifest column is not unique: {key}")
for row in rows:
    if row["library_layout"] != "SINGLE":
        raise SystemExit(f"ERROR: {row['run_accession']} is not marked SINGLE")
    if int(row["base_count"]) != int(row["read_count"]) * int(row["read_length_bp"]):
        raise SystemExit(f"ERROR: read/base count mismatch for {row['run_accession']}")
    if row["archive_filename"] != f"{row['run_accession']}.fastq.gz":
        raise SystemExit(f"ERROR: archive filename mismatch for {row['run_accession']}")
    if not row["https_url"].startswith("https://ftp.sra.ebi.ac.uk/"):
        raise SystemExit(f"ERROR: unpinned or non-HTTPS archive URL for {row['run_accession']}")
if sum(int(row["fastq_bytes"]) for row in rows) != 6_171_900_300:
    raise SystemExit("ERROR: manifest byte total does not match the pinned ENA report")
if sum(int(row["read_count"]) for row in rows) != 266_097_582:
    raise SystemExit("ERROR: manifest read total does not match the pinned ENA report")
with sample_table.open(newline="") as handle:
    metadata_rows = list(csv.DictReader(handle))
metadata_aliases = [row.get("sample_name", "") for row in metadata_rows]
manifest_aliases = [row["sample_alias"] for row in rows]
if metadata_aliases != manifest_aliases:
    raise SystemExit("ERROR: samples.csv aliases/order do not match the frozen manifest")
if any(row.get("status", "").upper() != "TUMOR" for row in metadata_rows):
    raise SystemExit("ERROR: every public patient-cohort metadata row must use TUMOR")
print("Manifest validated: 12 unique single-end runs; 6,171,900,300 compressed bytes")
PY_VALIDATE_MANIFEST

mkdir -p "$READS_DIR" "$CONFIG_DIR" "$OUTDIR" "$WORK_DIR" "$STUB_WORK_DIR" "$AUTO_WORK_DIR"

downloaded_rows=0
while IFS=$'\t' read -r sample_alias biosample experiment run instrument layout read_length reads bases archive_filename bytes md5 url; do
  [[ "$sample_alias" == "sample_alias" ]] && continue
  destination="$READS_DIR/${sample_alias}.fastq.gz"
  download_validated_fastq "$url" "$destination" "$bytes" "$md5"
  downloaded_rows=$((downloaded_rows + 1))
done < "$MANIFEST"
[[ "$downloaded_rows" -eq 12 ]] || {
  echo "ERROR: expected to process 12 manifest rows, processed $downloaded_rows" >&2
  exit 1
}

cp "$MANIFEST" "$READS_DIR/manifest.tsv"
cp "$SAMPLE_TABLE_SOURCE" "$SAMPLE_TABLE"

cat <<EOF_DOWNLOAD
Twelve validated public single-end FASTQs are ready:
  reads:       $READS_DIR
  manifest:    $MANIFEST
  metadata:    $SAMPLE_TABLE
  download:    6,171,900,300 bytes (about 5.75 GiB)
  read count:  266,097,582 reads (all archive files are 36 bp)
EOF_DOWNLOAD
[[ "$ACTION" == "--download-only" ]] && exit 0

command -v java >/dev/null 2>&1 || { echo "ERROR: Java 17+ is required for Automatic Setup." >&2; exit 1; }
command -v nextflow >/dev/null 2>&1 || { echo "ERROR: Nextflow is required for Automatic Setup." >&2; exit 1; }

cd "$ROOT_DIR"
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$READS_DIR" \
  --sample_table "$SAMPLE_TABLE" \
  --auto_config_dir "$CONFIG_DIR" \
  --auto_outdir "$OUTDIR" \
  --run_cna_classifier true \
  --cna_classifier_sample_set sarcoma \
  --cna_classifier_profile conda \
  --pathology_use_biomed_models false \
  --pathology_biomed_local_files_only true \
  -work-dir "$AUTO_WORK_DIR"

[[ -s "$SAMPLESHEET" ]] || { echo "ERROR: Automatic Setup did not create: $SAMPLESHEET" >&2; exit 1; }
[[ -s "$YAML" ]] || { echo "ERROR: Automatic Setup did not create: $YAML" >&2; exit 1; }

oncotracer_commit="unavailable"
oncotracer_tree="unavailable"
if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  oncotracer_commit="$(git -C "$ROOT_DIR" rev-parse HEAD)"
  if [[ -n "$(git -C "$ROOT_DIR" status --short)" ]]; then
    oncotracer_tree="dirty"
  else
    oncotracer_tree="clean"
  fi
fi
manifest_sha256="unavailable"
sample_table_sha256="unavailable"
if command -v sha256sum >/dev/null 2>&1; then
  manifest_sha256="$(sha256sum "$MANIFEST" | awk '{print $1}')"
  sample_table_sha256="$(sha256sum "$SAMPLE_TABLE_SOURCE" | awk '{print $1}')"
fi
{
  printf 'field\tvalue\n'
  printf 'archive_project\tPRJNA754199\n'
  printf 'archive_inventory_date\t2026-07-15\n'
  printf 'public_run_count\t12\n'
  printf 'manifest_sha256\t%s\n' "$manifest_sha256"
  printf 'sample_table_sha256\t%s\n' "$sample_table_sha256"
  printf 'oncotracer_commit\t%s\n' "$oncotracer_commit"
  printf 'oncotracer_worktree\t%s\n' "$oncotracer_tree"
  printf 'runtime_flag\t%s\n' "$RUNTIME"
  printf 'reference_build\thg38\n'
  printf 'initial_caller\tqDNAseq\n'
  printf 'initial_bin_size_kb\t100\n'
  printf 'classifier_context\tsarcoma\n'
} > "$RUN_PROVENANCE"

echo "Generated samplesheet: $SAMPLESHEET"
sed -n '1,16p' "$SAMPLESHEET"
echo "Generated YAML: $YAML"
sed -n '1,120p' "$YAML"
echo "Generated provenance receipt: $RUN_PROVENANCE"
sed -n '1,80p' "$RUN_PROVENANCE"
[[ "$ACTION" == "--prepare-only" ]] && exit 0

case "$RUNTIME" in
  --docker)
    command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker is required." >&2; exit 1; }
    docker pull carlosfarkas/oncotracer:latest
    ;;
  --singularity)
    if ! command -v singularity >/dev/null 2>&1 && ! command -v apptainer >/dev/null 2>&1; then
      echo "ERROR: Singularity or Apptainer is required." >&2
      exit 1
    fi
    ;;
  --conda)
    command -v conda >/dev/null 2>&1 || { echo "ERROR: Conda is required." >&2; exit 1; }
    ;;
esac

cd "$ROOT_DIR"
echo "Checking the 12-library single-end workflow wiring"
nextflow run main.nf -stub-run "$RUNTIME" -params-file "$YAML" \
  -work-dir "$STUB_WORK_DIR"

run_with_progress() {
  local label="$1" nested_log="$2"
  shift 2
  local started=$SECONDS
  "$@" &
  local run_pid=$!
  while kill -0 "$run_pid" 2>/dev/null; do
    sleep 30
    kill -0 "$run_pid" 2>/dev/null || break
    echo "  $label is active ($((SECONDS - started)) seconds elapsed)."
    [[ -s "$nested_log" ]] && tail -n 1 "$nested_log" || true
  done
  wait "$run_pid"
}

run_with_progress \
  "PRJNA754199 workflow" \
  "$OUTDIR/01_samurai_illumina/nextflow_launch/.nextflow.log" \
  nextflow run main.nf "$RUNTIME" -params-file "$YAML" \
    -work-dir "$WORK_DIR" -resume

python3 "$VERIFIER" --outdir "$OUTDIR"

segment_table="$OUTDIR/01_samurai_illumina/qdnaseq/all_segments.seg"
refinement_summary="$OUTDIR/02_bam_refinement/illumina_qdnaseq_100kb/01_tables/sample_refinement_summary.csv"

cat <<EOF_SUCCESS
SUCCESS: all 12 FASTQs currently exposed by PRJNA754199 completed the configured workflow.
Summary:                $OUTDIR/06_workflow_summary/workflow_summary.txt
SAMURAI segments:       $segment_table
Refinement statistics:  $refinement_summary
CNA profiles:           $OUTDIR/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf
Research interpretation:$OUTDIR/05_cna_classifier/03_report/cna_classifier_report.html
Provenance receipt:     $RUN_PROVENANCE

Resume the exact run with:
nextflow run main.nf $RUNTIME -params-file $YAML -resume
EOF_SUCCESS
