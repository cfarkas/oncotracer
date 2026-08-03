# OncoTracer

![OncoTracer: sequencing reads to copy-number alterations](docs/assets/oncotracer-hero.png)

[![Release](https://img.shields.io/github/v/release/cfarkas/oncotracer)](https://github.com/cfarkas/oncotracer/releases)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://cfarkas.github.io/oncotracer/)
[![Docker](https://img.shields.io/badge/docker-carlosfarkas%2Foncotracer-blue)](https://hub.docker.com/r/carlosfarkas/oncotracer)
[![Nextflow](https://img.shields.io/badge/nextflow-%E2%89%A525.04-green)](https://www.nextflow.io/)

OncoTracer is a Nextflow research workflow for **low-pass whole-genome sequencing (LP-WGS)**. It accepts Illumina single-end or paired-end FASTQ files and Oxford Nanopore Technologies (ONT) FASTQ files, then produces copy-number alteration tables, plots, and reports.

```text
FASTQ -> SAMURAI qDNAseq/ichorCNA -> boundary refinement -> CNA tables -> plots and reports
```

Read the [complete documentation](https://cfarkas.github.io/oncotracer/) for installation, tutorials, input formats, configuration, outputs, and troubleshooting.

## Requirements

Use Linux with [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git), [Java 17](https://adoptium.net/temurin/releases/?version=17) or newer, [Nextflow](https://www.nextflow.io/docs/latest/install.html), [Python 3](https://www.python.org/downloads/), [samtools](https://www.htslib.org/download/), [BWA](https://github.com/lh3/bwa), [minimap2](https://github.com/lh3/minimap2), [pigz](https://zlib.net/pigz/), and [curl](https://curl.se/download.html) or [wget](https://www.gnu.org/software/wget/).

Choose one execution environment: [Docker Engine](https://docs.docker.com/engine/install/), [SingularityCE](https://docs.sylabs.io/guides/latest/admin-guide/installation.html)/[Apptainer](https://apptainer.org/docs/admin/main/installation.html), or [Miniforge/Conda](https://github.com/conda-forge/miniforge). Install [Poetry](https://python-poetry.org/docs/#installation) only for the Poetry launcher route.

The first uncached analysis downloads the hg38 reference, about **3.16 GB**, and creates a BWA index. This commonly takes **30–60 minutes** and requires substantial memory; provide at least 80 GiB of addressable RAM. Later runs reuse a valid index.

## Clone OncoTracer

```bash
# Clone OncoTracer and enter the repository.
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
```

<a id="four-equivalent-analysis-commands"></a>

## Four installation and execution methods

OncoTracer reads an analysis from a YAML configuration file. A YAML is a plain-text file containing input, output, and analysis settings. Open the [minimal Illumina YAML example](params/illumina.minimal.yml) or [minimal ONT YAML example](params/ont.minimal.yml) to see the format. [Automatic Setup](https://cfarkas.github.io/oncotracer/auto_params/) creates the YAML and samplesheet for your FASTQs.

In the commands below, `CONFIG` points to a generated YAML. Choose **one** method; do not run all four.

### Installation and execution through Docker

Install [Docker Engine](https://docs.docker.com/engine/install/) once. Nextflow downloads and reuses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).

```bash
# Run a generated Illumina YAML through Docker.
PROJECT_DIR="$(pwd)/project"
CONFIG="$PROJECT_DIR/config/illumina.auto.yml"
nextflow run main.nf --docker \
  -params-file "$CONFIG" \
  -work-dir "$PROJECT_DIR/work/docker" \
  -resume
```

### Installation and execution through Singularity or Apptainer

Install [SingularityCE](https://docs.sylabs.io/guides/latest/admin-guide/installation.html) or [Apptainer](https://apptainer.org/docs/admin/main/installation.html) once. Nextflow obtains the same maintained image used by Docker.

```bash
# Run the same generated YAML through Singularity or Apptainer.
PROJECT_DIR="$(pwd)/project"
CONFIG="$PROJECT_DIR/config/illumina.auto.yml"
nextflow run main.nf --singularity \
  -params-file "$CONFIG" \
  -work-dir "$PROJECT_DIR/work/singularity" \
  -resume
```

### Installation and execution through Poetry

Install [Poetry](https://python-poetry.org/docs/#installation) and one scientific backend. `poetry install` creates the isolated launcher environment. The example below uses Docker.

```bash
# Install the Poetry launcher and run the generated YAML with Docker.
PROJECT_DIR="$(pwd)/project"
CONFIG="$PROJECT_DIR/config/illumina.auto.yml"
poetry install --no-interaction
poetry run oncotracer --repo-dir . --backend docker \
  -params-file "$CONFIG" \
  -work-dir "$PROJECT_DIR/work/poetry" \
  -resume
```

The Poetry launcher also accepts `--backend singularity` and `--backend conda`.

### Installation and execution through Conda

Install [Miniforge or Conda](https://github.com/conda-forge/miniforge) once. Nextflow can create and reuse the required Conda environments automatically from the versioned definitions.

```bash
# Run the generated YAML through native Conda environments.
PROJECT_DIR="$(pwd)/project"
CONFIG="$PROJECT_DIR/config/illumina.auto.yml"
nextflow run main.nf --conda \
  -params-file "$CONFIG" \
  -work-dir "$PROJECT_DIR/work/conda" \
  -resume
```

For ONT, point `CONFIG` to the generated `ont.auto.yml`.

## Run your own FASTQs

Create a small Illumina sample table whose names match the FASTQ filenames:

```csv
sample_name,status
TUMOR_01,TUMOR
TUMOR_02,TUMOR
CONTROL_01,NORMAL
CONTROL_02,NORMAL
```

```bash
# Clone OncoTracer and enter the repository.
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer

# Create the project folders and sample table.
PROJECT_DIR="$(pwd)/project"
mkdir -p "$PROJECT_DIR/input/fastq"
cat > "$PROJECT_DIR/input/samples.csv" <<'CSV'
sample_name,status
TUMOR_01,TUMOR
TUMOR_02,TUMOR
CONTROL_01,NORMAL
CONTROL_02,NORMAL
CSV

# Generate the Illumina YAML and samplesheet.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/fastq" \
  --sample_table "$PROJECT_DIR/input/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config" \
  --auto_outdir "$PROJECT_DIR/results"

# Run with Conda; use --docker or --singularity for another method.
nextflow run main.nf --conda \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/conda" \
  -resume

# Read the workflow summary.
cat "$PROJECT_DIR/results/06_workflow_summary/workflow_summary.txt"
```

For ONT barcode folders, use `--mode ont`. See [Automatic Setup](https://cfarkas.github.io/oncotracer/auto_params/) for complete Illumina and ONT examples.

## QuickStart Example 1: one public Illumina and one public ONT sample

This example downloads about **225 MB** of public reads, creates both YAML files, runs both workflows, and verifies the outputs.

```bash
# Clone OncoTracer and enter the repository.
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer

# Download the public reads and create both YAML files.
TEST_ROOT="$(pwd)/test"
nextflow run main.nf --make_test \
  --test_root "$TEST_ROOT"

# Run Illumina first.
nextflow run main.nf --docker \
  -params-file "$TEST_ROOT/configs/illumina.quickstart.yml" \
  -work-dir "$TEST_ROOT/work/illumina" \
  -resume

# Run ONT after Illumina finishes.
nextflow run main.nf --docker \
  -params-file "$TEST_ROOT/configs/ont.quickstart.yml" \
  -work-dir "$TEST_ROOT/work/ont" \
  -resume

# Verify both output sets.
python3 examples/quickstart/verify_outputs.py \
  --test-root "$TEST_ROOT"
```

See [QuickStart Example 1](https://cfarkas.github.io/oncotracer/quick_start/) for Docker, Singularity/Apptainer, Poetry, and Conda alternatives.

## QuickStart Example 2: three public HCC1143 libraries

This example downloads six public paired-end FASTQs and demonstrates Automatic Setup for three libraries.

```bash
# Clone OncoTracer and enter the repository.
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer

# Create the HCC1143 reads directory.
READS_DIR="$(pwd)/test/public/hcc1143_lpwgs"
mkdir -p "$READS_DIR"

# Download HCC1143_DMSO read 1.
if [[ ! -s "$READS_DIR/HCC1143_DMSO_R1.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_1.fastq.gz
  mv "$READS_DIR/SRR7085656_1.fastq.gz" \
    "$READS_DIR/HCC1143_DMSO_R1.fastq.gz"
fi

# Download HCC1143_DMSO read 2.
if [[ ! -s "$READS_DIR/HCC1143_DMSO_R2.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_2.fastq.gz
  mv "$READS_DIR/SRR7085656_2.fastq.gz" \
    "$READS_DIR/HCC1143_DMSO_R2.fastq.gz"
fi

# Download HCC1143_BEZ235 read 1.
if [[ ! -s "$READS_DIR/HCC1143_BEZ235_R1.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_1.fastq.gz
  mv "$READS_DIR/SRR7085655_1.fastq.gz" \
    "$READS_DIR/HCC1143_BEZ235_R1.fastq.gz"
fi

# Download HCC1143_BEZ235 read 2.
if [[ ! -s "$READS_DIR/HCC1143_BEZ235_R2.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_2.fastq.gz
  mv "$READS_DIR/SRR7085655_2.fastq.gz" \
    "$READS_DIR/HCC1143_BEZ235_R2.fastq.gz"
fi

# Download HCC1143_TRAMETINIB read 1.
if [[ ! -s "$READS_DIR/HCC1143_TRAMETINIB_R1.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_1.fastq.gz
  mv "$READS_DIR/SRR7085657_1.fastq.gz" \
    "$READS_DIR/HCC1143_TRAMETINIB_R1.fastq.gz"
fi

# Download HCC1143_TRAMETINIB read 2.
if [[ ! -s "$READS_DIR/HCC1143_TRAMETINIB_R2.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_2.fastq.gz
  mv "$READS_DIR/SRR7085657_2.fastq.gz" \
    "$READS_DIR/HCC1143_TRAMETINIB_R2.fastq.gz"
fi

# Create the exact sample table.
cat > "$READS_DIR/samples.csv" <<'CSV'
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
CSV

# Generate the YAML and R1/R2 samplesheet.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$READS_DIR" \
  --sample_table "$READS_DIR/samples.csv" \
  --auto_config_dir "test/configs/hcc1143_lpwgs" \
  --auto_outdir "test/runs/hcc1143_lpwgs"

# Run the generated configuration with Docker.
nextflow run main.nf --docker \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs-docker" \
  -resume
```

See [QuickStart Example 2](https://cfarkas.github.io/oncotracer/public_cohort/) for MD5, gzip, output, resume, Singularity/Apptainer, Poetry, and Conda commands.

## Other Example Runs

[Six tumors and four normal controls](https://cfarkas.github.io/oncotracer/six_tumor_four_control/) is a mock example illustrating how four `NORMAL` samples build a local qDNAseq panel of normals for six `TUMOR` samples.

The [Full Tutorial](https://cfarkas.github.io/oncotracer/full_tutorial/) downloads and processes all 12 public PRJNA754199 libraries currently available from the archive.

## Normal controls

For Illumina, Automatic Setup disables the local panel when there are no `NORMAL` rows, rejects exactly one normal, and enables the panel with at least two controls. Corrected CNA outputs contain tumor samples; controls remain reference and quality-control inputs.

## Main outputs

- `06_workflow_summary/workflow_summary.txt`: first file to open after a run
- `03_cna_codification/cna_events.tsv`: CNA event table
- `03_cna_codification/cna_cytogenomic_notation.tsv`: cytogenomic notation
- `04_cna_custom_plots/cna_per_sample_pages.pdf`: per-sample plots
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`: cohort plot
- `01_samurai_illumina/qdnaseq_local_pon/`: local-PoN files when two or more normal controls are used

## Research-use limitation

OncoTracer is not a standalone diagnostic system. Results require expert review, laboratory validation, and integration with pathology and orthogonal molecular evidence.
