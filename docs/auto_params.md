# Automatic Setup from a Reads Folder

`--auto_params` is the recommended way to configure your own FASTQs. You provide:

1. a reads folder; and
2. a small CSV that maps each sample to `TUMOR` or `NORMAL`.

OncoTracer validates the supported layout and writes a YAML file. For Illumina, it also writes the FASTQ samplesheet. Automatic Setup stops after creating those files; the second Nextflow command starts the analysis.

The generated YAML can be run in four ways:

- Docker with `nextflow run ... --docker`;
- Singularity or Apptainer with `nextflow run ... --singularity`;
- the Poetry launcher with `poetry run oncotracer --backend docker ...`; or
- native Conda environments with `nextflow run ... --conda`.

![Example OncoTracer input layouts](assets/tutorial/auto_params_folder_layout.svg)

## Before you begin

Use `/path/to/my/directory/oncotracer` as the repository path in these examples.

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Clone OncoTracer when it is not already installed.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"

# Enter the repository and confirm Nextflow is available.
cd "$REPO_DIR"
nextflow -version
```

## Why Automatic Setup creates a YAML

The sample table contains names and roles. Automatic Setup matches those names to FASTQs, checks the files, and stores the paths and analysis settings in a readable YAML. The real analysis command reads that YAML with `-params-file`.

```text
FASTQs + samples.csv
        |
        v
nextflow run main.nf --auto_params
        |
        +-- generated YAML
        +-- generated Illumina samplesheet, when mode=illumina
        +-- sample-count and checksum manifest
        |
        v
nextflow run main.nf --docker|--singularity|--conda -params-file generated.yml -resume
        or poetry run oncotracer --backend docker -params-file generated.yml -resume
```

## Illumina step by step

### 1. Organize the FASTQs

For paired-end data, keep one R1 and one R2 file per sample directly inside one folder:

```text
/path/to/my/directory/oncotracer/project/input/fastq/
├── TUMOR_01_R1.fastq.gz
├── TUMOR_01_R2.fastq.gz
├── TUMOR_02_R1.fastq.gz
├── TUMOR_02_R2.fastq.gz
├── CONTROL_01_R1.fastq.gz
├── CONTROL_01_R2.fastq.gz
├── CONTROL_02_R1.fastq.gz
└── CONTROL_02_R2.fastq.gz
```

Supported paired names include `<sample>_R1.fastq.gz` and `<sample>_R2.fastq.gz`, or `<sample>_1.fastq.gz` and `<sample>_2.fastq.gz`. The same patterns may end in `.fq.gz`.

For single-end data, use one `<sample>.fastq.gz` or `<sample>.fq.gz` file per sample. Do not mix single-end and paired-end libraries in one Automatic Setup command. Automatic Setup does not combine lane files recursively.

### 2. Create the sample table

Use this copy/paste-ready command. It creates or replaces the CSV instead of appending duplicate rows.

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
mkdir -p "$PROJECT_DIR/input/fastq"

# Create the Illumina sample table.
cat > "$PROJECT_DIR/input/samples.csv" <<'CSV'
sample_name,status
TUMOR_01,TUMOR
TUMOR_02,TUMOR
CONTROL_01,NORMAL
CONTROL_02,NORMAL
CSV

# Display the saved table.
cat "$PROJECT_DIR/input/samples.csv"
```

The filename prefix must match `sample_name` exactly. `status` must be `TUMOR` or `NORMAL`.

Normal-control behavior is:

- zero `NORMAL` rows: no local panel of normals;
- one `NORMAL` row: configuration stops with an error;
- two or more `NORMAL` rows: Automatic Setup enables the local qDNAseq reference.

### 3. Generate the Illumina YAML and samplesheet

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
cd "$REPO_DIR"

# Generate the Illumina configuration without starting the analysis.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/fastq" \
  --sample_table "$PROJECT_DIR/input/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config" \
  --auto_outdir "$PROJECT_DIR/results" \
  -work-dir "$PROJECT_DIR/work/auto_params"
```

Automatic Setup runs `gzip -t` on every compressed FASTQ. It stops when a file is missing, corrupt, ambiguous, or part of a mixed single-end/paired-end layout.

The generated directory contains:

```text
/path/to/my/directory/oncotracer/project/config/
├── auto_params_manifest.tsv
├── illumina.auto.yml
└── illumina.samplesheet.csv
```

### 4. Inspect the generated files

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Inspect the analysis settings.
sed -n '1,140p' "$PROJECT_DIR/config/illumina.auto.yml"

# Inspect the FASTQ-to-sample mapping.
sed -n '1,30p' "$PROJECT_DIR/config/illumina.samplesheet.csv"

# Inspect the sample counts and file hashes.
cat "$PROJECT_DIR/config/auto_params_manifest.tsv"
```

For the table above, the generated YAML includes:

```yaml
mode: illumina
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results
illumina_samplesheet: /path/to/my/directory/oncotracer/project/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
illumina_build_pon: true
illumina_pon_normal_samples: "CONTROL_01,CONTROL_02"
illumina_pon_min_normals: 2
illumina_pon_name: CONTROL_01_CONTROL_02_PoN
illumina_pon_min_mapq: 37
illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1
run_cna_classifier: false
force: false
```

The generated samplesheet contains absolute FASTQ paths:

