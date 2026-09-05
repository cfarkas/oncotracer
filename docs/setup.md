# Set up your own data

Use this page after [installing current source](installation.md#current-source-for-the-new-setup-workflow). `setup` creates the same YAML configuration that `run` reads. YAML is a text file of `key: value` settings; you can open it in any text editor.

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
| `--config` | YAML saved by setup | `/work/my-study/config/run.yml` |
| `--backend` | How the installed analysis tools are provided | `conda` |
| `--threads` | CPU worker threads to request | `8` |

Use absolute paths, beginning with `/`, to make commands work from any directory. Put paths in quotes if they contain spaces. In the examples, a backslash `\` at the end of a line continues the same command on the next line. `$PWD` means your current directory.

## Supply answers directly: ONT copy-number analysis

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

For multiple barcodes, pass comma-separated values, such as `--barcodes barcode01,barcode02 --sample-names sampleA,sampleB`. For tumor and independent normal groups, use [batch setup](auto_params.md) or the [ONT settings](configuration/ont.md).

## Supply answers directly: one Illumina library

```bash
oncotracer setup --non-interactive \
  --project /work/my-study \
  --mode illumina --analysis cna \
  --sample-name sampleA \
  --fastq-1 /data/sampleA_R1.fastq.gz \
  --fastq-2 /data/sampleA_R2.fastq.gz
```

Omit `--fastq-2` for single-end reads. For multiple libraries, use `--samplesheet /data/samplesheet.csv` with the [four-column Illumina format](inputs.md), or use [batch setup](auto_params.md) to generate it from filenames.

## Check, run, and read the summary

```bash
oncotracer check --config /work/my-study/config/run.yml
oncotracer run --backend conda --config /work/my-study/config/run.yml
```

`check` reports missing settings and paths, then displays the selected samples, CPU threads, and planned steps. It does not download data or run the scientific tools. `doctor --backend conda` checks the installed backend; methylation also needs the [optional tools and model files](configuration/methylation.md).

A successful run prints `OncoTracer native analysis completed:` followed by the results path. Open `results/06_workflow_summary/workflow_summary.txt` first. To resume an interrupted run, repeat the same `run` command. Leave `--force` off unless you intend to recompute completed stages.

For leukemia or CNS methylation, follow the [methylation guide](configuration/methylation.md). FASTQ alone cannot supply methylation calls.
