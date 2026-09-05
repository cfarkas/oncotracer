# Set up your own data

Use this page after [installing OncoTracer](installation.md). `setup` creates the YAML configuration that `run` reads. YAML is a text file of `key: value` settings; you can open it in any text editor.

Before a large download, run `oncotracer system --path /absolute/path/to/my-study`.
It reports usable CPU, available RAM, disk space and workflow limits. Low-RAM
computers can [reuse prebuilt genome indexes](reference_indexes.md).

## Let setup ask for the paths

```bash
oncotracer setup --project /absolute/path/to/my-study
```

Replace the path with a project directory you want to create. Setup asks for your sequencing platform, analysis, and input files. For methylation it also asks for the classifier and locally installed model files, and calculates their checksums for you. It does not install those optional tools or start analysis.

The folder will contain:

```text
my-study/
  config/run.yml        saved settings
  config/samplesheet.csv  created for a single Illumina library
  reference/            created when the analysis needs reference files
  results/              created when analysis starts
```

Setup refuses to overwrite an existing configuration. To change an existing project, edit its YAML; to start a different analysis, choose a new project directory.

## Understand the paths and flags

| Flag | What you supply | Example |
| --- | --- | --- |
| `--project` | Folder for the new project | `/work/my-study` |
| `--reads-folder` | Existing ONT folder containing barcode FASTQ folders | `/data/run/fastq_pass` |
| `--barcodes` | Folders to analyze, separated by commas | `barcode01,barcode02` |
| `--sample-names` | Names in the same order as the barcodes | `sampleA,sampleB` |
| `--fastq-1`, `--fastq-2` | Existing Illumina read files | `/data/sample_R1.fastq.gz` |
| `--samplesheet` | CSV linking each Illumina library to its FASTQs | `/data/illumina/samplesheet.csv` |
| `--reference-root` | Optional shared OncoTracer reference directory | `/data/oncotracer-reference` |
| `--config` | YAML saved by setup | `/work/my-study/config/run.yml` |
| `--backend` | How the installed analysis tools are provided | `conda` |
| `--threads` | CPU worker threads to request | `8` |

Use absolute paths, beginning with `/`, to make commands work from any directory. Put paths in quotes if they contain spaces. In the examples, a backslash `\` at the end of a line continues the same command on the next line. `$PWD` means your current directory.

## ONT: one barcode

Replace the example paths, barcode, and sample name with yours:

```bash
oncotracer setup --non-interactive \
  --project /work/my-study \
  --mode ont --analysis cna \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01 --sample-names sampleA \
  --threads 8
```

`--mode ont` selects Nanopore data. `--analysis cna` selects copy-number analysis. `--non-interactive` makes missing answers an error instead of asking questions. The example selects only `barcode01`; it does not combine other barcodes or unclassified reads.

For tumor and independent normal groups, use [batch setup](auto_params.md) or the [ONT settings](configuration/ont.md).

## Illumina: one library

```bash
oncotracer setup --non-interactive \
  --project /work/my-study \
  --mode illumina --analysis cna \
  --sample-name sampleA \
  --fastq-1 /data/sampleA_R1.fastq.gz \
  --fastq-2 /data/sampleA_R2.fastq.gz
```

Omit `--fastq-2` for single-end reads.

## Illumina: multiple libraries

This example analyzes **two libraries from four FASTQs**. Save the following as `/data/illumina/samplesheet.csv` in a text editor, replacing the names and paths with yours:

```csv
sample,fastq_1,fastq_2,status
sampleA,/data/illumina/sampleA_R1.fastq.gz,/data/illumina/sampleA_R2.fastq.gz,tumor
sampleB,/data/illumina/sampleB_R1.fastq.gz,/data/illumina/sampleB_R2.fastq.gz,tumor
```

Each row names one sample and its matching R1/R2 pair. Each file field takes one existing path, not a wildcard or a list. For single-end libraries, leave `fastq_2` empty in every row.

```bash
oncotracer setup --non-interactive \
  --project /work/illumina-study \
  --mode illumina --analysis cna \
  --samplesheet /data/illumina/samplesheet.csv \
  --threads 4
oncotracer check --config /work/illumina-study/config/run.yml
oncotracer run --backend conda --config /work/illumina-study/config/run.yml
```

`--samplesheet` selects the CSV; `--project` selects the new project. Both samples get separate results under `/work/illumina-study/results/`. Add another CSV row for each additional library. [Batch setup](auto_params.md) can generate the CSV from filenames.

## ONT: multiple barcodes and FASTQ batches

This example analyzes **two samples**, each split across two FASTQ files:

```text
/data/run/fastq_pass/
  barcode01/
    reads_001.fastq.gz
    reads_002.fastq.gz
  barcode02/
    reads_001.fastq.gz
    reads_002.fastq.gz
  unclassified/
    reads_001.fastq.gz
```

Point `--reads-folder` to `fastq_pass`, not to an individual barcode or FASTQ:

```bash
oncotracer setup --non-interactive \
  --project /work/ont-study \
  --mode ont --analysis cna \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01,barcode02 \
  --sample-names sampleA,sampleB \
  --threads 4
oncotracer check --config /work/ont-study/config/run.yml
oncotracer run --backend conda --config /work/ont-study/config/run.yml
```

The lists match by position: `barcode01` is `sampleA`, and `barcode02` is `sampleB`. OncoTracer combines the FASTQ batches **within each selected barcode**, never between samples. `unclassified` is excluded. Use completed FASTQ files; do not analyze files while they are being written.

Results go to `/work/ont-study/results/`. Add matching entries to both lists for more barcodes. For another run's `fastq_pass` folder, repeat setup with a different project path.

## Check, run, and read the summary

```bash
oncotracer check --config /work/my-study/config/run.yml
oncotracer run --backend conda --config /work/my-study/config/run.yml
```

Use your chosen project path above. `check` reports missing settings and paths, then displays the samples, CPU threads, and planned steps without starting analysis. `doctor --backend conda` checks the installed backend; methylation also needs the [optional tools and model files](configuration/methylation.md).

A successful run prints `OncoTracer native analysis completed:` followed by the results path. Open `results/06_workflow_summary/workflow_summary.txt` first. To resume an interrupted run, repeat the same `run` command. Leave `--force` off unless you intend to recompute completed stages.

For leukemia or CNS methylation, follow the [methylation guide](configuration/methylation.md). FASTQ alone cannot supply methylation calls.
