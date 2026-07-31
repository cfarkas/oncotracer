# OncoTracer

![OncoTracer: sequencing reads to copy-number alterations](docs/assets/oncotracer-hero.png)

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://cfarkas.github.io/oncotracer/)
[![Docker](https://img.shields.io/badge/docker-carlosfarkas%2Foncotracer-blue)](https://hub.docker.com/r/carlosfarkas/oncotracer)
[![Nextflow](https://img.shields.io/badge/nextflow-%E2%89%A525.04-green)](https://www.nextflow.io/)

OncoTracer is a Nextflow research workflow for **low-pass whole-genome sequencing (LP-WGS)**. It accepts Illumina single-end or paired-end FASTQ files and Oxford Nanopore Technologies (ONT) FASTQ files, then reports **copy-number alterations (CNAs)**: genomic regions with gains or losses of DNA.

```text
FASTQ -> SAMURAI qDNAseq/ichorCNA -> boundary refinement -> CNA tables -> plots and reports
```

Read the [documentation](https://cfarkas.github.io/oncotracer/) for installation, complete tutorials, configuration options, output interpretation, and troubleshooting.

## Before you start

Use Linux and install these host prerequisites:

- [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)
- [Java 17 or newer](https://www.nextflow.io/docs/latest/install.html#requirements)
- [Nextflow](https://www.nextflow.io/docs/latest/install/)
- [Docker Engine](https://docs.docker.com/engine/install/) or, on HPC, [Apptainer](https://apptainer.org/docs/admin/main/installation.html)
- Python 3, samtools, BWA, minimap2, pigz, and curl or wget for the host-side stage-01/reference helpers

> [!IMPORTANT]
> Launch every OncoTracer preparation, test, and analysis with `nextflow run`.
> `--docker` and `--singularity` are options on that Nextflow command;
> Nextflow selects and starts the required containers. Do not start OncoTracer
> with a separate `docker run`, `docker exec`, `apptainer run`,
> `apptainer exec`, `singularity run`, or `singularity exec` command.

The first real analysis downloads the hg38 reference (about **3.16 GB**) and BWA may take **30–60 minutes** to index it. The pinned BWA task requests 72 GB, so an uncached first run needs at least 80 GiB of addressable RAM. A valid cached index is reused by later runs. Container images and working files require additional disk space.

## General usage

Choose generic locations for the repository and for your analysis project. Keep sequencing data and results outside the Git clone.

```bash
# Choose where the repository and analysis project will live.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR=/path/to/my/directory/my_oncotracer_project

# Clone OncoTracer and enter the repository.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"
cd "$REPO_DIR"

# Generate a YAML configuration from an Illumina FASTQ folder and sample table.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/fastq" \
  --sample_table "$PROJECT_DIR/input/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config" \
  --auto_outdir "$PROJECT_DIR/results"

# Run the generated configuration with Docker and keep resumable work files.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work" \
  -resume

# Open the human-readable workflow summary after a successful run.
cat "$PROJECT_DIR/results/06_workflow_summary/workflow_summary.txt"
```

For ONT barcode folders, use `--mode ont`; Automatic Setup writes the corresponding ONT YAML. On an HPC system configured with Apptainer or Singularity, change only the Nextflow option from `--docker` to `--singularity`.

See [Automatic Setup](https://cfarkas.github.io/oncotracer/auto_params/) for the supported Illumina and ONT folder layouts, sample-table formats, normal controls, and generated files. Use [Manual YAML Editing](https://cfarkas.github.io/oncotracer/configuration/yaml_basics/) only when automatic detection does not fit the study.

## Small public verification

QuickStart Example 1 downloads about **225 MB of public reads**, runs one Illumina sample and one ONT sample, and verifies the expected outputs.

```bash
# Choose a generic clone location and its public-test directory.
REPO_DIR=/path/to/my/directory/oncotracer
TEST_ROOT="$REPO_DIR/test"

# Clone the repository and enter it.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"
cd "$REPO_DIR"

# Download and validate the public reads, then generate both run plans.
nextflow run "$REPO_DIR/main.nf" --make_test \
  --test_root "$TEST_ROOT"

# Run the Illumina example first.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$TEST_ROOT/configs/illumina.quickstart.yml" \
  -work-dir "$TEST_ROOT/work/illumina" \
  -resume

# Run the ONT example after the Illumina example finishes.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$TEST_ROOT/configs/ont.quickstart.yml" \
  -work-dir "$TEST_ROOT/work/ont" \
  -resume

# Verify that both workflows produced their required outputs.
python3 "$REPO_DIR/examples/quickstart/verify_outputs.py" \
  --test-root "$TEST_ROOT"
```

See [QuickStart Example 1](https://cfarkas.github.io/oncotracer/quick_start/) for the generated YAML files, expected runtime behavior, checkpoints, and output paths.

## Additional guides

| Goal | Guide |
| --- | --- |
| Run three paired public HCC1143 samples | [QuickStart Example 2](https://cfarkas.github.io/oncotracer/public_cohort/) |
| Build a local qDNAseq panel of normals from six tumors and four controls | [QuickStart Example 3](https://cfarkas.github.io/oncotracer/six_tumor_four_control/) |
| Reproduce all 12 public PRJNA754199 plasma libraries | [Full Tutorial](https://cfarkas.github.io/oncotracer/full_tutorial/) |
| Configure inputs manually | [Manual YAML and path guide](https://cfarkas.github.io/oncotracer/configuration/yaml_basics/) |
| Understand input formats | [Input files and folder layouts](https://cfarkas.github.io/oncotracer/inputs/) |
| Inspect example results | [Results Gallery](https://cfarkas.github.io/oncotracer/gallery/) |

## Illumina local panel of normals

For Illumina cohorts, rows marked `NORMAL` can build a run-local qDNAseq panel of normals (PoN). Automatic Setup disables the PoN when there are no normal rows, rejects exactly one normal, and enables the PoN when at least two controls are present. Corrected CNA outputs contain `TUMOR` samples only; controls remain provenance and quality-control inputs.

Control handling is fail-closed: an enabled panel must name every and only the normal rows. The exact completion marker is `qdnaseq_local_pon.done`, whose successful content must be `QDNASEQ_LOCAL_PON_SUCCESS`. A missing, empty, or different value means the panel is incomplete even when partial files remain.

Read [Illumina configuration](https://cfarkas.github.io/oncotracer/configuration/illumina/) for the PoN settings and [Output Files](https://cfarkas.github.io/oncotracer/outputs/#illumina-local-panel-of-normals) for manifests, leave-one-out control QC, reference bins, and corrected tumor outputs.

## Main outputs

- `01_samurai_illumina/qdnaseq_local_pon/`: local-PoN manifest, QC, reference bins, and tumor-only corrected CNA outputs when enabled
- `06_workflow_summary/workflow_summary.txt`: start here after a run
- `03_cna_codification/cna_events.tsv`: CNA event table
- `03_cna_codification/cna_cytogenomic_notation.tsv`: cytogenomic notation
- `04_cna_custom_plots/cna_per_sample_pages.pdf`: per-sample plots
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`: cohort plot

## Research-use limitation

OncoTracer is not a standalone diagnostic system. Results require expert review, laboratory validation, and integration with pathology and orthogonal molecular evidence.
