# OncoTracer

![OncoTracer: sequencing reads to copy-number alterations](docs/assets/oncotracer-hero.png)

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://cfarkas.github.io/oncotracer/)
[![Docker](https://img.shields.io/badge/docker-carlosfarkas%2Foncotracer-blue)](https://hub.docker.com/r/carlosfarkas/oncotracer)
[![Nextflow](https://img.shields.io/badge/nextflow-%E2%89%A525.04-green)](https://www.nextflow.io/)

OncoTracer is a reproducible Nextflow research workflow for LP-WGS copy-number alteration analysis from Illumina and Oxford Nanopore FASTQ files.

```text
FASTQ -> SAMURAI qDNAseq/ichorCNA -> boundary refinement -> CNA tables -> plots/reports
```

## Install and prepare public tests

```bash
git clone https://github.com/cfarkas/oncotracer.git              # clone the OncoTracer repository
cd oncotracer                                                    # enter the repository; run main.nf from here
current_dir=$(pwd)                                               # save the absolute repository path
echo $current_dir                                                # confirm the working directory
nextflow -version                                                # confirm that Nextflow is available
docker --version                                                 # confirm that Docker is available
nextflow run main.nf --make_test                                 # download public Illumina and ONT FASTQ files and write test YAML files
```

## Run the public Illumina test

```bash
nextflow run main.nf -stub-run --docker -params-file test/configs/illumina.quickstart.yml  # validate the Illumina workflow without analysis
nextflow run main.nf --docker -params-file test/configs/illumina.quickstart.yml -resume    # run the complete Illumina qDNAseq workflow
```

## Run the public ONT test

```bash
nextflow run main.nf -stub-run --docker -params-file test/configs/ont.quickstart.yml  # validate the ONT workflow without analysis
nextflow run main.nf --docker -params-file test/configs/ont.quickstart.yml -resume    # run the complete ONT ichorCNA workflow
```

See the [complete Quick Start](https://cfarkas.github.io/oncotracer/quick_start/) for the generated YAML files, line-by-line explanations, expected outputs, and examples using your own data.

## Main outputs

- `06_workflow_summary/workflow_summary.txt`
- `03_cna_codification/cna_events.tsv`
- `03_cna_codification/cna_cytogenomic_notation.tsv`
- `04_cna_custom_plots/cna_per_sample_pages.pdf`
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`

## Research-use limitation

OncoTracer is not a standalone diagnostic system. Its results require expert review, laboratory validation, and integration with pathology and orthogonal molecular evidence.
