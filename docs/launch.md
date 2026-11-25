# Run from one sample table

`oncotracer launch` creates the configuration and runs the analysis in Docker.
You supply the sample table, a new project folder and the preservation method.
It detects CPU/RAM budgets, pulls the versioned image and prepares missing
references. You do not need host Conda, R, Nextflow or separate caller installs.

## Fresh Illumina

Save this as `samples.csv`, replacing the example paths:

```csv
sample,fastq_1,fastq_2,status
CASE_01,reads/case_R1.fastq.gz,reads/case_R2.fastq.gz,tumor
NORMAL_01,reads/normal_R1.fastq.gz,reads/normal_R2.fastq.gz,normal
```

```bash
oncotracer launch --samples samples.csv --project fresh-run --specimen fresh
```

Paths are relative to the sample table, or absolute. Each sample ID must be unique.
Use `tumor` or `normal` from the specimen records; names do not determine roles.
Normal samples remain separate libraries. Additional clinical columns such as
`diagnosis` and `sample_type` are retained in the generated sample metadata.
For single-end reads, leave `fastq_2`
empty throughout the table. For multiple lanes, use a quoted JSON list in each
FASTQ column, with the same number and ordering of R1/R2 files.

Full analysis includes qDNAseq CNA, the sensitive CNA checks and ichorCNA
fallback, interpretation, small variants and platform-specific imputation.
Use `--analysis cna` for CNA alone. The scientific thresholds and exclusions
remain in the saved configuration and reports.

## FFPE Illumina

Use the same table format. FFPE calling uses FFPErase and requires its license
acknowledgement. Review [the upstream terms](https://github.com/papaemmelab/nf-ffperase/blob/b0dd56cbd0a939896a966b9ce30c4d719b158170/LICENSE)
before running:

```bash
oncotracer launch --samples ffpe.csv --project ffpe-run --specimen ffpe --accept-ffperase-license
```

ANNOVAR remains optional, separately licensed software. Existing permitted
installations are detected; OncoTracer does not redistribute it. The
[variant guide](variants_reference.md) describes annotation availability and
how to supply a licensed installation explicitly.

## ONT with methylation

Save `ont.csv` with one row per library:

```csv
sample,library,status,tissue
CASE_01,ONT/library_01,tumor,bone marrow
CASE_02,ONT/library_02,tumor,CNS
NORMAL_01,ONT/control_01,normal,blood
```

Each library contains `fastq_pass/` and `pod5_pass/`. They may contain a single
matching barcode directory. A folder containing several barcodes must be split
into explicit sample rows with `fastq_dir` and `pod5` columns. Completed passed
reads define the selected read IDs; failed-read folders are excluded.

```bash
oncotracer launch --samples ont.csv --project ont-run --specimen fresh --methylation --accept-sturgeon-license
```

The acknowledgement is required only when CNS samples select Sturgeon. Review
[its license](https://github.com/UMCUGenetics/sturgeon/blob/main/LICENSE) first.
The licensed software and model are downloaded into your private cache; they are
not included in the public image. Blood/marrow samples use MARLIN. Tissue
routing uses the supplied records, never predicted diagnoses.

Rockfish requires R10.4.1 E8.2, 400 bases/s, 5 kHz POD5. The managed ONT profile
uses pinned Dorado 1.1.1, SUP v5.2.0 and the validated Rockfish checkpoint.
Other chemistries and ONT FFPE need a separately validated configuration;
launch does not treat them as equivalent.

For full ONT analysis, launch reads the basecalling model recorded in each FASTQ
header and selects its matching Clair3 model and supported ClairS-TO platform.
If those headers were removed, supply `--ont-profile` from your sequencing records
(for example, `--ont-profile r1041_e82_400bps_sup_v500`). A conflicting or
unsupported model stops before downloads; no chemistry is guessed. Mixed
basecalling protocols need separate projects.

For methylation alone, replace `--methylation` with `--analysis methylation`.
For CNA/variants without signal processing, omit both and leave out `tissue`.

### Existing modified-base BAMs

Provide `sample,fastq_dir,status,tissue,modbam` columns. The FASTQ folder still
identifies each library. Select Modkit explicitly:

```bash
oncotracer launch --samples modified-bams.csv --project modkit-run --specimen fresh --analysis methylation --methylation-caller modkit
```

The BAMs must contain valid MM/ML modified-base calls. Existing calls are
realigned to hg38 and filtered to the selected library's read IDs. FASTQ alone
cannot supply methylation measurements. MARLIN/Sturgeon prediction still
requires enough covered probes; a completed run does not imply a confident call.

## Resources and downloads

```bash
oncotracer launch --samples samples.csv --project my-run --specimen fresh --dry-run
oncotracer launch --samples samples.csv --project my-run --specimen fresh --threads 8 --memory 64GB --parallel-samples 2
```

Defaults use up to 16 available logical CPUs, with CPU affinity and available
RAM included in allocation. `--threads` and `--memory` are **total cohort
budgets**. RAM limits are scheduler budgets; enforce a hard limit separately
with your container or job scheduler when required. The number of simultaneous
samples is chosen from those budgets.

An available NVIDIA GPU is selected for methylation. Docker must have the
[NVIDIA Container Toolkit](gpu.md); use `--cpu` to select CPU execution or
`--gpu` to require GPU execution. Alignment and variant defaults remain on CPU.

`--cache /full/path/to/shared-cache` relocates references and models. The default
is `$XDG_CACHE_HOME/oncotracer/v6`, or `~/.cache/oncotracer/v6`. Keep it separate
from input and project directories. Cached assets are checked against pinned
hashes; interrupted downloads resume. Installations are published only after
all required files have been verified. A failed preparation does not start an
analysis or overwrite an existing project.

Use `--prepare-only` to prepare methylation resources and save the configuration
without starting analysis. Genome and variant references are prepared when
analysis starts. The command prints the saved YAML and the command to run it.

## Results and repeating a command

The project contains:

```text
my-run/
├── config/
│   ├── samples.csv
│   ├── sample_metadata.csv
│   ├── methylation.tsv       # ONT methylation only
│   ├── run.yml
│   └── launch.json
└── results/
```

The original sample table is unchanged. The generated table uses absolute
paths. `launch.json` records the input-table hash, chosen options and generated
file hashes. Repeat the exact launch command to resume. Changed inputs/options
require a new project. If you edit the YAML intentionally, run it directly:

```bash
oncotracer check --config my-run/config/run.yml
oncotracer run --config my-run/config/run.yml --backend docker
```

For completed or interrupted v5 results, retain the original application/image
version. Use a new result directory when validating v6 against an older run.
The [v5 release guide](releases/5.0.0.md) remains available unchanged.
