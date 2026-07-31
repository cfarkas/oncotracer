# OncoTracer

![OncoTracer sequencing-to-CNA workflow](assets/oncotracer-hero.png)

OncoTracer is a reproducible Nextflow research workflow for **low-pass whole-genome sequencing (LP-WGS)**. It processes Illumina single-end or paired-end FASTQs and Oxford Nanopore Technologies (ONT) FASTQs, then produces copy-number alteration tables, plots, and reports.

Run the workflow through Nextflow with either `--docker` or `--singularity`. The Docker option uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer); a configured Singularity/Apptainer installation can use the same image on HPC.

## Choose where to start

| Goal | Guide | Data |
| --- | --- | --- |
| Verify the installation | [QuickStart Example 1](quick_start.md) | One public Illumina and one public ONT sample |
| Analyze your own reads | [Automatic Setup](auto_params.md) | Your Illumina FASTQ folder or ONT barcode folders |
| Run a small public Illumina cohort | [QuickStart Example 2](public_cohort.md) | Three public HCC1143 libraries, six FASTQs |
| Process the complete public PRJNA754199 archive | [Full Tutorial](full_tutorial.md) | Twelve public single-end plasma libraries |
| Configure unusual inputs manually | [Manual YAML editing](configuration/yaml_basics.md) | Your own samplesheet and YAML |
| Add pathology information | [Pathology and classifier](configuration/pathology.md) | Your own matched sequencing and pathology files |

## Other example runs

[Six Illumina tumors and four normal controls](six_tumor_four_control.md) is a configuration example for user-provided data. `ONCO001`–`ONCO006` and `CTRL001`–`CTRL004` are placeholders; their FASTQs are not included in the repository, so that page will not run until all 20 files are supplied.

## Basic run pattern

```bash
# Generate a YAML and samplesheet from supported FASTQ names.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /path/to/project/input/fastq \
  --sample_table /path/to/project/input/samples.csv \
  --auto_config_dir /path/to/project/config \
  --auto_outdir /path/to/project/results

# Run the generated YAML with Docker.
nextflow run main.nf --docker \
  -params-file /path/to/project/config/illumina.auto.yml \
  -work-dir /path/to/project/work \
  -resume

# Use this alternative on an HPC configured with Singularity or Apptainer.
nextflow run main.nf --singularity \
  -params-file /path/to/project/config/illumina.auto.yml \
  -work-dir /path/to/project/work \
  -resume
```

Automatic Setup only writes configuration files. The second command starts the analysis. `-resume` reuses unchanged completed tasks after an interruption.

## Estimated time for the first analysis

The first uncached analysis downloads the hg38 reference, approximately **3.16 GB**, and builds its BWA index. Indexing commonly takes **30–60 minutes**. The pinned BWA task requests 72 GB, so use at least **80 GiB of addressable RAM**. Later runs reuse a valid index.

## What the workflow does

```text
FASTQ reads
    |
    +-- Illumina -> SAMURAI + qDNAseq
    |
    +-- ONT ------> SAMURAI + ichorCNA
                         |
                         v
             CNA boundary refinement
                         |
                         v
               tables, plots, reports
```

## Research use

OncoTracer is not a standalone diagnostic system. Results require expert interpretation, laboratory validation, and integration with pathology and orthogonal molecular tests.
