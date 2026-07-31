# OncoTracer

![OncoTracer: sequencing reads to copy-number alterations](docs/assets/oncotracer-hero.png)

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://cfarkas.github.io/oncotracer/)
[![Docker](https://img.shields.io/badge/docker-carlosfarkas%2Foncotracer-blue)](https://hub.docker.com/r/carlosfarkas/oncotracer)
[![Nextflow](https://img.shields.io/badge/nextflow-%E2%89%A525.04-green)](https://www.nextflow.io/)

OncoTracer is a Nextflow research workflow for **low-pass whole-genome sequencing (LP-WGS)**. It accepts Illumina single-end or paired-end FASTQ files and Oxford Nanopore Technologies (ONT) FASTQ files, then produces copy-number alteration tables, plots, and reports.

```text
FASTQ -> SAMURAI qDNAseq/ichorCNA -> boundary refinement -> CNA tables -> plots and reports
```

Run OncoTracer through Nextflow with one container option:

- `--docker` uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).
- `--singularity` uses the same image as `docker://carlosfarkas/oncotracer:latest` on an HPC system configured with Singularity or Apptainer.

Read the [complete documentation](https://cfarkas.github.io/oncotracer/) for installation, tutorials, input formats, configuration, outputs, and troubleshooting.

## Requirements

Use Linux with [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git), [Java 17](https://adoptium.net/temurin/releases/?version=17) or newer, [Nextflow](https://www.nextflow.io/docs/latest/install.html), [Python 3](https://www.python.org/downloads/), [samtools](https://www.htslib.org/download/), [BWA](https://github.com/lh3/bwa), [minimap2](https://github.com/lh3/minimap2), [pigz](https://zlib.net/pigz/), [curl](https://curl.se/download.html) or [wget](https://www.gnu.org/software/wget/), and either [Docker Engine](https://docs.docker.com/engine/install/) or [SingularityCE](https://docs.sylabs.io/guides/latest/admin-guide/installation.html)/[Apptainer](https://apptainer.org/docs/admin/main/installation.html).

The first uncached analysis downloads the hg38 reference (about **3.16 GB**) and creates a BWA index. This commonly takes **30–60 minutes**, and the pinned BWA task requests 72 GB, so provide at least 80 GiB of addressable RAM. Later runs reuse a valid index.

## Run your own FASTQs

Keep sequencing data and results outside the Git clone. For Illumina, create a small sample table whose names match the FASTQ filenames:

```csv
sample_name,status
TUMOR_01,TUMOR
TUMOR_02,TUMOR
CONTROL_01,NORMAL
CONTROL_02,NORMAL
```

Generate the configuration and run it:

```bash
# Choose generic locations for the repository and analysis project.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR=/path/to/my/directory/my_oncotracer_project

# Clone OncoTracer and enter the repository.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"
cd "$REPO_DIR"

# Generate an Illumina YAML and samplesheet from the FASTQ folder and sample table.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/fastq" \
  --sample_table "$PROJECT_DIR/input/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config" \
  --auto_outdir "$PROJECT_DIR/results"

# Run the generated configuration with Docker and retain resumable work files.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work" \
  -resume

# Read the workflow summary after the analysis finishes.
cat "$PROJECT_DIR/results/06_workflow_summary/workflow_summary.txt"
```

`--auto_params` checks the supported FASTQ layout and writes the YAML used by the second command. For ONT barcode folders, use `--mode ont`. See [Automatic Setup](https://cfarkas.github.io/oncotracer/auto_params/) for complete Illumina and ONT examples.

## QuickStart Example 1: one public Illumina and one public ONT sample

This verification downloads about **225 MB** of public reads and runs both branches.

```bash
# Choose a generic clone location and test directory.
REPO_DIR=/path/to/my/directory/oncotracer
TEST_ROOT="$REPO_DIR/test"

# Clone the repository and enter it.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"
cd "$REPO_DIR"

# Download and validate the public reads, then create both YAML files.
nextflow run "$REPO_DIR/main.nf" --make_test \
  --test_root "$TEST_ROOT"

# Run the Illumina example first.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$TEST_ROOT/configs/illumina.quickstart.yml" \
  -work-dir "$TEST_ROOT/work/illumina" \
  -resume

# Run the ONT example after the Illumina run finishes.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$TEST_ROOT/configs/ont.quickstart.yml" \
  -work-dir "$TEST_ROOT/work/ont" \
  -resume

# Verify the required outputs from both runs.
python3 "$REPO_DIR/examples/quickstart/verify_outputs.py" \
  --test-root "$TEST_ROOT"
```

See [QuickStart Example 1](https://cfarkas.github.io/oncotracer/quick_start/) for the generated sample mappings, YAML files, expected time, and output folders.

## QuickStart Example 2: three public HCC1143 libraries

The HCC1143 example downloads six public paired-end FASTQs. The following block exposes the complete `wget` download, file naming, sample-table creation, Automatic Setup, and analysis commands.

```bash
# Set the standard repository and HCC1143 data paths.
REPO_DIR=/path/to/my/directory/oncotracer
READS_DIR="$REPO_DIR/test/public/hcc1143_lpwgs"
mkdir -p "$READS_DIR"
cd "$REPO_DIR"

# Download HCC1143_DMSO read 1 and rename it for Automatic Setup.
if [[ ! -s "$READS_DIR/HCC1143_DMSO_R1.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_1.fastq.gz
  mv "$READS_DIR/SRR7085656_1.fastq.gz" \
    "$READS_DIR/HCC1143_DMSO_R1.fastq.gz"
fi

# Download HCC1143_DMSO read 2 and rename it for Automatic Setup.
if [[ ! -s "$READS_DIR/HCC1143_DMSO_R2.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_2.fastq.gz
  mv "$READS_DIR/SRR7085656_2.fastq.gz" \
    "$READS_DIR/HCC1143_DMSO_R2.fastq.gz"
fi

# Download HCC1143_BEZ235 read 1 and rename it for Automatic Setup.
if [[ ! -s "$READS_DIR/HCC1143_BEZ235_R1.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_1.fastq.gz
  mv "$READS_DIR/SRR7085655_1.fastq.gz" \
    "$READS_DIR/HCC1143_BEZ235_R1.fastq.gz"
fi

# Download HCC1143_BEZ235 read 2 and rename it for Automatic Setup.
if [[ ! -s "$READS_DIR/HCC1143_BEZ235_R2.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_2.fastq.gz
  mv "$READS_DIR/SRR7085655_2.fastq.gz" \
    "$READS_DIR/HCC1143_BEZ235_R2.fastq.gz"
fi

# Download HCC1143_TRAMETINIB read 1 and rename it for Automatic Setup.
if [[ ! -s "$READS_DIR/HCC1143_TRAMETINIB_R1.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_1.fastq.gz
  mv "$READS_DIR/SRR7085657_1.fastq.gz" \
    "$READS_DIR/HCC1143_TRAMETINIB_R1.fastq.gz"
fi

# Download HCC1143_TRAMETINIB read 2 and rename it for Automatic Setup.
if [[ ! -s "$READS_DIR/HCC1143_TRAMETINIB_R2.fastq.gz" ]]; then
  wget --continue --directory-prefix="$READS_DIR" \
    https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_2.fastq.gz
  mv "$READS_DIR/SRR7085657_2.fastq.gz" \
    "$READS_DIR/HCC1143_TRAMETINIB_R2.fastq.gz"
fi

# Create or replace the exact HCC1143 sample table.
cat > "$READS_DIR/samples.csv" <<'CSV'
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
CSV

# Display the saved sample table before continuing.
cat "$READS_DIR/samples.csv"

# Generate the Illumina YAML and R1/R2 samplesheet automatically.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$READS_DIR" \
  --sample_table "$READS_DIR/samples.csv" \
  --auto_config_dir "$REPO_DIR/test/configs/hcc1143_lpwgs" \
  --auto_outdir "$REPO_DIR/test/runs/hcc1143_lpwgs"

# Run the generated HCC1143 configuration with Docker.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "$REPO_DIR/test/work/hcc1143_lpwgs" \
  -resume
```

Each `wget --continue` command can resume an accession-named partial download. After completion, the file is renamed to the sample prefix expected by Automatic Setup. The detailed [QuickStart Example 2](https://cfarkas.github.io/oncotracer/public_cohort/) also shows MD5, gzip, output, and resume checks.

## Other Example Runs

[Six tumors and four controls](https://cfarkas.github.io/oncotracer/six_tumor_four_control/) is a command template for a local qDNAseq panel of normals. The repository does **not** include or download the `ONCO001`–`ONCO006` and `CTRL001`–`CTRL004` FASTQs. That page will not run until you provide the 20 files and the sample table shown there.

The [Full Tutorial](https://cfarkas.github.io/oncotracer/full_tutorial/) downloads and processes all 12 public PRJNA754199 libraries currently available from the archive.

## Normal controls

For Illumina, Automatic Setup disables the local panel of normals when there are no `NORMAL` rows, rejects exactly one normal, and enables the panel when there are at least two controls. Corrected CNA outputs contain tumor samples; controls remain reference and quality-control inputs. See [Illumina setup](https://cfarkas.github.io/oncotracer/configuration/illumina/) and [output files](https://cfarkas.github.io/oncotracer/outputs/#illumina-local-panel-of-normals).

## Main outputs

- `06_workflow_summary/workflow_summary.txt`: first file to open after a run
- `03_cna_codification/cna_events.tsv`: CNA event table
- `03_cna_codification/cna_cytogenomic_notation.tsv`: cytogenomic notation
- `04_cna_custom_plots/cna_per_sample_pages.pdf`: per-sample plots
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`: cohort plot
- `01_samurai_illumina/qdnaseq_local_pon/`: local-PoN files when two or more Illumina normal controls are used

## Research-use limitation

OncoTracer is not a standalone diagnostic system. Results require expert review, laboratory validation, and integration with pathology and orthogonal molecular evidence.
