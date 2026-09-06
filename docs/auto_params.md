# Batch setup from FASTQs and a sample table

`oncotracer auto` connects sample names to FASTQ files and saves a configuration
(a YAML text file). It does not start the scientific analysis. Use it for many
libraries or barcodes; for one sample or methylation, start with [setup](setup.md).

Follow [installation](installation.md) first. These examples use Conda and your
own completed FASTQs; example reads are not bundled. For downloadable data, use
[QuickStart 2](public_cohort.md) or the [full tutorial](full_tutorial.md).

Choose **one** example below. Work from your analysis directory. `$PWD` means
that directory. Replace sample names and paths before pasting. Each `cat` block
creates its CSV: include the final `CSV` line. `cat >` replaces that file if it
exists. See [copying commands](command_basics.md) if this is new to you.

## Illumina: multiple libraries

This example is **four libraries, eight FASTQs**. Place the following files in
`project/input/fastq/` (or change `--reads-folder` to their existing folder):

```text
TUMOR_01_R1.fastq.gz     TUMOR_01_R2.fastq.gz
TUMOR_02_R1.fastq.gz     TUMOR_02_R2.fastq.gz
CONTROL_01_R1.fastq.gz   CONTROL_01_R2.fastq.gz
CONTROL_02_R1.fastq.gz   CONTROL_02_R2.fastq.gz
```

### 1. Create the sample table

```bash
mkdir -p "$PWD/project/input/fastq"
cat > "$PWD/project/input/samples.csv" <<'CSV'
sample_name,status
TUMOR_01,TUMOR
TUMOR_02,TUMOR
CONTROL_01,NORMAL
CONTROL_02,NORMAL
CSV
```

`sample_name` matches the filename before `_R1`/`_R2`. `status` records `TUMOR`
or `NORMAL`; remove unused rows or add one per library. For single-end data,
use one file named `<sample_name>.fastq.gz` per row. Do not mix layouts.
Each library needs one R1/R2 pair, not multiple lane pairs. For split lanes,
prepare a single pair per library before using this route; never merge different
biological samples. For unusual filenames use an [explicit samplesheet](setup.md#illumina-multiple-libraries).

### 2. Save the settings

```bash
oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/project/input/fastq" \
  --sample-table "$PWD/project/input/samples.csv" \
  --config-dir "$PWD/project/config" \
  --outdir "$PWD/project/results" \
  --threads 4
```

Expect `Selected samples: 4 (2 TUMOR, 2 NORMAL)` after gzip validation. Inspect
`project/config/illumina.samplesheet.csv`: each row must link the correct R1/R2
pair. Ambiguous or missing files are errors, not silently combined libraries.

### 3. Check, then run

```bash
oncotracer check --config "$PWD/project/config/illumina.auto.yml"
oncotracer run --backend conda \
  --config "$PWD/project/config/illumina.auto.yml"
```

Continue only when `check` succeeds and lists the four intended samples.
Normal rows are ordinary, independently analyzed qDNAseq samples. OncoTracer
does not pool them or subtract them from tumors; the generated YAML contains
no local-panel settings.

## ONT: multiple barcodes and FASTQ batches

This separate example is **two samples**, with all FASTQ batches for each sample
inside its barcode folder:

```text
ont-project/input/fastq_pass/
  barcode01/
    reads_001.fastq.gz
    reads_002.fastq.gz
  barcode02/
    reads_001.fastq.gz
    reads_002.fastq.gz
  unclassified/
    reads_001.fastq.gz
```

### 1. Create the barcode table

```bash
mkdir -p "$PWD/ont-project/input/fastq_pass"
cat > "$PWD/ont-project/input/samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,PATIENT_A,TUMOR
barcode02,PATIENT_B,TUMOR
CSV
```

`barcode` must match the folder name exactly. `sample_name` is the label in your
results. One row selects one barcode; unlisted folders such as `unclassified`
are excluded. All batches **within** a selected barcode are combined, never
across samples. Use files that are no longer being written by sequencing.

### 2. Save the settings

```bash
oncotracer auto \
  --mode ont \
  --reads-folder "$PWD/ont-project/input/fastq_pass" \
  --sample-table "$PWD/ont-project/input/samples.csv" \
  --config-dir "$PWD/ont-project/config" \
  --outdir "$PWD/ont-project/results" \
  --threads 4
```

Expect `Selected samples: 2 (2 TUMOR, 0 NORMAL)`. Point `--reads-folder` to
`fastq_pass`, not an individual barcode. The table must name barcodes explicitly;
OncoTracer does not guess identities from folder order.

### 3. Check, then run

```bash
oncotracer check --config "$PWD/ont-project/config/ont.auto.yml"
oncotracer run --backend conda \
  --config "$PWD/ont-project/config/ont.auto.yml"
```

Check must list `PATIENT_A` and `PATIENT_B`. This tumor-only table selects the
liquid-biopsy ichorCNA preset (500 kb). A table containing NORMAL rows selects
the solid-biopsy qDNAseq preset (100 kb), analyzing all samples independently.
Review [ONT settings](configuration/ont.md) if that preset does not fit your study.
FASTQ alone cannot support [methylation classification](configuration/methylation.md).

## What the flags mean

| Flag | What you supply |
| --- | --- |
| `--reads-folder` | Existing folder of reads; files are not moved |
| `--sample-table` | CSV you created above; only listed samples are selected |
| `--config-dir` | Folder for the generated YAML, mapping and checksum record |
| `--outdir` | Results folder, created when analysis starts |
| `--threads` | CPU worker threads; 4 in these examples |

To reuse prepared hg38 indexes, add `--hg38_build /path/to/reference` to `auto`.
Without a path, or with this option omitted, `run` downloads prebuilt indexes
into `CONFIG_DIR/reference/`. To build your own instead, add `--build_reference`;
this takes more RAM, disk and time. The two options cannot be combined. Neither
`auto` nor `check` downloads or builds a genome. [Reference guide](reference_indexes.md).

For optional copy-number reports, add `--run-cna-classifier` to the initial
`auto` command. `--cna-classifier-sample-set sarcoma` selects a known study context;
it does not establish a diagnosis. Add `--no-pathology-models` to disable optional
biomedical model downloads. Web/LLM enrichment is off unless you opt in through
[report settings](llm_reports.md).

## Read results or resume

Start with `results/06_workflow_summary/workflow_summary.txt`, then the sample
plots and `03_cna_codification/cna_events.tsv`. [Output guide](outputs.md).

To resume, repeat the same `run` command, **not** `auto`. Existing generated
files are protected from overwrite. Edit the YAML to change settings; use new
configuration and result folders for a different analysis. `run --dry-run`
previews analysis steps; `auto --dry-run` only previews configuration generation.
