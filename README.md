# OncoTracer

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://cfarkas.github.io/oncotracer/)
[![Docker](https://img.shields.io/badge/docker-carlosfarkas%2Foncotracer-blue)](https://hub.docker.com/r/carlosfarkas/oncotracer)
[![Nextflow](https://img.shields.io/badge/nextflow-%E2%89%A525.04-green)](https://www.nextflow.io/)
[![License](https://img.shields.io/badge/license-see%20repository-lightgrey)](#license)
[![GitHub release](https://img.shields.io/github/v/release/cfarkas/oncotracer?display_name=tag)](https://github.com/cfarkas/oncotracer/releases)

**OncoTracer: reproducible LP-WGS CNA analysis for ONT and Illumina data**

**Full documentation: https://cfarkas.github.io/oncotracer/**

OncoTracer is a Nextflow research workflow for copy-number alteration analysis from low-pass whole-genome sequencing FASTQ files. It is not a standalone diagnostic system.

```text
FASTQ files -> SAMURAI/qDNAseq or SAMURAI/ichorCNA -> boundary refinement
            -> CNA event tables -> cytogenomic notation -> plots/reports
```

## Getting Started

Install Nextflow, choose a container runtime, copy one YAML file from `params/`, edit the paths, and run one command.

### 1. Illumina FASTQ files

```bash
cp params/illumina.example.yml params/my_illumina.yml
nano params/my_illumina.yml
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume
```

### 2. ONT fastq_pass / barcode FASTQ files

```bash
cp params/ont.example.yml params/my_ont.yml
nano params/my_ont.yml
nextflow run main.nf --docker -params-file params/my_ont.yml -resume
```

Use `--singularity` instead of `--docker` on HPC systems:

```bash
nextflow run main.nf --singularity -params-file params/my_ont.yml -resume
```

### 3. Illumina FASTQ files + pathology reports

```bash
cp params/illumina.pathology.example.yml params/my_illumina_pathology.yml
nano params/my_illumina_pathology.yml
nextflow run main.nf --docker -params-file params/my_illumina_pathology.yml -resume
```

New to YAML? It is just a text settings file: `setting_name: value`. Start with [Configuration](docs/configuration.md) and edit the paths to your FASTQ files and output folder.

## Introduction

OncoTracer turns LP-WGS CNA analysis into a versioned, repeatable workflow. It starts from FASTQ files, runs the upstream CNA caller route, refines CNA boundaries, and writes organized tables, plots, summaries, and optional pathology concordance outputs.

## Why OncoTracer?

- Reproducible: workflow logic, scripts, containers, and examples live together.
- FASTQ-first: the supported entry points start from Illumina FASTQ or ONT `fastq_pass` barcode FASTQ files.
- LP-WGS focused: designed around copy-number alteration analysis rather than broad variant calling.
- ONT and Illumina aware: uses qDNAseq-style Illumina outputs and ichorCNA-style ONT outputs downstream.
- Report friendly: writes event tables, cytogenomic notation, CNA plots, workflow summaries, and optional pathology concordance.

## Installation

Docker is recommended:

```bash
docker pull carlosfarkas/oncotracer:latest
nextflow run main.nf --docker -params-file params/illumina.example.yml -resume
```

Singularity/Apptainer is recommended for many HPC systems:

```bash
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
nextflow run main.nf --singularity -params-file params/illumina.example.yml -resume
```

Conda is a fallback when containers are not available:

```bash
conda env create -f environment.yml
conda activate oncotracer
nextflow run main.nf --conda -params-file params/illumina.example.yml -resume
```

## Test

```bash
docker run --rm carlosfarkas/oncotracer:latest --help
nextflow -version
nextflow run main.nf -stub-run --docker -params-file params/illumina.example.yml --outdir /tmp/oncotracer_stub
```

Public FASTQ examples are listed in [Example Data](docs/example_data.md).

## Outputs

Key result files:

- `06_workflow_summary/workflow_summary.txt`
- `03_cna_codification/cna_events.tsv`
- `03_cna_codification/cna_cytogenomic_notation.tsv`
- `04_cna_custom_plots/cna_per_sample_pages.pdf`
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`

Example output plots:

![Illumina CNA overview](docs/assets/tutorial/illumina_cna_genome_overview.png)
![ONT CNA overview](docs/assets/tutorial/ont_cna_genome_overview.png)

## Tutorial

See [docs/tutorial_our_runs.md](docs/tutorial_our_runs.md) for a worked FASTQ-first tutorial and example plots in [docs/assets/tutorial/](docs/assets/tutorial/).

## Citation

Citation coming soon. For now, please cite the GitHub repository and include the version or commit used. See [CITATION.cff](CITATION.cff).

## Limitations

OncoTracer is intended for research workflows and reproducible analysis. It does not replace expert review, laboratory validation, or clinical diagnostic interpretation.

## License

See the repository license information. TODO: confirm the final project license before formal release.
