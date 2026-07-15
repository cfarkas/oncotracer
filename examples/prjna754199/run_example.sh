#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'HELP'
Usage: bash examples/prjna754199/run_example.sh [--docker|--singularity|--conda] [--download-only|--prepare-only|--run]

--download-only  Download and validate all 12 currently public FASTQs, then stop.
--prepare-only   Download/validate the FASTQs and write the samplesheet and YAML, then stop.
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
SAMPLESHEET="$CONFIG_DIR/illumina.single_end.samplesheet.csv"
YAML="$CONFIG_DIR/illumina.full_tutorial.yml"
RUN_PROVENANCE="$CONFIG_DIR/run_provenance.tsv"
MANIFEST="$SCRIPT_DIR/manifest.tsv"

source "$ROOT_DIR/bin/scripts/download_helpers.sh"

for command_name in python3 curl gzip md5sum stat readlink; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $command_name" >&2
    exit 1
  }
done

# Refuse a silently truncated or edited manifest before transferring human data.
python3 - "$MANIFEST" <<'PY_VALIDATE_MANIFEST'
import csv
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
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
print("Manifest validated: 12 unique single-end runs; 6,171,900,300 compressed bytes")
PY_VALIDATE_MANIFEST

mkdir -p "$READS_DIR" "$CONFIG_DIR" "$OUTDIR" "$WORK_DIR" "$STUB_WORK_DIR"

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

cat <<EOF_DOWNLOAD
Twelve validated public single-end FASTQs are ready:
  reads:       $READS_DIR
  manifest:    $MANIFEST
  download:    6,171,900,300 bytes (about 5.75 GiB)
  read count:  266,097,582 reads (all archive files are 36 bp)
EOF_DOWNLOAD
[[ "$ACTION" == "--download-only" ]] && exit 0

# Use Python's CSV writer so an unusual COHORT_ROOT remains a valid samplesheet.
# JSON strings are valid YAML scalars, so paths with spaces are quoted safely.
python3 - "$MANIFEST" "$READS_DIR" "$SAMPLESHEET" "$YAML" "$COHORT_ROOT" "$OUTDIR" <<'PY_CONFIG'
import csv
import json
import sys
from pathlib import Path

manifest_path, reads_dir, samplesheet_path, yaml_path, cohort_root, outdir = map(Path, sys.argv[1:])
with manifest_path.open(newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))

with samplesheet_path.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["sample", "fastq_1", "fastq_2", "status"])
    writer.writeheader()
    for row in rows:
        fastq = (reads_dir / f"{row['sample_alias']}.fastq.gz").resolve()
        if not fastq.is_file() or fastq.stat().st_size != int(row["fastq_bytes"]):
            raise SystemExit(f"ERROR: validated FASTQ is missing or has the wrong size: {fastq}")
        # `tumor` is the SAMURAI condition group for this patient cohort. It does not
        # assert detectable ctDNA, active tumor, or a diagnosis at that time point.
        writer.writerow({
            "sample": row["sample_alias"],
            "fastq_1": str(fastq),
            "fastq_2": "",
            "status": "tumor",
        })

quote = lambda value: json.dumps(str(value.resolve()))
yaml_path.write_text(
    "\n".join([
        "mode: illumina",
        f"lpwgs_root: {quote(cohort_root)}",
        f"outdir: {quote(outdir)}",
        f"illumina_samplesheet: {quote(samplesheet_path)}",
        "illumina_analysis_type: solid_biopsy",
        "illumina_caller: qdnaseq",
        "illumina_binsize_kb: 100",
        "run_cna_classifier: true",
        "cna_classifier_sample_set: sarcoma",
        "cna_classifier_profile: conda",
        "pathology_csv: null",
        "pathology_use_biomed_models: false",
        "pathology_biomed_local_files_only: true",
        "force: false",
        "",
    ]),
    encoding="utf-8",
)
print(f"Generated single-end samplesheet: {samplesheet_path}")
print(f"Generated run YAML: {yaml_path}")
PY_CONFIG

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
if command -v sha256sum >/dev/null 2>&1; then
  manifest_sha256="$(sha256sum "$MANIFEST" | awk '{print $1}')"
