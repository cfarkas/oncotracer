# ONT Configuration

Use this route for Oxford Nanopore FASTQs organized in barcode directories. OncoTracer merges selected reads, aligns them with minimap2, runs SAMURAI/ichorCNA, refines CNA boundaries, and creates tables, plots, and a workflow summary.

The examples use paths relative to the cloned `oncotracer` directory.

## Recommended: create the YAML automatically

### 1. Arrange the FASTQs

Point `--reads_folder` at the parent `fastq_pass` directory:

```text
project/input/fastq_pass/
├── barcode01/
│   ├── reads_001.fastq.gz
│   └── reads_002.fastq.gz
└── barcode02/
    └── reads_001.fastq.gz
```

Each barcode can contain one or more `.fastq`, `.fq`, `.fastq.gz`, or `.fq.gz` files directly inside it.

### 2. Create the barcode table

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"
mkdir -p "$PROJECT_DIR/input/fastq_pass"

# Create or replace the explicit barcode-to-sample table.
cat > "$PROJECT_DIR/input/fastq_pass/samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Patient_B,NORMAL
CSV

# Display the saved table.
cat "$PROJECT_DIR/input/fastq_pass/samples.csv"
```

`barcode` must match a directory name exactly. At least one row must be `TUMOR`.

### 3. Generate the configuration

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"

# Generate the ONT YAML without starting the analysis.
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder "$PROJECT_DIR/input/fastq_pass" \
  --sample_table "$PROJECT_DIR/input/fastq_pass/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config/ont" \
  --auto_outdir "$PROJECT_DIR/results/ont" \
  -work-dir "$PROJECT_DIR/work/auto_params_ont"
```

Automatic Setup verifies each listed barcode and compressed FASTQ and writes `ont.auto.yml`. It does not start alignment or CNA analysis.

### 4. Inspect and run

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"

# Inspect the generated settings and audit manifest.
sed -n '1,160p' "$PROJECT_DIR/config/ont/ont.auto.yml"
cat "$PROJECT_DIR/config/ont/auto_params_manifest.tsv"

# Run the generated ONT YAML with Docker.
nextflow run main.nf --docker \
  -params-file "$PROJECT_DIR/config/ont/ont.auto.yml" \
  -work-dir "$PROJECT_DIR/work/ont" \
  -resume

# Read the completed workflow summary.
cat "$PROJECT_DIR/results/ont/06_workflow_summary/workflow_summary.txt"
```

On HPC, replace `--docker` with `--singularity`.

The generated YAML resembles:

```yaml
mode: ont
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results/ont
ont_folder: /path/to/my/directory/oncotracer/project/input/fastq_pass
ont_barcodes: barcode01
ont_sample_names: Patient_A
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
ont_normal_folder: /path/to/my/directory/oncotracer/project/input/fastq_pass
ont_normal_barcodes: barcode02
ont_normal_sample_names: Patient_B
run_cna_classifier: false
force: false
```

Tumor and normal barcode lists are positional: the first barcode maps to the first sample name.

## Second option: manual setup

Use a manual YAML when selecting only some barcodes, using a custom reference, or configuring advanced settings.

### 1. Copy and edit the template

```bash
# Copy the ONT template and edit the copy.
cp "params/ont.minimal.yml" "params/my_ont.yml"
nano "params/my_ont.yml"
```

A minimal two-tumor file is:

```yaml
mode: ont
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results/manual_ont
ont_folder: /path/to/my/directory/oncotracer/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
force: false
```

For a control barcode, add:

```yaml
ont_normal_folder: /path/to/my/directory/oncotracer/project/input/fastq_pass
ont_normal_barcodes: barcode03
ont_normal_sample_names: Patient_Normal
```

### 2. Check and run

```bash
# Check workflow connections without running the analysis tools.
nextflow run main.nf -stub-run --docker \
  -params-file "params/my_ont.yml"

# Run or resume the manual ONT YAML.
nextflow run main.nf --docker \
  -params-file "params/my_ont.yml" \
  -resume
```

## Main ONT settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `ont_folder` | required | Parent of barcode directories |
| `ont_barcodes` | required | Comma-separated tumor barcodes |
| `ont_sample_names` | none | Biological names in matching order |
| `ont_analysis_type` | `liquid_biopsy` | SAMURAI analysis preset |
| `ont_caller` | `ichorcna` | ONT CNA caller |
| `ont_binsize_kb` | `500` | Initial bin size in kilobases |
| `ont_min_age_minutes` | `0` | Minimum completed-file age |
| `ont_normal_*` | none | Optional normal/control folder and positional lists |
| `force` | `false` | Keep false for normal project runs |

See [All parameters](parameter_reference.md) for advanced options.
