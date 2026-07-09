# Quick Start

This page gives copy-paste commands for a fresh OncoTracer clone. Docker is recommended for new users: use `--docker`. Use `--singularity` on HPC systems with Singularity/Apptainer.

The commands below clone the repository, create a `test/` working folder, download public Illumina and ONT FASTQ examples, ask the built-in YAML agent to write ready-to-run configs, and run both workflows head to toe.

## Docker

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
mkdir -p test
cd test
bash ../bin/scripts/download_quickstart_data.sh .
cd ..
bash bin/scripts/make_quickstart_configs.sh test
nextflow run main.nf --docker -params-file test/configs/illumina.quickstart.yml -resume
nextflow run main.nf --docker -params-file test/configs/ont.quickstart.yml -resume
```

## Singularity / Apptainer

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
mkdir -p test
cd test
bash ../bin/scripts/download_quickstart_data.sh .
cd ..
bash bin/scripts/make_quickstart_configs.sh test
nextflow run main.nf --singularity -params-file test/configs/illumina.quickstart.yml -resume
nextflow run main.nf --singularity -params-file test/configs/ont.quickstart.yml -resume
```

The first full run downloads public FASTQ files, containers, and the hg38 reference, so it can take a while and needs enough disk space.

## Use The YAML Agent Directly

You can also ask `main.nf` to write your own YAML file. This does not run the analysis.

Illumina example:

```bash
nextflow run main.nf \
  --make_config true \
  --config_mode illumina \
  --config_root /data/oncotracer_project \
  --config_out /data/oncotracer_project/configs/my_illumina.yml \
  --config_samplesheet /data/oncotracer_project/input/samplesheet.csv \
  -resume
```

ONT example:

```bash
nextflow run main.nf \
  --make_config true \
  --config_mode ont \
  --config_root /data/oncotracer_project \
  --config_out /data/oncotracer_project/configs/my_ont.yml \
  --config_ont_folder /data/ont_run/fastq_pass \
  --config_ont_barcodes barcode01,barcode02 \
  --config_ont_sample_names caseA,caseB \
  -resume
```

Then run with the generated file:

```bash
nextflow run main.nf --docker -params-file /data/oncotracer_project/configs/my_illumina.yml -resume
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
