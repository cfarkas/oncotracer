# Other Example Run: Six Illumina Tumors and Four Controls

This page is a **command template**, not a QuickStart. The repository does **not** include or download the `ONCO001`–`ONCO006` and `CTRL001`–`CTRL004` FASTQs. Nothing on this page can run until you provide all 20 paired-end files.

The example shows how Automatic Setup generates a local qDNAseq panel of normals from four controls and applies it to six tumors. Corrected CNA outputs contain the six tumors; controls remain reference and quality-control inputs.

The Docker commands use [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer). On an HPC system configured with Singularity or Apptainer, use `--singularity` instead of `--docker`.

## Estimated resources

The first uncached run downloads hg38 and creates a BWA index. The pinned BWA task requests 72 GB, so provide at least 80 GiB of addressable RAM. This example gives Nextflow a 20-logical-CPU view and does not request a GPU. Actual time and storage depend on the size of your FASTQs.

## 1. Set the paths and check the programs

Use `/path/to/my/directory/oncotracer` for the repository. Keep the local sequencing project outside the Git clone.

```bash
# Set the standard repository and local-project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4

# Enter the cloned repository.
cd "$REPO_DIR"

# Confirm the required launchers.
nextflow -version
command -v docker
command -v taskset

# Display the logical CPUs currently allowed for this shell.
taskset -pc $$
```

Continue with CPU IDs `0-19` only when those IDs belong to your allocation. Otherwise replace every `0-19` below with 20 CPUs assigned by your scheduler or administrator.

## 2. Arrange the 20 FASTQ files

```bash
# Set the local-project path and create its directories.
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4
mkdir -p \
  "$PROJECT_DIR/input/fastq" \
  "$PROJECT_DIR/config" \
  "$PROJECT_DIR/results" \
  "$PROJECT_DIR/work"
```

Place exactly one R1/R2 pair for each sample directly in `input/fastq`:

```text
/path/to/my/directory/oncotracer_projects/onco6_ctrl4/
├── input/
│   ├── samples.csv
│   └── fastq/
│       ├── ONCO001_R1.fastq.gz
│       ├── ONCO001_R2.fastq.gz
│       ├── ONCO002_R1.fastq.gz
│       ├── ONCO002_R2.fastq.gz
│       ├── ONCO003_R1.fastq.gz
│       ├── ONCO003_R2.fastq.gz
│       ├── ONCO004_R1.fastq.gz
│       ├── ONCO004_R2.fastq.gz
│       ├── ONCO005_R1.fastq.gz
│       ├── ONCO005_R2.fastq.gz
│       ├── ONCO006_R1.fastq.gz
│       ├── ONCO006_R2.fastq.gz
│       ├── CTRL001_R1.fastq.gz
│       ├── CTRL001_R2.fastq.gz
│       ├── CTRL002_R1.fastq.gz
│       ├── CTRL002_R2.fastq.gz
│       ├── CTRL003_R1.fastq.gz
│       ├── CTRL003_R2.fastq.gz
│       ├── CTRL004_R1.fastq.gz
│       └── CTRL004_R2.fastq.gz
├── config/
├── results/
└── work/
```

Automatic Setup expects one pair per sample in the top level of the FASTQ directory. It does not combine lane files recursively.

```bash
# Set the local-project path.
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4

# Confirm that the input directory contains exactly 20 compressed FASTQs.
find "$PROJECT_DIR/input/fastq" \
  -maxdepth 1 -type f -name '*.fastq.gz' | wc -l
```

The command must print `20`.

## 3. Create the tumor/normal table

Use this copy/paste-ready command. It creates or replaces the table instead of appending duplicate rows.

```bash
# Set the local-project path.
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4

# Create the exact six-tumor/four-control table.
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

# Display the saved table.
cat "$PROJECT_DIR/input/samples.csv"
```

`sample_name` must match the text before `_R1` and `_R2`. Automatic Setup uses the four `NORMAL` rows to generate the local panel settings.

## 4. Set the CPU and GPU environment

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Hide GPU devices because this example does not request a GPU.
export CUDA_VISIBLE_DEVICES=""
export NVIDIA_VISIBLE_DEVICES=none
export SINGULARITYENV_CUDA_VISIBLE_DEVICES=""
export APPTAINERENV_CUDA_VISIBLE_DEVICES=""

