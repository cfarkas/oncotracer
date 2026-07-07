# Quick Start

This page gives copy-paste commands for the most common OncoTracer entry points. For new users, Docker is the recommended execution mode.

!!! important "Edit YAML paths first"
    Copy one of the files in `params/`, edit the input and output paths, then run the command. For Docker and Singularity/Apptainer, every absolute path in the YAML must be visible inside the container through a bind mount.

## Docker Run

Use Docker when available. Replace `/your/data:/your/data` with the folder containing the files referenced in your YAML.

```bash
docker pull carlosfarkas/oncotracer:latest
nextflow run main.nf -profile docker \
  -params-file params/illumina.example.yml \
  --docker_run_options "-u $(id -u):$(id -g) -e HOME=/tmp -e MPLCONFIGDIR=/tmp/matplotlib -e XDG_CACHE_HOME=/tmp/cache -v /your/data:/your/data" \
  -resume
```

## Singularity / Apptainer Run

Use this on HPC systems where Docker is not available.

```bash
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
nextflow run main.nf -profile singularity \
  -params-file params/illumina.example.yml \
  --singularity_run_options '--bind /your/data:/your/data' \
  -resume
```

## Conda Fallback

Use this only when neither Docker nor Singularity/Apptainer is available.

```bash
conda env create -f environment.yml
conda activate oncotracer
nextflow run main.nf -profile conda \
  -params-file params/illumina.example.yml \
  -resume
```

## Illumina Example

Use this when you have existing Illumina SAMURAI/qDNAseq output and aligned BAM files.

```bash
cp params/illumina.example.yml params/my_illumina.yml
nano params/my_illumina.yml
nextflow run main.nf -profile docker \
  -params-file params/my_illumina.yml \
  --docker_run_options "-u $(id -u):$(id -g) -e HOME=/tmp -e MPLCONFIGDIR=/tmp/matplotlib -e XDG_CACHE_HOME=/tmp/cache -v /your/data:/your/data" \
  -resume
```

## ONT Existing SAMURAI/ichorCNA Example

Use this when SAMURAI/ichorCNA has already run and you want OncoTracer to begin with boundary refinement.

```bash
cp params/ont.from_existing_samurai.example.yml params/my_ont_existing.yml
nano params/my_ont_existing.yml
nextflow run main.nf -profile docker \
  -params-file params/my_ont_existing.yml \
  --docker_run_options "-u $(id -u):$(id -g) -e HOME=/tmp -e MPLCONFIGDIR=/tmp/matplotlib -e XDG_CACHE_HOME=/tmp/cache -v /your/data:/your/data" \
  -resume
```

## ONT FASTQ/Barcode Example

Use this when you want OncoTracer to run the ONT SAMURAI step from barcode/FASTQ inputs.

```bash
cp params/ont.example.yml params/my_ont_fastq.yml
nano params/my_ont_fastq.yml
nextflow run main.nf -profile docker \
  -params-file params/my_ont_fastq.yml \
  --docker_run_options "-u $(id -u):$(id -g) -e HOME=/tmp -e MPLCONFIGDIR=/tmp/matplotlib -e XDG_CACHE_HOME=/tmp/cache -v /your/data:/your/data" \
  -resume
```

## After A Successful Core Run

Check these files first:

```text
06_workflow_summary/workflow_summary.txt
03_cna_codification/cna_events.tsv
03_cna_codification/cna_cytogenomic_notation.tsv
04_cna_custom_plots/cna_per_sample_pages.pdf
04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf
```

Then enable classifier and pathology concordance if needed:

```bash
nextflow run main.nf -profile docker \
  -params-file params/my_illumina.yml \
  --run_cna_classifier true \
  --pathology_csv /your/data/pathology.csv \
  --pathology_sample_col illumina_sample_id \
  --pathology_case_col case_code \
  --pathology_diagnosis_col final_diagnosis \
  --docker_run_options "-u $(id -u):$(id -g) -e HOME=/tmp -e MPLCONFIGDIR=/tmp/matplotlib -e XDG_CACHE_HOME=/tmp/cache -v /your/data:/your/data" \
  -resume
```
