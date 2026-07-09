# OncoTracer

**Reproducible LP-WGS CNA analysis for ONT and Illumina FASTQ data.**

OncoTracer is a Nextflow research workflow for copy-number alteration analysis. It starts from FASTQ files, runs the appropriate upstream CNA route, refines CNA boundaries, and writes tables, plots, reports, and optional pathology concordance outputs.

!!! warning "Research workflow"
    OncoTracer is not a standalone diagnostic system. Use outputs as research evidence that requires expert review and validation.

## Start Here

1. Install Nextflow and choose Docker, Singularity/Apptainer, or Conda.
2. For the public examples, follow the [Beginner Tutorial](tutorial_new_users.md).
3. For your own data, create a YAML with the built-in config agent or copy one file from `params/`.
4. Run `nextflow run main.nf --docker -params-file params/my_config.yml -resume`.

## Three Supported Entry Points

| I have... | Start with | Run example |
| --- | --- | --- |
| Illumina paired-end LP-WGS FASTQ files | [`params/illumina.example.yml`](https://github.com/cfarkas/oncotracer/blob/main/params/illumina.example.yml) | [Quick Start: Illumina](quick_start.md#1-illumina-fastq-files) |
| ONT `fastq_pass` barcode FASTQ files | [`params/ont.example.yml`](https://github.com/cfarkas/oncotracer/blob/main/params/ont.example.yml) | [Quick Start: ONT](quick_start.md) |
| Illumina FASTQ files plus pathology CSV | [`params/illumina.pathology.example.yml`](https://github.com/cfarkas/oncotracer/blob/main/params/illumina.pathology.example.yml) | [Quick Start: Pathology](quick_start.md) |

## What You Get

- Refined CNA event tables.
- Cytogenomic notation tables.
- Per-sample CNA plots and cohort overview plots.
- Workflow summary files for reproducibility.
- Optional classifier reports and pathology concordance summaries.

## Public Example Data

Use [Example Data](example_data.md) for verified ENA FASTQ accessions and copy-paste download commands.

## Documentation Map

- [Installation](installation.md): Docker, Singularity/Apptainer, and Conda.
- [Beginner Tutorial](tutorial_new_users.md): clone the repository, download data, generate YAMLs, and run Illumina plus ONT examples.
- [Configuration](configuration.md): every YAML field explained, including the YAML agent.
- [Inputs](inputs.md): FASTQ and pathology input formats.
- [Running OncoTracer](running.md): runtime flags and resume behavior.
- [Outputs](outputs.md): important result files.
- [Troubleshooting](troubleshooting.md): common setup and runtime issues.
