# Beginner Tutorial: Reproduce The Quickstart

This tutorial is for users who are new to Nextflow, YAML, or OncoTracer. It reproduces both public example runs from a clean clone and explains what each step does.

## What You Need

Install these first:

- `git`, to download the repository;
- `nextflow`, to run the workflow;
- Docker or Singularity/Apptainer, to run the software environment.

Use Docker on a workstation. Use Singularity/Apptainer on many HPC systems.

## 1. Clone OncoTracer

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
```

This downloads the workflow code and moves your shell into the repository.

## 2. Create A Test Workspace

```bash
mkdir -p test
cd test
```

The `test/` folder will hold public FASTQ files, generated YAML files, references, caches, and output folders. Keeping these under one folder makes the example easy to remove later.

## 3. Download Public Test Data

```bash
bash ../bin/scripts/download_quickstart_data.sh .
```

This downloads small public example inputs into the current `test/` folder:

```text
public/illumina_DRR000542/
public/ont_DRR165691/
```

Go back to the repository root before running Nextflow:

```bash
cd ..
```

## 4. Generate YAML Configs With The Built-In Agent

```bash
bash bin/scripts/make_quickstart_configs.sh test
```

This calls the YAML agent inside `main.nf` and writes:

```text
test/configs/illumina.quickstart.yml
test/configs/ont.quickstart.yml
```

A YAML file is a plain-text settings file. For example, the Illumina YAML tells OncoTracer where the Illumina samplesheet is, where outputs should go, and which CNA caller to use.

You can inspect a YAML file with:

```bash
cat test/configs/illumina.quickstart.yml
cat test/configs/ont.quickstart.yml
```

## 5. Run The Illumina Example Head To Toe

Docker:

```bash
nextflow run main.nf --docker -params-file test/configs/illumina.quickstart.yml -resume
```

Singularity / Apptainer:

```bash
nextflow run main.nf --singularity -params-file test/configs/illumina.quickstart.yml -resume
```

This run starts from paired-end Illumina FASTQ files and runs:

```text
FASTQ -> alignment/qDNAseq -> BAM boundary refinement -> CNA notation -> CNA plots -> summary
```

The first run may take a long time because it downloads containers and reference data and creates an aligned BAM.

## 6. Run The ONT Example Head To Toe

Docker:

```bash
nextflow run main.nf --docker -params-file test/configs/ont.quickstart.yml -resume
```

Singularity / Apptainer:

```bash
nextflow run main.nf --singularity -params-file test/configs/ont.quickstart.yml -resume
```

This run starts from ONT `fastq_pass/barcode01` FASTQ files and runs:

```text
FASTQ -> SAMURAI/ichorCNA -> BAM boundary refinement -> CNA notation -> CNA plots -> summary
```

## 7. Check The Results

Illumina summary:

```bash
cat test/runs/illumina/06_workflow_summary/workflow_summary.txt
```

ONT summary:

```bash
cat test/runs/ont/06_workflow_summary/workflow_summary.txt
```

Important output files include:

```text
03_cna_codification/cna_events.tsv
03_cna_codification/cna_cytogenomic_notation.tsv
04_cna_custom_plots/cna_per_sample_pages.pdf
04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf
06_workflow_summary/workflow_summary.txt
```

If the quickstart sample has no CNA events after filtering, OncoTracer still writes placeholder plot PDFs so the run completes reproducibly.

## 8. Make Your Own YAML

Illumina users usually provide a samplesheet:

```bash
nextflow run main.nf \
  --make_config true \
  --config_mode illumina \
  --config_root /data/my_oncotracer_run \
  --config_out /data/my_oncotracer_run/configs/my_illumina.yml \
  --config_samplesheet /data/my_oncotracer_run/input/samplesheet.csv \
  -resume
```

ONT users usually provide a `fastq_pass` folder and barcode list:

```bash
nextflow run main.nf \
  --make_config true \
  --config_mode ont \
  --config_root /data/my_oncotracer_run \
  --config_out /data/my_oncotracer_run/configs/my_ont.yml \
  --config_ont_folder /data/ont_run/fastq_pass \
  --config_ont_barcodes barcode01,barcode02 \
  --config_ont_sample_names caseA,caseB \
  -resume
```

Then run the generated YAML with Docker or Singularity:

```bash
nextflow run main.nf --docker -params-file /data/my_oncotracer_run/configs/my_illumina.yml -resume
```

## Troubleshooting

If a run stops, fix the reported issue and rerun the same command with `-resume`. Nextflow will reuse completed work where possible.

If Docker is unavailable, use `--singularity` instead. If you are on an HPC system, ask your administrator whether the command is `singularity` or `apptainer`; Nextflow uses the same `--singularity` flag for both.
