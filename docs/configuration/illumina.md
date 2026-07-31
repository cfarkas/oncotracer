# Illumina Configuration

Use this route for single-end or paired-end Illumina FASTQs. OncoTracer aligns the reads, runs SAMURAI/qDNAseq, refines CNA boundaries, and creates tables, plots, and a workflow summary.

The examples use `/path/to/my/directory/oncotracer` as the repository path.

## Recommended: create the YAML automatically

Automatic Setup supports either:

- one `<sample>.fastq.gz` or `<sample>.fq.gz` file per sample; or
- one `<sample>_R1.fastq.gz` and `<sample>_R2.fastq.gz` pair per sample.

Do not mix single-end and paired-end libraries in one run.

### 1. Arrange the FASTQs

```text
/path/to/my/directory/oncotracer/project/input/illumina_fastq/
├── Patient_A_R1.fastq.gz
├── Patient_A_R2.fastq.gz
├── Patient_B_R1.fastq.gz
├── Patient_B_R2.fastq.gz
├── Control_A_R1.fastq.gz
├── Control_A_R2.fastq.gz
├── Control_B_R1.fastq.gz
└── Control_B_R2.fastq.gz
```

### 2. Create the sample table

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
mkdir -p "$PROJECT_DIR/input/illumina_fastq"

# Create or replace the Illumina sample table.
cat > "$PROJECT_DIR/input/illumina_fastq/samples.csv" <<'CSV'
sample_name,status
Patient_A,TUMOR
Patient_B,TUMOR
Control_A,NORMAL
Control_B,NORMAL
CSV

# Display the saved table.
cat "$PROJECT_DIR/input/illumina_fastq/samples.csv"
```

`sample_name` must match the FASTQ filename prefix exactly. Zero normal rows run without a local panel, one normal is rejected, and two or more normals enable the local qDNAseq reference.

### 3. Generate the configuration

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
cd "$REPO_DIR"

# Generate the Illumina YAML and FASTQ samplesheet.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/illumina_fastq" \
  --sample_table "$PROJECT_DIR/input/illumina_fastq/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config/illumina" \
  --auto_outdir "$PROJECT_DIR/results/illumina" \
  -work-dir "$PROJECT_DIR/work/auto_params_illumina"
```

Automatic Setup validates every gzip file and writes:

```text
/path/to/my/directory/oncotracer/project/config/illumina/
├── auto_params_manifest.tsv
├── illumina.auto.yml
└── illumina.samplesheet.csv
```

It does not start the analysis.

### 4. Inspect and run

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Inspect the generated files.
sed -n '1,140p' "$PROJECT_DIR/config/illumina/illumina.auto.yml"
sed -n '1,30p' "$PROJECT_DIR/config/illumina/illumina.samplesheet.csv"
cat "$PROJECT_DIR/config/illumina/auto_params_manifest.tsv"

# Run the generated YAML with Docker.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$PROJECT_DIR/config/illumina/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/illumina" \
  -resume

# Read the completed workflow summary.
cat "$PROJECT_DIR/results/illumina/06_workflow_summary/workflow_summary.txt"
```

On HPC, replace `--docker` with `--singularity`.

## Second option: manual setup

Use manual setup when the filenames do not match the supported automatic patterns or when advanced settings are required.

### 1. Create the samplesheet

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
mkdir -p "$PROJECT_DIR/input"

# Create or replace a paired-end Illumina samplesheet.
cat > "$PROJECT_DIR/input/illumina.samplesheet.csv" <<CSV
sample,fastq_1,fastq_2,status
Patient_A,$PROJECT_DIR/input/illumina_fastq/Patient_A_R1.fastq.gz,$PROJECT_DIR/input/illumina_fastq/Patient_A_R2.fastq.gz,tumor
Patient_B,$PROJECT_DIR/input/illumina_fastq/Patient_B_R1.fastq.gz,$PROJECT_DIR/input/illumina_fastq/Patient_B_R2.fastq.gz,tumor
Control_A,$PROJECT_DIR/input/illumina_fastq/Control_A_R1.fastq.gz,$PROJECT_DIR/input/illumina_fastq/Control_A_R2.fastq.gz,normal
Control_B,$PROJECT_DIR/input/illumina_fastq/Control_B_R1.fastq.gz,$PROJECT_DIR/input/illumina_fastq/Control_B_R2.fastq.gz,normal
CSV

# Display the saved samplesheet.
cat "$PROJECT_DIR/input/illumina.samplesheet.csv"
```

For a single-end run, keep the four-column header and leave `fastq_2` empty for every row.

### 2. Copy and edit the YAML

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Copy the minimal template and edit the copy.
cp "$REPO_DIR/params/illumina.minimal.yml" "$REPO_DIR/params/my_illumina.yml"
nano "$REPO_DIR/params/my_illumina.yml"
```

A tumor-plus-controls YAML can contain:

```yaml
mode: illumina
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results/manual_illumina
illumina_samplesheet: /path/to/my/directory/oncotracer/project/input/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
illumina_build_pon: true
illumina_pon_normal_samples: Control_A,Control_B
illumina_pon_min_normals: 2
illumina_pon_name: Control_A_Control_B_PoN
illumina_pon_min_mapq: 37
illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1
force: false
```

### How the local PoN is built

All tumor and normal BAMs use the same alignment stage, bin definition, paired-read setting, and MAPQ threshold. qDNAseq computes the per-bin median signal across the selected controls and subtracts that reference from each tumor profile. Corrected bins, segments, and plots contain tumors only.

The normal list must contain every and only samplesheet row marked `normal`. At least two controls are required. Review `qc/normal_panel_sample_qc.tsv` before interpreting corrected calls.

### 3. Check and run the manual YAML

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Check workflow connections without running the scientific tools.
nextflow run "$REPO_DIR/main.nf" -stub-run --docker \
  -params-file "$REPO_DIR/params/my_illumina.yml"

# Run or resume the manual configuration.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/params/my_illumina.yml" \
  -resume
```

## Main Illumina settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `illumina_analysis_type` | `solid_biopsy` | SAMURAI analysis preset |
| `illumina_caller` | `qdnaseq` | Illumina CNA caller |
| `illumina_binsize_kb` | `100` | Initial bin size in kilobases |
| `illumina_build_pon` | `false` | Enable the local qDNAseq normal reference |
| `illumina_pon_normal_samples` | none | Exact comma-separated normal IDs |
| `illumina_pon_min_normals` | `2` | Minimum required controls |
| `force` | `false` | Keep false for normal project runs |

See [All parameters](parameter_reference.md) for advanced options.
