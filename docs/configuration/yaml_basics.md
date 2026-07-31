# YAML and Paths

A YAML file is a small plain-text run configuration. It stores input paths, output paths, and analysis settings. FASTQ reads are not stored in the YAML.

## Choose how to create the YAML

| Situation | Route |
| --- | --- |
| Standard Illumina filenames or ONT barcode folders | [Automatic Setup](../auto_params.md), recommended |
| Custom samplesheet, reference, or advanced settings | Manual YAML editing |
| Public verification data | [QuickStart Example 1](../quick_start.md) |

## Automatic Setup

Create an Illumina sample table:

```csv
sample_name,status
Sample_A,TUMOR
Control_1,NORMAL
Control_2,NORMAL
```

Then generate the YAML:

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Generate an Illumina YAML and samplesheet.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/project/input/fastq \
  --sample_table /home/student/oncotracer/project/input/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config \
  --auto_outdir /home/student/oncotracer/project/results
```

Automatic Setup checks the files, writes the YAML and manifest, writes an Illumina samplesheet, and stops before analysis.

## YAML vocabulary

```yaml
mode: illumina                         # text value
illumina_binsize_kb: 100               # integer value
run_cna_classifier: false              # Boolean value
pathology_csv: null                    # null means not supplied
ont_barcodes: barcode01,barcode02      # comma-separated list
```

Use these rules:

- Use spaces, not tabs.
- Keep one `key: value` setting per line.
- Use lowercase `true`, `false`, and `null`.
- A line beginning with `#` is a comment.
- Do not repeat a key.
- The YAML does not expand `~`, `$HOME`, `$ROOT`, or `$(pwd)`.
- Use absolute paths.

## The important paths

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/results/sample_a
illumina_samplesheet: /home/student/oncotracer/project/config/illumina.samplesheet.csv
```

- `mode` selects `illumina` or `ont`.
- `lpwgs_root` is the common directory made available to Docker or Singularity.
- `outdir` is the result directory for one run.
- `illumina_samplesheet` points to the FASTQ-to-sample CSV.

All configured inputs and outputs should be below `lpwgs_root` so the container can access them.

## Check absolute paths

```bash
# Print the repository path.
pwd

# Print an absolute FASTQ path.
realpath project/input/fastq/Sample_A_R1.fastq.gz

# Confirm that the FASTQ exists and is not empty.
ls -lh project/input/fastq/Sample_A_R1.fastq.gz

# Check gzip integrity; success produces no output.
gzip -t project/input/fastq/Sample_A_R1.fastq.gz
```

Linux paths start with `/`. In WSL, use a Linux path such as `/mnt/c/Users/Name/oncotracer`, not a Windows `C:\...` path.

## Manual YAML editing

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Copy the minimal Illumina template.
cp params/illumina.minimal.yml params/my_illumina.yml

# Edit the copied file.
nano params/my_illumina.yml
```

Example manual YAML:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/results/sample_a
illumina_samplesheet: /home/student/oncotracer/project/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
```

```bash
# Inspect the saved YAML.
sed -n '1,140p' params/my_illumina.yml

# Run or resume the YAML with Docker.
nextflow run main.nf --docker \
  -params-file params/my_illumina.yml \
  -resume
```

Use `--singularity` instead of `--docker` on a configured HPC system.

Continue with [Illumina Configuration](illumina.md), [ONT Configuration](ont.md), or the [Parameter Reference](parameter_reference.md).