fi
{
  printf 'field\tvalue\n'
  printf 'archive_project\tPRJNA754199\n'
  printf 'archive_inventory_date\t2026-07-15\n'
  printf 'public_run_count\t12\n'
  printf 'manifest_sha256\t%s\n' "$manifest_sha256"
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

command -v java >/dev/null 2>&1 || { echo "ERROR: Java 17+ is required." >&2; exit 1; }
command -v nextflow >/dev/null 2>&1 || { echo "ERROR: Nextflow is required." >&2; exit 1; }
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

alignment_dir="$OUTDIR/01_samurai_illumina/alignment"
segment_table="$OUTDIR/01_samurai_illumina/qdnaseq/all_segments.seg"
refinement_summary="$OUTDIR/02_bam_refinement/illumina_qdnaseq_100kb/01_tables/sample_refinement_summary.csv"
classification_table="$OUTDIR/05_cna_classifier/02_classification/cna_patient_classification.tsv"

bam_count="$(find "$alignment_dir" -maxdepth 1 -type f -name '*.bam' | wc -l | tr -d ' ')"
[[ "$bam_count" -eq 12 ]] || { echo "ERROR: expected 12 BAMs, found $bam_count" >&2; exit 1; }
while IFS=$'\t' read -r sample_alias rest; do
  [[ "$sample_alias" == "sample_alias" ]] && continue
  grep -Fq "$sample_alias" "$segment_table" || {
    echo "ERROR: $sample_alias is absent from the SAMURAI qDNAseq segment table" >&2
    exit 1
  }
done < "$MANIFEST"

for expected in \
  "$segment_table" \
  "$refinement_summary" \
  "$OUTDIR/02_bam_refinement/illumina_qdnaseq_100kb/04_final_results/final_segments.tsv" \
  "$OUTDIR/03_cna_codification/cna_events.tsv" \
  "$OUTDIR/03_cna_codification/cna_cytogenomic_notation.tsv" \
  "$OUTDIR/04_cna_custom_plots/cna_per_sample_pages.pdf" \
  "$OUTDIR/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf" \
  "$classification_table" \
  "$OUTDIR/05_cna_classifier/03_report/cna_classifier_report.html" \
  "$OUTDIR/06_workflow_summary/workflow_summary.txt"; do
  [[ -s "$expected" ]] || { echo "ERROR: expected output missing or empty: $expected" >&2; exit 1; }
done

python3 - "$MANIFEST" "$OUTDIR" <<'PY_VERIFY_SAMPLES'
import csv
import sys
from pathlib import Path

manifest, outdir = Path(sys.argv[1]), Path(sys.argv[2])
with manifest.open(newline="") as handle:
    expected = {
        row["sample_alias"]
        for row in csv.DictReader(handle, delimiter="\t")
    }

def require_exact(label, observed):
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing or unexpected:
        raise SystemExit(
            f"ERROR: {label} sample mismatch; "
            f"missing={missing}; unexpected={unexpected}"
        )
    print(f"VERIFIED: {label} contains all {len(observed)} manifest samples")

bam_dir = outdir / "01_samurai_illumina" / "alignment"
require_exact("BAM outputs", {path.stem for path in bam_dir.glob("*.bam")})

segments = (
    outdir / "01_samurai_illumina" / "qdnaseq" / "all_segments.seg"
)
with segments.open(newline="") as handle:
    segment_ids = {
        row["ID"]
        for row in csv.DictReader(handle, delimiter="\t")
    }
segment_samples = set()
for value in segment_ids:
    matches = [
        sample
        for sample in expected
        if value == sample or value.startswith(sample + "_")
    ]
    if not matches:
        raise SystemExit(f"ERROR: unrecognized SAMURAI segment ID: {value}")
    segment_samples.add(max(matches, key=len))
require_exact("SAMURAI segments", segment_samples)

refinement = (
    outdir / "02_bam_refinement" / "illumina_qdnaseq_100kb"
    / "01_tables" / "sample_refinement_summary.csv"
)
with refinement.open(newline="") as handle:
    require_exact(
        "refinement summary",
        {row["sample"] for row in csv.DictReader(handle)},
    )

classification = (
    outdir / "05_cna_classifier" / "02_classification"
    / "cna_patient_classification.tsv"
)
with classification.open(newline="") as handle:
    require_exact(
        "classifier table",
        {
            row["sample"]
            for row in csv.DictReader(handle, delimiter="\t")
        },
    )
PY_VERIFY_SAMPLES

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
