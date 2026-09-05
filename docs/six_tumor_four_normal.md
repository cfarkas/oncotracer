# Mock Six-Tumor/Four-Normal Study

This native v2 example runs ten paired-end Illumina samples in one analysis:
`ONCO001`–`ONCO006` are marked `TUMOR`, and `CTRL001`–`CTRL004` are marked
`NORMAL`. Every sample is aligned, normalized, segmented, called, and reported
independently by qDNAseq. OncoTracer does **not** pool the four `CTRL` samples,
does not construct a sample-derived reference from them, and does not subtract
their signal from the `ONCO` samples.

!!! note "Mock inputs"
    These 20 FASTQs are not bundled. Use de-identified paired-end files that you
    are permitted to analyze. This setup example does not replace the
    checksum-validated public-data [QuickStart 1](quick_start.md),
    [QuickStart 2](public_cohort.md), or stable-release parity gates.

Every command below uses OncoTracer's native engine and does not invoke Nextflow.

## Expected cohort behavior

| Recorded status | Samples | Native behavior |
| --- | --- | --- |
| `TUMOR` | `ONCO001`–`ONCO006` | Each sample receives its own qDNAseq bins, segments, calls, plot, and sample-status record |
| `NORMAL` | `CTRL001`–`CTRL004` | Each sample receives the same independent qDNAseq analysis; the status is retained in the input samplesheet |

`NORMAL` is sample metadata; it is not permission to turn these four libraries
into a panel. Confirm the biological status and suitability of every sample
before interpreting results.

## 1. Verify the installed executable and backend

Follow [Installation](installation.md), then prepare and inspect the five
isolated Conda environments:

```bash
# Verify the installed executable, source identity, and scientific backend.
oncotracer --version
oncotracer provenance --json
oncotracer install --conda
oncotracer doctor --backend conda
```

## 2. Create the project and sample table

Start from the directory where analysis data may be written:

```bash
cd /path/to/my/analyses_dir/

PROJECT_DIR="$PWD/oncotracer-onco6-ctrl4"
mkdir -p "$PROJECT_DIR/input/fastq" "$PROJECT_DIR/config" "$PROJECT_DIR/results"

cat > "$PROJECT_DIR/input/samples.csv" <<'CSV'
sample_name,status
ONCO001,TUMOR
ONCO002,TUMOR
ONCO003,TUMOR
ONCO004,TUMOR
ONCO005,TUMOR
ONCO006,TUMOR
CTRL001,NORMAL
CTRL002,NORMAL
CTRL003,NORMAL
CTRL004,NORMAL
CSV
cat "$PROJECT_DIR/input/samples.csv"
```

Automatic Setup expects exactly one R1/R2 pair for each row:

```text
ONCO001_R1.fastq.gz       ONCO001_R2.fastq.gz
ONCO002_R1.fastq.gz       ONCO002_R2.fastq.gz
ONCO003_R1.fastq.gz       ONCO003_R2.fastq.gz
ONCO004_R1.fastq.gz       ONCO004_R2.fastq.gz
ONCO005_R1.fastq.gz       ONCO005_R2.fastq.gz
ONCO006_R1.fastq.gz       ONCO006_R2.fastq.gz
CTRL001_R1.fastq.gz       CTRL001_R2.fastq.gz
CTRL002_R1.fastq.gz       CTRL002_R2.fastq.gz
CTRL003_R1.fastq.gz       CTRL003_R2.fastq.gz
CTRL004_R1.fastq.gz       CTRL004_R2.fastq.gz
```

Place them in `$PROJECT_DIR/input/fastq`. Each identifier must match the
filename before `_R1` and `_R2`, then validate all inputs:

```bash
cd /path/to/my/analyses_dir/

PROJECT_DIR="$PWD/oncotracer-onco6-ctrl4"
SAMPLES=(ONCO001 ONCO002 ONCO003 ONCO004 ONCO005 ONCO006 CTRL001 CTRL002 CTRL003 CTRL004)
for sample in "${SAMPLES[@]}"; do
  for read in R1 R2; do
    fastq="$PROJECT_DIR/input/fastq/${sample}_${read}.fastq.gz"
    test -s "$fastq"
    gzip -t "$fastq"
  done
done
printf 'Validated %s paired-end samples.\n' "${#SAMPLES[@]}"
```

## 3. Generate and inspect the native YAML

The option names below are the exact native v2 parser interface:

```bash
cd /path/to/my/analyses_dir/

PROJECT_DIR="$PWD/oncotracer-onco6-ctrl4"
oncotracer auto \
  --mode illumina \
  --reads-folder "$PROJECT_DIR/input/fastq" \
  --sample-table "$PROJECT_DIR/input/samples.csv" \
  --config-dir "$PROJECT_DIR/config" \
  --outdir "$PROJECT_DIR/results"

test -s "$PROJECT_DIR/config/illumina.auto.yml"
test -s "$PROJECT_DIR/config/illumina.samplesheet.csv"
test -s "$PROJECT_DIR/config/auto_params_manifest.tsv"
test "$(grep -c ',tumor$' "$PROJECT_DIR/config/illumina.samplesheet.csv")" -eq 6
test "$(grep -c ',normal$' "$PROJECT_DIR/config/illumina.samplesheet.csv")" -eq 4
awk -F '\t' 'NR == 2 { exit !($1 == "illumina" && $2 == 6 && $3 == 4) }' \
  "$PROJECT_DIR/config/auto_params_manifest.tsv"

if grep -Eiq 'panel|pool.*normal|normal.*reference' \
  "$PROJECT_DIR/config/illumina.auto.yml"; then
  echo "ERROR: generated configuration requests a local normal panel" >&2
  exit 1
fi

sed -n '1,140p' "$PROJECT_DIR/config/illumina.auto.yml"
cat "$PROJECT_DIR/config/auto_params_manifest.tsv"
```

