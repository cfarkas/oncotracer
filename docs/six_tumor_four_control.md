# Other Example Run: Mock Six-Tumor/Four-Normal Study

This mock example illustrates how OncoTracer uses four `NORMAL` samples to build a local qDNAseq panel of normals and applies that reference to six `TUMOR` samples. The names `ONCO001`–`ONCO006` and `CTRL001`–`CTRL004` are placeholders used to demonstrate the configuration.

Corrected CNA outputs contain the six tumors. The four controls remain reference and quality-control inputs.

## 1. Clone OncoTracer

Start from a fresh clone. In the commands below, the only path that needs to be edited is `/path/to/my/directory/oncotracer`; every other path is derived from `REPO_DIR`.

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Clone OncoTracer and enter the repository.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"
cd "$REPO_DIR"
```

## 2. Prepare the mock project

Automatic Setup expects one R1/R2 pair for each sample:

```text
ONCO001_R1.fastq.gz       ONCO001_R2.fastq.gz
ONCO002_R1.fastq.gz       ONCO002_R2.fastq.gz
ONCO003_R1.fastq.gz       ONCO003_R2.fastq.gz
ONCO004_R1.fastq.gz       ONCO004_R2.fastq.gz
ONCO005_R1.fastq.gz       ONCO005_R2.fastq.gz
ONCO006_R1.fastq.gz       ONCO006_R2.fastq.gz
CTRL001_R1.fastq.gz       CTRL001_R2.fastq.gz
CTRL002_R1.fastq.gz       CTRL002_R2.fastq.gz
CTRL003_R1.fastq.gz       CTRL003_R2.fastq.gz
CTRL004_R1.fastq.gz       CTRL004_R2.fastq.gz
```

The project is kept inside the clone so no second path needs to be edited:

```text
/path/to/my/directory/oncotracer/test/examples/onco6_ctrl4/
├── input/
│   ├── samples.csv
│   └── fastq/
├── config/
├── results/
└── work/
```

```bash
# Derive the mock-project path from the repository clone.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/test/examples/onco6_ctrl4"
mkdir -p "$PROJECT_DIR/input/fastq"

# Create or replace the exact six-tumor/four-normal sample table.
cat > "$PROJECT_DIR/input/samples.csv" <<'CSV'
sample_name,status
ONCO001,TUMOR
ONCO002,TUMOR
ONCO003,TUMOR
ONCO004,TUMOR
ONCO005,TUMOR
ONCO006,TUMOR
CTRL001,NORMAL
CTRL002,NORMAL
CTRL003,NORMAL
CTRL004,NORMAL
CSV

# Display the saved table and the expected FASTQ folder.
cat "$PROJECT_DIR/input/samples.csv"
printf 'Place the paired FASTQs in: %s\n' "$PROJECT_DIR/input/fastq"
```

`sample_name` must match the filename text before `_R1` and `_R2`. The four `NORMAL` rows tell Automatic Setup to enable the local qDNAseq reference.

## 3. Generate the YAML automatically

```bash
# Set the repository and mock-project paths and enter the clone.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/test/examples/onco6_ctrl4"
cd "$REPO_DIR"

# Validate the paired FASTQs and generate the YAML and samplesheet.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/fastq" \
  --sample_table "$PROJECT_DIR/input/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config" \
  --auto_outdir "$PROJECT_DIR/results" \
  -work-dir "$PROJECT_DIR/work/auto_params"
```

Automatic Setup writes ten sample rows and enables the local panel with settings equivalent to:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: "CTRL001,CTRL002,CTRL003,CTRL004"
illumina_pon_min_normals: 4
illumina_pon_name: CTRL001_CTRL002_CTRL003_CTRL004_PoN
illumina_pon_min_mapq: 37
```

## 4. Inspect the generated files

```bash
# Set the repository and mock-project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/test/examples/onco6_ctrl4"

# Review the generated run configuration and sample mapping.
sed -n '1,140p' "$PROJECT_DIR/config/illumina.auto.yml"
sed -n '1,20p' "$PROJECT_DIR/config/illumina.samplesheet.csv"
cat "$PROJECT_DIR/config/auto_params_manifest.tsv"

# Confirm the generated tumor and normal row counts.
grep -c ',tumor$' "$PROJECT_DIR/config/illumina.samplesheet.csv"
grep -c ',normal$' "$PROJECT_DIR/config/illumina.samplesheet.csv"
```

The final two commands should print `6` and `4`.

## 5. Run the mock configuration

Choose exactly one method. Each command reads the same generated YAML; route-specific work directories prevent cache mixing.

### Docker

```bash
# Run the mock configuration with the maintained Docker image.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/test/examples/onco6_ctrl4"
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/docker" \
  -resume
```

### Singularity or Apptainer

```bash
# Run the mock configuration through Singularity or Apptainer.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/test/examples/onco6_ctrl4"
nextflow run "$REPO_DIR/main.nf" --singularity \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/singularity" \
  -resume
```

### Poetry launcher

```bash
# Install the launcher and run the mock configuration through Poetry with Docker.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/test/examples/onco6_ctrl4"
cd "$REPO_DIR"
poetry install --no-interaction
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/poetry" \
  -resume
```

### Conda

```bash
# Create or reuse the native environments and run the mock configuration.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/test/examples/onco6_ctrl4"
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/conda" \
  -resume
```

The Poetry launcher also accepts `--backend singularity` and `--backend conda`.

## 6. Check the panel and corrected tumor outputs

```bash
# Set the repository, mock-project, and result paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/test/examples/onco6_ctrl4"
OUT="$PROJECT_DIR/results"
PON="$OUT/01_samurai_illumina/qdnaseq_local_pon"

# Require the successful local-panel completion marker.
test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = QDNASEQ_LOCAL_PON_SUCCESS \
  && echo "PoN completed successfully"

# Review the four-control manifest and leave-one-out control QC.
sed -n '1,10p' "$PON/pon/normal_panel_manifest.tsv"
sed -n '1,10p' "$PON/qc/normal_panel_sample_qc.tsv"

# List corrected tumor bins and read the workflow summary.
find "$PON/bins" -maxdepth 1 -type f -name '*_markdup_bins.bed' -printf '%f\n' | sort
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

The control manifest and QC contain `CTRL001` through `CTRL004`. Corrected tumor outputs contain `ONCO001` through `ONCO006`.

Four controls form a small, run-specific reference, so review the control QC before interpreting CNA calls. OncoTracer is for research use and is not a standalone diagnostic system.
