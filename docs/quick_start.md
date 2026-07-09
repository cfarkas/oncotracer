# Quick Start

This page gives copy-paste commands for the three supported OncoTracer entry points. Docker is recommended for new users: use `--docker`. Use `--singularity` on HPC systems with Singularity/Apptainer.

!!! tip "Edit YAML paths first"
    Copy one file from `params/`, edit the FASTQ paths and output path, then run. YAML is a plain-text settings file with lines like `setting_name: value`.

## 1. Illumina FASTQ Files

```bash
cp params/illumina.example.yml params/my_illumina.yml
nano params/my_illumina.yml
nextflow run main.nf --docker   -params-file params/my_illumina.yml   -resume
```

## 2. ONT fastq_pass / Barcode FASTQ Files

```bash
cp params/ont.example.yml params/my_ont.yml
nano params/my_ont.yml
nextflow run main.nf --docker   -params-file params/my_ont.yml   -resume
```

## 3. Illumina FASTQ Files + Pathology Reports

```bash
cp params/illumina.pathology.example.yml params/my_illumina_pathology.yml
nano params/my_illumina_pathology.yml
nextflow run main.nf --docker   -params-file params/my_illumina_pathology.yml   -resume
```

## Singularity / Apptainer

Use this on HPC systems where Docker is not available.

```bash
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
nextflow run main.nf --singularity   -params-file params/my_illumina.yml   -resume
```

## Conda Fallback

Use Conda only when containers are unavailable.

```bash
conda env create -f environment.yml
conda activate oncotracer
nextflow run main.nf --conda   -params-file params/my_illumina.yml   -resume
```

## After A Successful Run

Check these files first:

```text
06_workflow_summary/workflow_summary.txt
03_cna_codification/cna_events.tsv
03_cna_codification/cna_cytogenomic_notation.tsv
04_cna_custom_plots/cna_per_sample_pages.pdf
04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf
```

## Public FASTQ Examples

See [Example Data](example_data.md) for real ENA download commands for Illumina and ONT FASTQ smoke tests.