# Give each Nextflow controller a 20-CPU view and an 8 GiB JVM heap limit.
export NXF_OPTS="-XX:ActiveProcessorCount=20 -Xms512m -Xmx8g"
```

`-Xmx8g` limits the Nextflow controller JVM, not the total analysis memory. Alignment and qDNAseq use additional RAM.

## 5. Generate the YAML automatically

`--auto_params` checks the 20 FASTQs, matches them to the sample table, writes `illumina.samplesheet.csv`, and writes `illumina.auto.yml`. It does not align reads or call CNAs.

```bash
# Set the standard repository and local-project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4

# Validate the FASTQs and generate the Illumina YAML and samplesheet.
taskset --cpu-list 0-19 nextflow run "$REPO_DIR/main.nf" \
  --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/fastq" \
  --sample_table "$PROJECT_DIR/input/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config" \
  --auto_outdir "$PROJECT_DIR/results" \
  -work-dir "$PROJECT_DIR/work/auto_params"
```

Continue only after `AUTO_PARAMS SUCCESS` appears.

```bash
# Set the local-project path.
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4

# Inspect the generated YAML.
sed -n '1,120p' "$PROJECT_DIR/config/illumina.auto.yml"

# Inspect the generated FASTQ-to-sample mapping.
sed -n '1,20p' "$PROJECT_DIR/config/illumina.samplesheet.csv"

# Inspect sample counts and file hashes.
cat "$PROJECT_DIR/config/auto_params_manifest.tsv"
```

The YAML must include:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: "CTRL001,CTRL002,CTRL003,CTRL004"
illumina_pon_min_normals: 4
illumina_pon_name: CTRL001_CTRL002_CTRL003_CTRL004_PoN
illumina_pon_min_mapq: 37
illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1
```

```bash
# Set the local-project path.
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4

# Count the generated tumor rows.
grep -c ',tumor$' "$PROJECT_DIR/config/illumina.samplesheet.csv"

# Count the generated normal rows.
grep -c ',normal$' "$PROJECT_DIR/config/illumina.samplesheet.csv"
```

The commands must print `6` and `4`.

## 6. Run the analysis

### Docker

```bash
# Set the standard repository and local-project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4

# Run the generated configuration with Docker and resume support.
taskset --cpu-list 0-19 nextflow run "$REPO_DIR/main.nf" \
  --docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/analysis" \
  -resume
```

### Singularity or Apptainer

```bash
# Set the standard repository and local-project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4

# Run the same configuration through the HPC container option.
taskset --cpu-list 0-19 nextflow run "$REPO_DIR/main.nf" \
  --singularity \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/analysis" \
  -resume
```

Keep the terminal open until Nextflow returns to the prompt. Do not start a second copy of the same analysis while it is active.

## 7. Resume an interrupted run

Restore the environment variables from step 4, then use the same YAML and work directory:

```bash
# Set the standard repository and local-project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4

# Resume the existing Docker analysis.
taskset --cpu-list 0-19 nextflow run "$REPO_DIR/main.nf" \
  --docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/analysis" \
  -resume
```

## 8. Verify the panel and tumor outputs

```bash
# Set the local-project and result paths.
PROJECT_DIR=/path/to/my/directory/oncotracer_projects/onco6_ctrl4
OUT="$PROJECT_DIR/results"
PON="$OUT/01_samurai_illumina/qdnaseq_local_pon"

# Require the successful panel completion marker.
test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = QDNASEQ_LOCAL_PON_SUCCESS \
  && echo "PoN completed successfully"

# Inspect the four-control manifest and leave-one-out control QC.
sed -n '1,10p' "$PON/pon/normal_panel_manifest.tsv"
sed -n '1,10p' "$PON/qc/normal_panel_sample_qc.tsv"

# List the corrected tumor bin files.
find "$PON/bins" -maxdepth 1 -type f -name '*_markdup_bins.bed' -printf '%f\n' | sort

# Read the final workflow summary.
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

The control files must contain `CTRL001` through `CTRL004`. Corrected tumor files must contain `ONCO001` through `ONCO006` and no controls.

Four controls form a small, run-specific reference. Review the control QC before interpreting CNA calls. OncoTracer is for research use and is not a standalone diagnostic system.
