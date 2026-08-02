# Run OncoTracer

Run commands from the cloned repository directory containing `main.nf`.

Choose one execution option:

- `--conda` makes Nextflow create and reuse the required Conda environments automatically.
- `--docker` uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).
- `--singularity` uses the same image as `docker://carlosfarkas/oncotracer:latest` on a configured HPC system.

## Choose the shortest route

| Starting point | Route |
| --- | --- |
| Verify the installation with public data | [QuickStart Example 1](quick_start.md) |
| Standard Illumina FASTQs | [Automatic Setup](auto_params.md#illumina-step-by-step) |
| ONT barcode folders | [Automatic Setup](auto_params.md#ont-step-by-step) |
| Three public HCC1143 libraries | [QuickStart Example 2](public_cohort.md) |
| Twelve public PRJNA754199 libraries | [Full Tutorial](full_tutorial.md) |
| Mock six-tumor/four-normal analysis | [Other Example Run](six_tumor_four_control.md), illustrating a local qDNAseq panel of normals |
| Unsupported naming or advanced settings | Manual [Illumina YAML](configuration/illumina.md) or [ONT YAML](configuration/ont.md) |
| An existing checked YAML | [Run a YAML](#run-a-yaml) |

## 1. Enter the repository and select an execution environment

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Confirm Nextflow and Conda for a --conda run.
pwd
nextflow -version
conda --version
```

For Docker, check `command -v docker`. For HPC, check `command -v singularity` or `command -v apptainer`.

## 2. Recommended route: Automatic Setup

`--auto_params` validates the supported input layout and writes the YAML. It stops before the analysis. A second command runs that YAML.

### Illumina example

Input layout:

```text
/path/to/my/directory/oncotracer/project/input/fastq/
├── Patient_A_R1.fastq.gz
├── Patient_A_R2.fastq.gz
├── Control_A_R1.fastq.gz
├── Control_A_R2.fastq.gz
├── Control_B_R1.fastq.gz
├── Control_B_R2.fastq.gz
└── samples.csv
```

Create the exact sample table:

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
mkdir -p "$PROJECT_DIR/input/fastq"

# Create or replace the Illumina sample table.
cat > "$PROJECT_DIR/input/fastq/samples.csv" <<'CSV'
sample_name,status
Patient_A,TUMOR
Control_A,NORMAL
Control_B,NORMAL
CSV

# Display the saved table.
cat "$PROJECT_DIR/input/fastq/samples.csv"
```

Generate and run:

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Generate the Illumina YAML and samplesheet.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/fastq" \
  --sample_table "$PROJECT_DIR/input/fastq/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config/illumina" \
  --auto_outdir "$PROJECT_DIR/results/illumina" \
  -work-dir "$PROJECT_DIR/work/auto_params_illumina"

# Run the generated Illumina YAML with Conda.
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$PROJECT_DIR/config/illumina/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/illumina" \
  -resume
```

On the first `--conda` run, Nextflow creates the required environments. Replace `--conda` with `--docker` or `--singularity` to use a container runtime.

### ONT example

Input layout:

```text
/path/to/my/directory/oncotracer/project/input/fastq_pass/
├── barcode01/
│   └── reads.fastq.gz
├── barcode02/
│   └── reads.fastq.gz
└── samples.csv
```

Create the exact barcode table:

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
mkdir -p "$PROJECT_DIR/input/fastq_pass"

# Create or replace the ONT barcode table.
cat > "$PROJECT_DIR/input/fastq_pass/samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Control_A,NORMAL
CSV

# Display the saved table.
cat "$PROJECT_DIR/input/fastq_pass/samples.csv"
```

Generate and run:

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Generate the ONT YAML from the barcode folders and sample table.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode ont \
  --reads_folder "$PROJECT_DIR/input/fastq_pass" \
  --sample_table "$PROJECT_DIR/input/fastq_pass/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config/ont" \
  --auto_outdir "$PROJECT_DIR/results/ont" \
  -work-dir "$PROJECT_DIR/work/auto_params_ont"

# Run the generated ONT YAML with Conda.
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$PROJECT_DIR/config/ont/ont.auto.yml" \
  -work-dir "$PROJECT_DIR/work/ont" \
  -resume
```

Replace `--conda` with `--docker` or `--singularity` when using a container runtime.

## 3. Manual configuration

Use a manual YAML when Automatic Setup does not support the file naming or when a non-default option is needed.

Illumina:

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Copy the Illumina template and edit the copied YAML.
cp "$REPO_DIR/params/illumina.minimal.yml" "$REPO_DIR/params/my_illumina.yml"
nano "$REPO_DIR/params/my_illumina.yml"
```

ONT:

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Copy the ONT template and edit the copied YAML.
cp "$REPO_DIR/params/ont.minimal.yml" "$REPO_DIR/params/my_ont.yml"
nano "$REPO_DIR/params/my_ont.yml"
```

Use absolute paths under `lpwgs_root` and do not add internal SAMURAI output paths.

<a id="check-wiring-then-run"></a>

## Run a YAML

Optional stub check:

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Check parameters and workflow connections without running the analysis tools.
nextflow run "$REPO_DIR/main.nf" -stub-run --conda \
  -params-file "$REPO_DIR/params/my_illumina.yml"
```

Real run:

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Run or resume the checked Illumina YAML with Conda.
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$REPO_DIR/params/my_illumina.yml" \
  -resume
```

For ONT, replace the YAML path. Replace `--conda` with `--docker` or `--singularity` for the corresponding runtime.

## Workflow stages

| Stage | Purpose |
| --- | --- |
| `01_samurai_illumina` or `01_samurai_ont` | Align FASTQs and produce initial qDNAseq or ichorCNA segments |
| `02_bam_refinement` | Evaluate and refine broad CNA boundaries using BAM coverage |
| `03_cna_codification` | Create CNA event tables and cytogenomic notation |
| `04_cna_custom_plots` | Create per-sample and cohort plots |
| `05_cna_classifier` | Optional CNA-pattern and pathology research reports |
| `06_workflow_summary` | Record important result paths |

The outer display can remain at `RUN_*_SAMURAI (0 of 1)` while nested SAMURAI tasks are active. An uncached first run also downloads and indexes hg38; provide at least 80 GiB of addressable RAM.

## Verify a completed run

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
OUT="$REPO_DIR/project/results/illumina"

# Read the workflow summary.
cat "$OUT/06_workflow_summary/workflow_summary.txt"

# Confirm the main CNA table and PDF plots exist.
ls -lh "$OUT/03_cna_codification/cna_events.tsv"
ls -lh "$OUT/04_cna_custom_plots"/*.pdf
```

## Resume safely

Use the same YAML, `outdir`, and work directory:

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Resume the existing Illumina run with the same Conda environments.
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$PROJECT_DIR/config/illumina/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/illumina" \
  -resume
```

Do not delete the work directory or Conda cache before diagnosing an error. They contain the task logs and reusable environments used by `-resume`.

## Launch through Poetry

```bash
# Forward an existing run configuration through Poetry to Nextflow.
REPO_DIR=/path/to/my/directory/oncotracer
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file /path/to/my/directory/my_oncotracer_project/config/illumina.auto.yml \
  -work-dir /path/to/my/directory/my_oncotracer_project/work -resume
```
