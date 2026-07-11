# OncoTracer

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://cfarkas.github.io/oncotracer/)
[![Docker](https://img.shields.io/badge/docker-carlosfarkas%2Foncotracer-blue)](https://hub.docker.com/r/carlosfarkas/oncotracer)
[![Nextflow](https://img.shields.io/badge/nextflow-%E2%89%A525.04-green)](https://www.nextflow.io/)
[![GitHub release](https://img.shields.io/github/v/release/cfarkas/oncotracer?display_name=tag)](https://github.com/cfarkas/oncotracer/releases)

**OncoTracer is a reproducible Nextflow research workflow for LP-WGS copy-number alteration analysis from ONT and Illumina FASTQ data. It is not a standalone diagnostic system.**

Full documentation: <https://cfarkas.github.io/oncotracer/>

```text
FASTQ files -> SAMURAI/qDNAseq or SAMURAI/ichorCNA -> boundary refinement
            -> CNA event tables -> cytogenomic notation -> plots/reports
```

## Start Here

1. Read [Before You Begin](docs/getting_started/before_you_begin.md).
2. Learn path basics in [What Is a Path?](docs/getting_started/paths.md).
3. Install Nextflow plus Docker, Singularity/Apptainer, or Conda in [Install OncoTracer](docs/installation.md).
4. Copy a versioned YAML template from `params/`, edit real absolute paths, validate it, and run with `-params-file`.

## Installation

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
nextflow -version
docker pull carlosfarkas/oncotracer:latest
```

On HPC systems, Singularity/Apptainer is often used instead of Docker:

```bash
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
```

## First Run

Illumina:

```bash
cp params/illumina.minimal.yml params/my_illumina.yml
realpath .
nano params/my_illumina.yml
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume
```

ONT:

```bash
cp params/ont.minimal.yml params/my_ont.yml
realpath .
nano params/my_ont.yml
nextflow run main.nf -stub-run --docker -params-file params/my_ont.yml
nextflow run main.nf --docker -params-file params/my_ont.yml -resume
```

Use `--singularity` instead of `--docker` where Docker is not available.

## Inputs

Illumina runs need paired FASTQ files and a CSV samplesheet:

```csv
sample,fastq_1,fastq_2,status
Sample_A,/home/student/data/Sample_A_R1.fastq.gz,/home/student/data/Sample_A_R2.fastq.gz,tumor
```

ONT runs need a folder containing FASTQ files or barcode folders, plus barcode names such as `barcode01,barcode02`.

## Outputs

Important output files under `outdir`:

- `06_workflow_summary/workflow_summary.txt`
- `03_cna_codification/cna_events.tsv`
- `03_cna_codification/cna_cytogenomic_notation.tsv`
- `04_cna_custom_plots/cna_per_sample_pages.pdf`
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`

## Support

See [Troubleshooting](docs/troubleshooting.md). When asking for help, include the command, YAML file, `.nextflow.log`, and the last error message.

## Citation and Research-Use Limitations

Citation coming soon. For now, cite the GitHub repository and include the version or commit used. See [CITATION.cff](CITATION.cff).

OncoTracer is intended for research workflows and reproducible analysis. It does not replace expert review, laboratory validation, or clinical diagnostic interpretation.
