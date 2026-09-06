# Mock six-tumor/four-normal study

This example runs ten paired-end Illumina samples in one analysis. Every sample
is aligned, normalized, segmented, called, and reported independently by qDNAseq.
OncoTracer does **not** pool the four `CTRL` samples or subtract them from tumors.
`NORMAL` records a sample's role, not a reference-building instruction.

These **20 FASTQs are not bundled**. Use your own de-identified data, changing
the names below to match. For checksum-validated public-data examples, choose
[QuickStart 1](quick_start.md) or [QuickStart 2](public_cohort.md).

## 1. Check the installation

Follow [installation](installation.md). If you have not installed the Conda tools:

```bash
oncotracer install --conda
oncotracer doctor --backend conda
```

The ordinary OncoTracer command does not invoke Nextflow or a special study script.

## 2. Create the sample table

Replace the analysis directory, then paste the whole block through `CSV`.
`cat >` replaces the named file if it exists.

```bash
cd /path/to/my/analyses_dir/
mkdir -p "$PWD/oncotracer-onco6-ctrl4/input/fastq"
cat > "$PWD/oncotracer-onco6-ctrl4/input/samples.csv" <<'CSV'
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
```

Place exactly one matching R1/R2 pair per row in `input/fastq/`:

```text
ONCO001_R1.fastq.gz   ONCO001_R2.fastq.gz
ONCO002_R1.fastq.gz   ONCO002_R2.fastq.gz
ONCO003_R1.fastq.gz   ONCO003_R2.fastq.gz
ONCO004_R1.fastq.gz   ONCO004_R2.fastq.gz
ONCO005_R1.fastq.gz   ONCO005_R2.fastq.gz
ONCO006_R1.fastq.gz   ONCO006_R2.fastq.gz
CTRL001_R1.fastq.gz   CTRL001_R2.fastq.gz
CTRL002_R1.fastq.gz   CTRL002_R2.fastq.gz
CTRL003_R1.fastq.gz   CTRL003_R2.fastq.gz
CTRL004_R1.fastq.gz   CTRL004_R2.fastq.gz
```

Each name before `_R1`/`_R2` must match the table. Do not merge different
libraries. [Flag and filename explanations](auto_params.md).

## 3. Save and check the settings

```bash
cd /path/to/my/analyses_dir/
oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/oncotracer-onco6-ctrl4/input/fastq" \
  --sample-table "$PWD/oncotracer-onco6-ctrl4/input/samples.csv" \
  --config-dir "$PWD/oncotracer-onco6-ctrl4/config" \
  --outdir "$PWD/oncotracer-onco6-ctrl4/results" \
  --threads 4
oncotracer check --config "$PWD/oncotracer-onco6-ctrl4/config/illumina.auto.yml"
```

Expect `Selected samples: 10 (6 TUMOR, 4 NORMAL)` and all ten names in `check`.
Batch setup checks compressed reads and rejects duplicates or missing mates.
It saves `illumina.auto.yml`, `illumina.samplesheet.csv` and a checksum record,
`auto_params_manifest.tsv`. Keep these together.

To reuse genome indexes, add `--hg38_build /path/to/reference` to `auto`.
To build your own, add `--build_reference` instead. With neither flag, run
downloads prebuilt indexes. [RAM and reference choices](reference_indexes.md).

## 4. Run or resume the one native analysis

```bash
cd /path/to/my/analyses_dir/
oncotracer run --backend conda \
  --config "$PWD/oncotracer-onco6-ctrl4/config/illumina.auto.yml"
```

This analyzes all ten rows. To resume, repeat only this command. Leave the
configuration, inputs and output directory unchanged; leave `--force` off.

## 5. Confirm that every sample completed

```bash
cd /path/to/my/analyses_dir/
cat "$PWD/oncotracer-onco6-ctrl4/config/illumina.samplesheet.csv"
cat "$PWD/oncotracer-onco6-ctrl4/results/01_samurai_illumina/qdnaseq/qdnaseq_sample_status.json"
cat "$PWD/oncotracer-onco6-ctrl4/results/06_workflow_summary/workflow_summary.txt"
```

The samplesheet should retain six `tumor` and four `normal` rows. In
`qdnaseq_sample_status.json`, `completed_samples` must contain all ten names
and `failed_samples` must be empty. The summary records `engine=native` and
`nextflow_used=false`. Resolve failures before describing the cohort as complete.

Review each sample's plots under `01_samurai_illumina/qdnaseq/plots/` and the
final events in `03_cna_codification/cna_events.tsv`. A successful run does not
confirm a tumor diagnosis. [Output guide](outputs.md).
