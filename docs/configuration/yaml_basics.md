# YAML and Paths

A YAML file is a small plain-text run configuration. It tells OncoTracer which sequencing route to use, where the inputs are, and where results belong. FASTQ reads are not stored in YAML.

## Choose how to create the YAML

| Situation | Route |
| --- | --- |
| Standard Illumina FASTQs or ONT barcode folders | Automatic Setup with `--auto_params` |
| Custom samplesheet, custom reference, or advanced settings | Manual YAML editing |
| Public installation test | `--make_test`; see [QuickStart Example 1](../quick_start.md) |

The examples use paths relative to the cloned `oncotracer` directory.

## Recommended: Automatic Setup

For Illumina, first create a sample table:

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"
mkdir -p "$PROJECT_DIR/input/fastq"

# Create or replace the sample table.
cat > "$PROJECT_DIR/input/samples.csv" <<'CSV'
sample_name,status
Sample_A,TUMOR
Control_A,NORMAL
Control_B,NORMAL
CSV

# Display the saved table.
cat "$PROJECT_DIR/input/samples.csv"
```

Then generate the YAML and Illumina samplesheet:

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"

# Generate configuration files without starting the analysis.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/fastq" \
  --sample_table "$PROJECT_DIR/input/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config" \
  --auto_outdir "$PROJECT_DIR/results"
```

Automatic Setup validates the input files and writes the YAML, audit manifest, and Illumina samplesheet. The later analysis command reads the YAML with `-params-file`.

## YAML vocabulary

```yaml
mode: illumina                         # text value
illumina_binsize_kb: 100               # integer
run_cna_classifier: false              # Boolean
pathology_csv: null                    # not supplied
ont_barcodes: barcode01,barcode02      # comma-separated list
```

Rules:

- Use spaces, not tabs.
- Keep one `key: value` setting per line.
- Use lowercase `true`, `false`, and `null`.
- Text after `#` is a comment.
- Do not repeat a key.
- Avoid spaces and `#` in filenames.
- YAML does not expand `~`, `$HOME`, `$ROOT`, or `$(pwd)`.

## Important paths

```yaml
mode: illumina
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results/sample_a
illumina_samplesheet: /path/to/my/directory/oncotracer/project/config/illumina.samplesheet.csv
```

- `mode` selects `illumina` or `ont`.
- `lpwgs_root` is the common parent visible to Docker or Singularity/Apptainer.
- `outdir` is the result directory for one run.
- `illumina_samplesheet` points to the FASTQ-to-sample table.

Keep every configured input, output, reference, and cache below `lpwgs_root`.

## Check absolute paths

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"

# Print and validate example paths.
realpath .
realpath "$PROJECT_DIR/input/fastq/Sample_A_R1.fastq.gz"
ls -lh "$PROJECT_DIR/input/fastq/Sample_A_R1.fastq.gz"
gzip -t "$PROJECT_DIR/input/fastq/Sample_A_R1.fastq.gz"
```

On Linux, an absolute path begins with `/`. In WSL, use Linux paths such as `/mnt/c/Users/Name/oncotracer`, not Windows `C:\...` paths.

## Manual YAML editing

Use manual setup only when Automatic Setup does not fit the study.

```bash
# Run this command from the oncotracer directory.

# Copy the minimal Illumina template and edit the copy.
cp "params/illumina.minimal.yml" "params/my_illumina.yml"
nano "params/my_illumina.yml"
```

A minimal file is:

```yaml
mode: illumina
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results/sample_a
illumina_samplesheet: /path/to/my/directory/oncotracer/project/input/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
```

Save Nano with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

```bash
# Run this command from the oncotracer directory.

# Inspect and run the copied YAML.
sed -n '1,120p' "params/my_illumina.yml"
nextflow run main.nf --docker \
  -params-file "params/my_illumina.yml" \
  -resume
```

Continue with [Illumina configuration](illumina.md), [ONT configuration](ont.md), or [All parameters](parameter_reference.md).