Automatic Setup rejects duplicate or invalid sample identifiers, missing
mates, and corrupt gzip streams. It publishes generated
files only after validation succeeds. The manifest records SHA-256 values for
the YAML and samplesheet; archive all three files. One, four, or any other
number of `NORMAL` rows does not activate a local panel.

## 4. Run or resume the one native analysis

```bash
cd /path/to/my/analyses_dir/

PROJECT_DIR="$PWD/oncotracer-onco6-ctrl4"
oncotracer run \
  --backend conda \
  --config "$PROJECT_DIR/config/illumina.auto.yml" \
  --threads 16
```

That single command analyzes all ten rows. Repeat it to reuse valid
content-matched stages. Add `--force` only to deliberately invalidate reusable
work. Do not delete `.oncotracer-native/state.json`, reference indexes, or
active outputs to resume. Docker and Singularity use the same command with the
matching installed backend; see [Execution backends](containers.md).

## 5. Audit independent outputs for all ten samples

```bash
cd /path/to/my/analyses_dir/

PROJECT_DIR="$PWD/oncotracer-onco6-ctrl4"
OUT="$PROJECT_DIR/results"
QDNA="$OUT/01_samurai_illumina/qdnaseq"
SUMMARY="$OUT/06_workflow_summary/workflow_summary.json"
TRACE="$OUT/.oncotracer-native/trace.tsv"

test -s "$QDNA/qdnaseq_sample_status.json"
test -s "$QDNA/qdnaseq_sample_roles.tsv"
test -s "$QDNA/all_segments.seg"
test -s "$QDNA/all_calls.seg"
test -s "$OUT/03_cna_codification/cna_events.tsv"
test -s "$OUT/06_workflow_summary/native_run_manifest.json"
test -s "$TRACE"

python3 - "$QDNA/qdnaseq_sample_status.json" "$QDNA/qdnaseq_sample_roles.tsv" "$SUMMARY" <<'PY'
import csv
import json
import sys

expected = {
    "ONCO001", "ONCO002", "ONCO003", "ONCO004", "ONCO005", "ONCO006",
    "CTRL001", "CTRL002", "CTRL003", "CTRL004",
}
with open(sys.argv[1], encoding="utf-8") as handle:
    qdnaseq = json.load(handle)
assert qdnaseq["overall_status"] == "complete"
assert set(qdnaseq["completed_samples"]) == expected
assert qdnaseq["failed_samples"] == []

with open(sys.argv[2], encoding="utf-8", newline="") as handle:
    roles = {row["sample"]: row["status"] for row in csv.DictReader(handle, delimiter="\t")}
assert set(roles) == expected
assert {sample for sample, role in roles.items() if role == "normal"} == {
    "CTRL001", "CTRL002", "CTRL003", "CTRL004",
}

with open(sys.argv[3], encoding="utf-8") as handle:
    summary = json.load(handle)
assert summary["engine"] == "native"
assert summary["nextflow_used"] is False
assert summary["mode"] == "illumina"
assert summary["sample_derived_panel_used"] is False
assert set(summary["normal_samples"]) == {"CTRL001", "CTRL002", "CTRL003", "CTRL004"}
PY

SAMPLES=(ONCO001 ONCO002 ONCO003 ONCO004 ONCO005 ONCO006 CTRL001 CTRL002 CTRL003 CTRL004)
for sample in "${SAMPLES[@]}"; do
  test -s "$QDNA/bins/${sample}_markdup_bins.bed"
  test -s "$QDNA/segments/${sample}_.seg"
  test -s "$QDNA/segments/${sample}.calls.seg"
  test -s "$QDNA/plots/${sample}_markdup_segment_plot.pdf"
done

if grep -Eiq '(^|[[:space:]/])nextflow([[:space:]]|$)|panel|pool.*normal' "$TRACE"; then
  echo "ERROR: native trace contains a forbidden workflow or local-panel command" >&2
  exit 1
fi

cat "$PROJECT_DIR/config/illumina.samplesheet.csv"
cat "$QDNA/qdnaseq_sample_roles.tsv"
cat "$QDNA/qdnaseq_sample_status.json"
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

The generated samplesheet is the preserved status record: it must still show
six `tumor` rows and four `normal` rows. The qDNAseq status record and per-sample
files prove that every `CTRL` and every `ONCO` sample was analyzed independently.

This is a research-use example, not a diagnostic protocol. Preserve input
checksums, the generated samplesheet, native trace, run manifest, exact source and
reference identities, environment specifications, and primary stage-02/03
tables with any interpretation.