```csv
sample,fastq_1,fastq_2,status
TUMOR_01,/path/to/my/directory/oncotracer/project/input/fastq/TUMOR_01_R1.fastq.gz,/path/to/my/directory/oncotracer/project/input/fastq/TUMOR_01_R2.fastq.gz,tumor
TUMOR_02,/path/to/my/directory/oncotracer/project/input/fastq/TUMOR_02_R1.fastq.gz,/path/to/my/directory/oncotracer/project/input/fastq/TUMOR_02_R2.fastq.gz,tumor
CONTROL_01,/path/to/my/directory/oncotracer/project/input/fastq/CONTROL_01_R1.fastq.gz,/path/to/my/directory/oncotracer/project/input/fastq/CONTROL_01_R2.fastq.gz,normal
CONTROL_02,/path/to/my/directory/oncotracer/project/input/fastq/CONTROL_02_R1.fastq.gz,/path/to/my/directory/oncotracer/project/input/fastq/CONTROL_02_R2.fastq.gz,normal
```

### 5. Run the Illumina analysis

Choose exactly one method.

#### Docker

```bash
# Run the generated Illumina YAML with Docker.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/docker" \
  -resume
```

#### Singularity or Apptainer

```bash
# Run the same YAML through Singularity or Apptainer.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
nextflow run "$REPO_DIR/main.nf" --singularity \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/singularity" \
  -resume
```

#### Poetry launcher

```bash
# Install the launcher and run the same YAML through Poetry with Docker.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
cd "$REPO_DIR"
poetry install --no-interaction
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/poetry" \
  -resume
```

#### Conda

```bash
# Create or reuse the native environments and run the same YAML.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/conda" \
  -resume
```

### 6. Check a local panel of normals

When two or more controls are used, verify the control list, control QC, tumor-only corrected files, and completion marker:

```bash
# Set the standard project and result paths.
REPO_DIR=/path/to/my/directory/oncotracer
OUT="$REPO_DIR/project/results"
PON="$OUT/01_samurai_illumina/qdnaseq_local_pon"

# Require the successful panel completion marker.
test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = QDNASEQ_LOCAL_PON_SUCCESS

# Inspect the selected controls and their leave-one-out QC.
sed -n '1,20p' "$PON/pon/normal_panel_manifest.tsv"
sed -n '1,20p' "$PON/qc/normal_panel_sample_qc.tsv"

# List the corrected tumor files.
find "$PON/bins" "$PON/segments" -maxdepth 1 -type f -print | sort
```

See [Output files](outputs.md#illumina-local-panel-of-normals) for the complete result list.

## ONT step by step

### 1. Organize barcode folders

Point `--reads_folder` at the `fastq_pass` directory. Each barcode folder must contain at least one FASTQ:

```text
/path/to/my/directory/oncotracer/project/fastq_pass/
├── barcode01/
│   └── reads_001.fastq.gz
├── barcode02/
│   └── reads_001.fastq.gz
└── barcode03/
    └── reads_001.fastq.gz
```

### 2. Create the barcode table

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
mkdir -p "$PROJECT_DIR/fastq_pass"

# Create the explicit barcode-to-sample table.
cat > "$PROJECT_DIR/fastq_pass/samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,TUMOR_01,TUMOR
barcode02,CONTROL_01,NORMAL
barcode03,TUMOR_02,TUMOR
CSV

# Display the saved table.
cat "$PROJECT_DIR/fastq_pass/samples.csv"
```

The barcode value must match the directory name exactly. The explicit three-column form is recommended because it is easy to review.

### 3. Generate the ONT YAML

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
cd "$REPO_DIR"

# Generate the ONT configuration without starting the analysis.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode ont \
  --reads_folder "$PROJECT_DIR/fastq_pass" \
  --sample_table "$PROJECT_DIR/fastq_pass/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config_ont" \
  --auto_outdir "$PROJECT_DIR/results_ont" \
  -work-dir "$PROJECT_DIR/work/auto_params_ont"
```

### 4. Inspect and run the ONT YAML

```bash
# Inspect the generated ONT settings and manifest.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
sed -n '1,140p' "$PROJECT_DIR/config_ont/ont.auto.yml"
cat "$PROJECT_DIR/config_ont/auto_params_manifest.tsv"
```

Choose exactly one method.

#### Docker

```bash
# Run the generated ONT YAML with Docker.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$PROJECT_DIR/config_ont/ont.auto.yml" \
  -work-dir "$PROJECT_DIR/work/ont-docker" \
  -resume
```

#### Singularity or Apptainer

```bash
# Run the generated ONT YAML through Singularity or Apptainer.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
nextflow run "$REPO_DIR/main.nf" --singularity \
  -params-file "$PROJECT_DIR/config_ont/ont.auto.yml" \
  -work-dir "$PROJECT_DIR/work/ont-singularity" \
  -resume
```

#### Poetry launcher

```bash
# Install the launcher and run the ONT YAML through Poetry with Docker.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
cd "$REPO_DIR"
poetry install --no-interaction
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file "$PROJECT_DIR/config_ont/ont.auto.yml" \
  -work-dir "$PROJECT_DIR/work/ont-poetry" \
  -resume
```

#### Conda

```bash
# Create or reuse the native environments and run the ONT YAML.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$PROJECT_DIR/config_ont/ont.auto.yml" \
  -work-dir "$PROJECT_DIR/work/ont-conda" \
  -resume
```

## Common setup errors

- The sample name does not match the FASTQ prefix.
- More than one R1 or R2 file matches the same sample.
- Single-end and paired-end Illumina files are mixed.
- A compressed FASTQ fails `gzip -t`.
- An ONT barcode in the table does not match a directory.
- Exactly one Illumina `NORMAL` row was supplied.
- Reads, configuration, and output paths do not share a usable project root.

Fix the input layout or table, rerun `--auto_params`, inspect the generated files, and then run the analysis YAML.
