# OncoTracer

**Reproducible LP-WGS CNA analysis for ONT and Illumina data.**

OncoTracer is a Nextflow workflow that turns existing LP-WGS copy-number analysis scripts into a repeatable, documented pipeline. It produces refined CNA events, cytogenomic notation, plots, optional reports, and optional pathology concordance outputs.

!!! warning "Research workflow"
    OncoTracer is intended for research use. It is not a standalone diagnostic system and does not replace expert pathology or clinical interpretation.

## Start Here

1. Pick the user path that matches your data.
2. Copy the matching YAML file from `params/`.
3. Edit only the paths first.
4. Run the workflow with Docker, Singularity/Apptainer, or Conda.

For command examples, go to [Quick Start](quick_start.md). For all YAML fields, go to [Configuration](configuration.md).

## Choose Your Path

### I have Illumina LP-WGS CNA outputs

Use this path if you already have SAMURAI/qDNAseq output and aligned BAM files.

- YAML: [`params/illumina.example.yml`](https://github.com/cfarkas/oncotracer/blob/main/params/illumina.example.yml)
- Configuration help: [Illumina YAML](configuration.md#illumina-yaml)
- Run commands: [Quick Start](quick_start.md#illumina-example)

### I have ONT SAMURAI/ichorCNA outputs

Use this path if ONT CNA calling already ran and you want OncoTracer to start from BAM-supported boundary refinement.

- YAML: [`params/ont.from_existing_samurai.example.yml`](https://github.com/cfarkas/oncotracer/blob/main/params/ont.from_existing_samurai.example.yml)
- Configuration help: [ONT from existing SAMURAI/ichorCNA YAML](configuration.md#ont-from-existing-samuraiichorcna-yaml)
- Run commands: [Quick Start](quick_start.md#ont-existing-samuraiichorcna-example)

### I have ONT FASTQ/barcodes

Use this path if you want the workflow to run the ONT SAMURAI step before boundary refinement and downstream reporting.

- YAML: [`params/ont.example.yml`](https://github.com/cfarkas/oncotracer/blob/main/params/ont.example.yml)
- Configuration help: [ONT from FASTQ/barcodes YAML](configuration.md#ont-from-fastqbarcodes-yaml)
- Run commands: [Quick Start](quick_start.md#ont-fastqbarcode-example)

!!! tip "First run"
    Keep `run_cna_classifier: false` for the first run if you mainly want to validate paths, BAM refinement, CNA codification, and plots. Enable classifier/report stages after the core workflow succeeds.

## What You Get

The workflow writes numbered output folders so it is easy to inspect each stage:

```text
01_samurai_ont/
02_bam_refinement/
03_cna_codification/
04_cna_custom_plots/
05_cna_classifier/
06_workflow_summary/
```

Main outputs include:

- CNA event tables: `03_cna_codification/cna_events.tsv`
- Cytogenomic notation: `03_cna_codification/cna_cytogenomic_notation.tsv`
- Per-sample and cohort CNA plots: `04_cna_custom_plots/`
- Optional CNA reports: `05_cna_classifier/03_report/`
- Optional pathology concordance: `05_cna_classifier/07_pathology/pathology_concordance.tsv`
- Run summary: `06_workflow_summary/workflow_summary.txt`

## Example Outputs

The tutorial includes example plots generated during validation runs.

| Illumina genome overview | ONT genome overview |
|---|---|
| ![Illumina CNA genome overview](assets/tutorial/illumina_cna_genome_overview.png) | ![ONT CNA genome overview](assets/tutorial/ont_cna_genome_overview.png) |

See the full worked example in [Tutorial](tutorial_our_runs.md).

## More Documentation

- [Installation](installation.md)
- [Inputs](inputs.md)
- [Running OncoTracer](running.md)
- [Outputs](outputs.md)
- [Models & Pathology](models_pathology.md)
- [Troubleshooting](troubleshooting.md)
