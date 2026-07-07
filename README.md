# OncoTracer

OncoTracer is a reproducible Nextflow workflow for copy-number alteration (CNA) analysis from low-pass whole-genome sequencing (LP-WGS). It packages the current ONT/Illumina CNA workflow into one command while keeping the underlying scripts versioned inside the repository.

OncoTracer can process:

- Oxford Nanopore Technologies (ONT) LP-WGS data, either from FASTQ/barcodes or from existing ONT SAMURAI/ichorCNA outputs.
- Illumina LP-WGS data from existing SAMURAI/qDNAseq outputs and aligned BAM files.

The workflow produces refined CNA segments, cytogenomic CNA event tables, custom cohort and per-sample plots, optional CNA classification, literature-backed PDF/HTML reports, and optional pathology-vs-CNA concordance tables.

## Why This Exists

Many LP-WGS CNA analyses are run as a chain of local scripts. That works for exploratory analysis, but it is hard to reproduce later or share with another user. OncoTracer wraps the current scripts in Nextflow so a user can clone a repository, edit a YAML file with their input paths, and rerun the same analysis with a consistent output structure.

## Workflow Overview

```text
ONT FASTQ/barcodes or existing ONT/Illumina CNA results
  -> optional ONT SAMURAI step
  -> BAM-supported CNA boundary refinement
  -> CNA codification and cytogenomic notation
  -> custom CNA plots
  -> optional CNA classifier, GISTIC2, literature enrichment, reports
  -> optional pathology concordance/inference
```

## Quick Start

Docker is the preferred portable execution method. Native Conda remains supported as a fallback, and Singularity/Apptainer can run the Docker image on HPC systems.

Clone the repository:

```bash
git clone <REPOSITORY_URL> OncoTracer
cd OncoTracer
```

Run an Illumina analysis from the example YAML:

```bash
nextflow run main.nf -profile local \
  -params-file params/illumina.example.yml \
  -resume
```

Run ONT from existing SAMURAI/ichorCNA outputs:

```bash
nextflow run main.nf -profile local \
  -params-file params/ont.from_existing_samurai.example.yml \
  -resume
```

Run with the classifier and optional pathology CSV:

```bash
nextflow run main.nf -profile local \
  -params-file params/illumina.example.yml \
  --run_cna_classifier true \
  --pathology_csv /path/to/pathology.csv \
  --pathology_sample_col illumina_sample_id \
  --pathology_case_col case_code \
  --pathology_diagnosis_col final_diagnosis \
  -resume
```


## Execution Modes

OncoTracer can run with Docker, Singularity/Apptainer, or native Conda. For new users, Docker is the recommended path.

### Docker: recommended for most users

Docker Hub is the primary container source:

```text
carlosfarkas/oncotracer:latest
carlosfarkas/oncotracer:2026-07-06
carlosfarkas/oncotracer:v0.1.0
```

Check the image first:

```bash
docker pull carlosfarkas/oncotracer:latest
docker run --rm carlosfarkas/oncotracer:latest --help
```

Run with Nextflow's Docker executor from the host:

```bash
nextflow run main.nf -profile docker \
  -params-file params/illumina.example.yml \
  --docker_run_options "-u $(id -u):$(id -g) -e HOME=/tmp -e MPLCONFIGDIR=/tmp/matplotlib -e XDG_CACHE_HOME=/tmp/cache -v /your/data:/your/data" \
  -resume
```

Replace `/your/data:/your/data` with the folder containing the files referenced in your YAML. Keep the left and right sides identical when possible; it makes absolute YAML paths work inside the container.

### Singularity / Apptainer: recommended for HPC

Pull the Docker Hub image as a SIF file:

```bash
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
```

Run with Nextflow:

```bash
nextflow run main.nf -profile singularity \
  -params-file params/illumina.example.yml \
  --singularity_run_options '--bind /your/data:/your/data' \
  -resume
```

### Native Conda: fallback only

Use this when Docker and Singularity/Apptainer are unavailable:

