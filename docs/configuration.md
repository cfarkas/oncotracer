# Choose how to configure a run

Most analyses need one YAML file. Use this page to choose the shortest setup route.

## Which route should I choose?

| Your goal | Start here | Data used |
| --- | --- | --- |
| Verify the installation | [QuickStart Example 1](quick_start.md) | Public Illumina and ONT data downloaded by the tutorial |
| Run the three-library HCC1143 example | [QuickStart Example 2](public_cohort.md) | Six public FASTQs downloaded from ENA |
| Run the complete public archive tutorial | [Full Tutorial](full_tutorial.md) | Twelve public PRJNA754199 FASTQs |
| Configure your own standard FASTQ folder | [Automatic Setup](auto_params.md) | Your own Illumina files or ONT barcode folders |
| See a six-tumor/four-control template | [Other Example Run](six_tumor_four_control.md) | **Not included**; the user must provide all 20 FASTQs |
| Write an Illumina YAML manually | [Illumina manual setup](configuration/illumina.md#second-option-manual-setup) | Your own samplesheet and FASTQs |
| Write an ONT YAML manually | [ONT manual setup](configuration/ont.md) | Your own barcode folders and mapping table |
| Add pathology or classifier settings | [Pathology and classifier](configuration/pathology.md) | Your own matched sequencing and pathology tables |
| Change boundary refinement | [Advanced refinement](configuration/refinement.md) | A justified non-default analysis |
| Look up one field or default | [All parameters](configuration/parameter_reference.md) | Reference only |

For a standard Illumina or ONT layout, use Automatic Setup. Edit a YAML manually only when the supported naming rules do not fit the study or when a non-default option is required.

For Illumina, zero `NORMAL` rows run without a local panel of normals, one normal is rejected, and two or more normals enable the local qDNAseq reference.

## One YAML controls one run

A YAML is a plain-text list of paths and settings. It does not contain sequencing reads.

```yaml
mode: illumina
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results
illumina_samplesheet: /path/to/my/directory/oncotracer/project/config/illumina.samplesheet.csv
```

Keep the input, output, work, reference, and cache paths under a project root that the selected container can access.

## Recommended route: Automatic Setup

Create a sample table and point `--auto_params` at the reads folder. Automatic Setup writes the YAML and, for Illumina, the FASTQ samplesheet.

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Generate an Illumina YAML and samplesheet from a standard FASTQ folder.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/fastq" \
  --sample_table "$PROJECT_DIR/input/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config" \
  --auto_outdir "$PROJECT_DIR/results"

# Run the generated configuration with Docker.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work" \
  -resume
```

See [Automatic Setup](auto_params.md) for exact Illumina and ONT sample tables.

## Second option: manual YAML

### 1. Copy a template

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Copy the Illumina template to an editable file.
cp "$REPO_DIR/params/illumina.minimal.yml" "$REPO_DIR/params/my_illumina.yml"

# For ONT, copy the ONT template instead.
# cp "$REPO_DIR/params/ont.minimal.yml" "$REPO_DIR/params/my_ont.yml"
```

Do not edit the versioned template directly.

### 2. Resolve the project paths

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Print the absolute repository and project paths.
realpath "$REPO_DIR"
realpath "$REPO_DIR/project"
realpath "$REPO_DIR/project/input"
```

Use absolute paths in the YAML.

### 3. Edit the YAML

```bash
# Open the copied Illumina YAML.
nano /path/to/my/directory/oncotracer/params/my_illumina.yml
```

Save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`. Do not use tabs.

### 4. Check the workflow wiring

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Validate parameters and workflow connections without running the analysis tools.
nextflow run "$REPO_DIR/main.nf" -stub-run --docker \
  -params-file "$REPO_DIR/params/my_illumina.yml"
```

### 5. Run the analysis

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Run or resume the manual Illumina YAML with Docker.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/params/my_illumina.yml" \
  -resume
```

For HPC, replace `--docker` with `--singularity`.

## Runtime options

| Option | Use when | Image |
| --- | --- | --- |
| `--docker` | Docker is installed on a Linux workstation or server | [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer) |
| `--singularity` | Singularity or Apptainer is configured on HPC | `docker://carlosfarkas/oncotracer:latest` |
| `--conda` | No container runtime is available | Conda environments prepared by Nextflow |

Use exactly one runtime option on an analysis command.

## Settings to leave unchanged for a first run

Keep the caller, analysis type, bin size, and refinement defaults from the generated YAML or selected template. Do not add internal SAMURAI output paths; OncoTracer derives stage 01 from `outdir`.

Use a new YAML and a new `outdir` when changing the scientific configuration. This keeps the original results separate and easier to compare.
