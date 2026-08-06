# Mock Six-Tumor/Four-Normal Study

This native v2 example uses the installed `oncotracer` executable to build a run-local qDNAseq panel from four `NORMAL` samples and apply it to six `TUMOR` samples. `ONCO001`–`ONCO006` and `CTRL001`–`CTRL004` are placeholders for a paired-end Illumina cohort.

!!! note "Mock inputs"
    These 20 FASTQs are not bundled. Use de-identified paired-end files that you are permitted to analyze. This setup example does not replace the checksum-validated public-data [QuickStart 1](quick_start.md), [QuickStart 2](public_cohort.md), or stable-release parity gates.

The historical launcher is linked only from [Legacy v1.1](legacy_v1.md). Every command below uses native v2 and does not invoke Nextflow.

## Expected cohort behavior

| Role | Samples | Native behavior |
| --- | --- | --- |
| Tumor | `ONCO001`–`ONCO006` | Corrected CNA bins, segments, events, and plots are exported downstream |
| Normal | `CTRL001`–`CTRL004` | Build the local reference and remain panel/QC inputs |

Four controls are a small run-specific reference. Review their leave-one-out stability and other QC before interpreting tumor CNA calls.

## 1. Verify the installed executable and backend

Follow [Installation](installation.md), then prepare and inspect the five isolated Conda environments:

```bash
# Verify the installed executable, source identity, and scientific backend.
oncotracer --version
oncotracer provenance --json
oncotracer install --conda
oncotracer doctor --backend conda
```

## 2. Create the project and sample table

Run the blocks from one working directory, or export `PROJECT_DIR` as an absolute path:

```bash
# Create a project without requiring a source checkout.
PROJECT_DIR="${PROJECT_DIR:-$PWD/oncotracer-onco6-ctrl4}"
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

Place them in `$PROJECT_DIR/input/fastq`. Each identifier must match the filename before `_R1` and `_R2`:

```bash
# Require 20 non-empty, complete gzip files.
PROJECT_DIR="${PROJECT_DIR:-$PWD/oncotracer-onco6-ctrl4}"
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
# Validate inputs and atomically publish YAML, samplesheet, and manifest.
PROJECT_DIR="${PROJECT_DIR:-$PWD/oncotracer-onco6-ctrl4}"
oncotracer auto --mode illumina --reads-folder "$PROJECT_DIR/input/fastq" --sample-table "$PROJECT_DIR/input/samples.csv" --config-dir "$PROJECT_DIR/config" --outdir "$PROJECT_DIR/results"

test -s "$PROJECT_DIR/config/illumina.auto.yml"
test -s "$PROJECT_DIR/config/illumina.samplesheet.csv"
test -s "$PROJECT_DIR/config/auto_params_manifest.tsv"
test "$(grep -c ',tumor$' "$PROJECT_DIR/config/illumina.samplesheet.csv")" -eq 6
test "$(grep -c ',normal$' "$PROJECT_DIR/config/illumina.samplesheet.csv")" -eq 4
awk -F '\t' 'NR == 2 { exit !($1 == "illumina" && $2 == 6 && $3 == 4) }' "$PROJECT_DIR/config/auto_params_manifest.tsv"
sed -n '1,140p' "$PROJECT_DIR/config/illumina.auto.yml"
cat "$PROJECT_DIR/config/auto_params_manifest.tsv"
```

The generated YAML records:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: "CTRL001,CTRL002,CTRL003,CTRL004"
illumina_pon_min_normals: 4
illumina_pon_name: CTRL001_CTRL002_CTRL003_CTRL004_PoN
illumina_pon_min_mapq: 37
```

Automatic Setup rejects a single normal, duplicate or invalid sample identifiers, missing mates, corrupt gzip streams, and normal-only cohorts. It publishes generated files only after validation succeeds. The manifest records SHA-256 values for the YAML and samplesheet; archive all three files.

## 4. Run or resume the native analysis

```bash
# Run all native Illumina stages with the prepared Conda backend.
PROJECT_DIR="${PROJECT_DIR:-$PWD/oncotracer-onco6-ctrl4}"
oncotracer run --backend conda --config "$PROJECT_DIR/config/illumina.auto.yml" --threads 16
```

Repeat that command to reuse valid content-matched stages. Add `--force` only to deliberately invalidate reusable work. Do not delete `.oncotracer-native/state.json`, reference indexes, or active outputs to resume. Docker and Singularity use the same command with the matching installed backend; see [Execution backends](containers.md).

## 5. Audit the panel and native outputs

```bash
# Require panel completion, native identity, and principal result files.
PROJECT_DIR="${PROJECT_DIR:-$PWD/oncotracer-onco6-ctrl4}"
OUT="$PROJECT_DIR/results"
PON="$OUT/01_samurai_illumina/qdnaseq_local_pon"
SUMMARY="$OUT/06_workflow_summary/workflow_summary.json"
TRACE="$OUT/.oncotracer-native/trace.tsv"

test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = QDNASEQ_LOCAL_PON_SUCCESS
test -s "$PON/pon/normal_panel_manifest.tsv"
test -s "$PON/qc/normal_panel_sample_qc.tsv"
test -s "$PON/all_tumors.qdnaseq_pon_corrected_bins.tsv"
test -s "$OUT/03_cna_codification/cna_events.tsv"
test -s "$OUT/06_workflow_summary/native_run_manifest.json"
test -s "$TRACE"

python3 - "$SUMMARY" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    summary = json.load(handle)
assert summary["engine"] == "native"
assert summary["nextflow_used"] is False
assert summary["mode"] == "illumina"
PY

if grep -Eiq '(^|[[:space:]/])nextflow([[:space:]]|$)' "$TRACE"; then
  echo "ERROR: native trace contains a Nextflow command" >&2
  exit 1
fi

sed -n '1,10p' "$PON/pon/normal_panel_manifest.tsv"
sed -n '1,10p' "$PON/qc/normal_panel_sample_qc.tsv"
find "$PON/bins" -maxdepth 1 -type f -name '*_markdup_bins.bed' -printf '%f\n' | sort
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

The normal manifest and QC must contain `CTRL001` through `CTRL004`. Corrected downstream outputs must contain `ONCO001` through `ONCO006`, while controls remain reference/QC inputs.

This is a research-use example, not a diagnostic protocol. Preserve input checksums, the generated contract, native trace, run manifest, exact source and reference identities, environment specifications, and primary stage-02/03 tables with any interpretation.
