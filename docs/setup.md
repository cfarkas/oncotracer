# Set up your own data

[Install OncoTracer](installation.md), then choose **Illumina or ONT** below.
`setup` is interactive by default: it asks for missing answers and saves
`config/run.yml`, a text file of `key: value` settings. No `--interactive` flag is needed.

Replace example paths with your own absolute paths. At prompts, type paths without
quotes; in shell commands, quote paths containing spaces. Press Enter to accept a
default in brackets. Flags prefill answers and skip their prompts.

## 1. Configure interactively (recommended)

Check your computer before a large download:

```bash
oncotracer system --path /work/my-study
```

### Illumina

Choose a new project folder:

```bash
oncotracer setup --project /work/illumina-study --mode illumina --threads 4
```

Example conversation for one paired-end library:

```text
Analysis (--analysis; cna=copy-number) (cna/methylation/both) [cna]: cna
Sample name (--sample-name): sampleA
Read 1 FASTQ (--fastq-1): /data/illumina/sampleA_R1.fastq.gz
Read 2 FASTQ (--fastq-2; Enter for single-end): /data/illumina/sampleA_R2.fastq.gz
```

For single-end reads, press Enter at the Read 2 prompt. Illumina supports `cna`
(copy-number analysis). For multiple libraries, use the samplesheet example below.

Saved settings: `/work/illumina-study/config/run.yml`.
Setup also creates `config/samplesheet.csv` linking this sample to its FASTQs.

### ONT

```bash
oncotracer setup --project /work/ont-study --mode ont --threads 4
```

Example answers for two samples:

```text
Analysis (--analysis; cna=copy-number) (cna/methylation/both) [cna]: cna
FASTQ parent folder (--reads-folder): /data/run/fastq_pass
FASTQ folder: /data/run/fastq_pass
Available folders: barcode01, barcode02, unclassified
Barcode folders to include, comma separated (--barcodes): barcode01,barcode02
Sample names in the same order (--sample-names) [barcode01,barcode02]: sampleA,sampleB
```

Use the parent containing barcode folders. `barcode01` becomes `sampleA` and
`barcode02` becomes `sampleB`; Enter at the names prompt keeps barcode names.
All completed FASTQ batches within each selected barcode are combined per sample.
The example excludes `unclassified`. For one barcode, enter one barcode and one name.

Saved settings: `/work/ont-study/config/run.yml`.
For methylation or `both`, follow the [methylation guide](configuration/methylation.md);
FASTQ alone cannot supply methylation calls.

The examples request four CPU workers. Threads, backend and reference choice use
flags/defaults; setup does not prompt for them. Without `--threads`, the default is
8. Omit `--mode` to also choose the platform interactively.

## 2. Check and run your platform

Setup saves settings without starting analysis or downloading genomes.
`check` validates paths and displays samples, resources and planned steps.
Resolve its errors before running.

### Illumina

```bash
oncotracer check --config /work/illumina-study/config/run.yml
oncotracer run --backend conda --config /work/illumina-study/config/run.yml
```

### ONT

```bash
oncotracer check --config /work/ont-study/config/run.yml
oncotracer run --backend conda --config /work/ont-study/config/run.yml
```

`--config` selects your saved settings; `--backend conda` selects installed tools.
For [container installations](containers.md), use `--backend docker` or
`--backend singularity`.

Results go under your project's `results/`. A successful run prints
`OncoTracer native analysis completed:`. Open
`results/06_workflow_summary/workflow_summary.txt` first. Repeat the same `run`
command to resume; leave `--force` off.

To change settings, edit `config/run.yml` and run `check` again. Setup never
overwrites an existing configuration. For a new project, adding `--run` to setup
validates, prepares missing backend tools and starts immediately; repeating
`setup --project PATH --run` resumes its saved settings.

## Optional: reuse prepared genome indexes

No prepared reference is needed for the examples above: `run` downloads prebuilt
hg38 indexes into `PROJECT/reference/` automatically (about 8.0 GiB for Illumina,
9.7 GiB for ONT). Completed downloads are reused.

Suppose a prepared OncoTracer reference is in `/data/shared-reference`, with
files under `/data/shared-reference/references/samurai_hg38/`. For a **new** project,
use the matching command, then answer the same prompts as above:

### Illumina with an existing reference

```bash
oncotracer setup --project /work/illumina-reuse --mode illumina --threads 4 \
  --hg38_build /data/shared-reference
```

### ONT with an existing reference

```bash
oncotracer setup --project /work/ont-reuse --mode ont --threads 4 \
  --hg38_build /data/shared-reference
```

Use that new project's `config/run.yml` for check/run. The path must contain
OncoTracer's prepared genome, indexes and manifests; a FASTA alone is insufficient.
You may also supply its `references/samurai_hg38` folder. Illumina needs BWA indexes;
ONT needs minimap2. Sharing between platforms requires both.

| Setup option | What run does |
| --- | --- |
| No reference flag | Downloads prebuilt indexes automatically |
| `--hg38_build` without a path | Same automatic download |
| `--hg38_build /data/shared-reference` | Reuses that prepared reference |
| `--build_reference` | Builds missing indexes locally on CPU; needs more RAM, disk and time |

To build locally, replace `--hg38_build /data/shared-reference` with
`--build_reference`. Choose one option. See [reference details](reference_indexes.md)
for preparing a shared reference or changing an existing project's reference path.

## Optional: scripted setup without prompts

`--non-interactive` disables questions, uses flags and defaults, and errors when a
required answer is missing. It is useful in scripts. Omit it for interactive setup.

### Illumina

```bash
oncotracer setup --non-interactive \
  --project /work/illumina-scripted --mode illumina --analysis cna \
  --sample-name sampleA \
  --fastq-1 /data/illumina/sampleA_R1.fastq.gz \
  --fastq-2 /data/illumina/sampleA_R2.fastq.gz --threads 4
```

Omit `--fastq-2` for single-end reads in this non-interactive command.

### ONT

```bash
oncotracer setup --non-interactive \
  --project /work/ont-scripted --mode ont --analysis cna \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01,barcode02 --sample-names sampleA,sampleB --threads 4
```

Check and run using the scripted project's `config/run.yml`.

## Illumina: multiple libraries

Replace paths and names, then paste this block. Each row is one library. `cat >`
overwrites the named CSV; choose a new filename if it already exists.

```bash
mkdir -p "/data/illumina"
cat > "/data/illumina/samplesheet.csv" <<'CSV'
sample,fastq_1,fastq_2,status
sampleA,"/data/illumina/sampleA_R1.fastq.gz","/data/illumina/sampleA_R2.fastq.gz",tumor
sampleB,"/data/illumina/sampleB_R1.fastq.gz","/data/illumina/sampleB_R2.fastq.gz",tumor
CSV
```

Each file field is one existing path; leave `fastq_2` empty for single-end libraries.
The flags below supply every answer:

```bash
oncotracer setup --project /work/illumina-batch --mode illumina --analysis cna \
  --samplesheet /data/illumina/samplesheet.csv --threads 4
oncotracer check --config /work/illumina-batch/config/run.yml
oncotracer run --backend conda --config /work/illumina-batch/config/run.yml
```

Results stay separate per sample.
[Batch setup](auto_params.md) can generate CSVs from filenames and configure ONT
tumor/normal groups.