```bash
conda env create -f environment.yml
conda activate oncotracer
nextflow run main.nf -profile conda -params-file params/illumina.example.yml -resume
```

### Maintainer Docker Hub publishing

Most users do not need this. Maintainers should create the Docker Hub repository `carlosfarkas/oncotracer`, then tag and push:

```bash
docker login -u carlosfarkas
docker build -t carlosfarkas/oncotracer:latest .
docker tag carlosfarkas/oncotracer:latest carlosfarkas/oncotracer:2026-07-06
docker tag carlosfarkas/oncotracer:latest carlosfarkas/oncotracer:v0.1.0
docker push carlosfarkas/oncotracer:latest
docker push carlosfarkas/oncotracer:2026-07-06
docker push carlosfarkas/oncotracer:v0.1.0
```

Verify after publishing:

```bash
docker pull carlosfarkas/oncotracer:latest
docker run --rm carlosfarkas/oncotracer:latest --help
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
```

See `docs/containers.md` for detailed Docker, Singularity, and Conda instructions.

## YAML Configuration Files

OncoTracer is configured through YAML files in `params/`:

```text
params/ont.example.yml                         # ONT from FASTQ/barcodes
params/ont.from_existing_samurai.example.yml   # ONT from existing ichorCNA/SAMURAI results
params/illumina.example.yml                    # Illumina qDNAseq/BAM workflow
```

You should copy one of these files, rename it for your project, and edit the paths.

Example:

```bash
cp params/illumina.example.yml params/my_illumina_run.yml
nano params/my_illumina_run.yml
nextflow run main.nf -profile local -params-file params/my_illumina_run.yml -resume
```

## Output Folders

Every run writes numbered stage folders inside `outdir`:

```text
<outdir>/
  01_samurai_ont/          # ONT-only when starting from FASTQ/barcodes
  02_bam_refinement/
  03_cna_codification/
  04_cna_custom_plots/
  05_cna_classifier/       # optional
  06_workflow_summary/
```

Key outputs include:

```text
03_cna_codification/cna_events.tsv
03_cna_codification/cna_cytogenomic_notation.tsv
04_cna_custom_plots/cna_per_sample_pages.pdf
04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf
05_cna_classifier/03_report/pdf_reports/all_sample_CNA_knowledge_reports.pdf
05_cna_classifier/03_report/clinician_reports/all_sample_clinician_driver_summaries.pdf
05_cna_classifier/07_pathology/pathology_concordance.tsv
```

## Documentation

A ReadTheDocs-ready documentation site is included in `docs/`.

The tutorial page `docs/tutorial_our_runs.md` includes the validated ONT and Illumina commands used during packaging, observed output counts, and embedded example figures from the generated CNA plots.

Local preview with MkDocs:

```bash
pip install -r docs/requirements.txt
mkdocs serve
```

Build static documentation:

```bash
mkdocs build
```

## Models and Optional LLM Layers

OncoTracer uses deterministic CNA rules first. Optional Hugging Face/transformer layers are attempted for literature summarization, reference ranking, and pathology semantic agreement. These model attempts are non-fatal: if a model is unavailable, the workflow records the failure and keeps the deterministic output.

Default literature models attempted:

```text
google/flan-t5-small
google/flan-t5-base
Falconsai/medical_summarization
```

Default pathology biomedical transformer models attempted:

```text
microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract
dmis-lab/biobert-base-cased-v1.1
emilyalsentzer/Bio_ClinicalBERT
```

Optional biomedical NER model, disabled by default:

```text
d4data/biomedical-ner-all
```

## Pathology Concordance

If a pathology table is supplied, OncoTracer matches CNA samples to pathology records and compares pathology text/IHC features with CNA-derived classifier evidence. It reports whether the CNA pattern is compatible with the pathology report, partially compatible, not assessable, or unmatched. It does not replace pathology review or clinical interpretation.

## Important Limitation

OncoTracer is a research workflow. It is not a standalone clinical diagnostic system. LP-WGS CNA data must be interpreted with histology, IHC, tumor purity, sequencing depth, clinical context, and orthogonal molecular testing.
