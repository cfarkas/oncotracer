# Automatic Setup from a Reads Folder

`--auto_params` is the recommended way to configure your own FASTQs. You provide:

1. a reads folder; and
2. a small CSV that maps each sample to `TUMOR` or `NORMAL`.

OncoTracer validates the supported layout and writes a YAML file. For Illumina, it also writes the FASTQ samplesheet. Automatic Setup stops after creating those files; the second Nextflow command starts the analysis.

The analysis can use:

- `--docker` with [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer); or
- `--singularity` with `docker://carlosfarkas/oncotracer:latest` on a configured HPC system.

![Example OncoTracer input layouts: Illumina FASTQ files and ONT barcode folders mapped to sample names and tumor/normal status in samples.csv.](assets/tutorial/auto_params_folder_layout.svg)

## Before you begin

```bash
# Clone OncoTracer when it is not already installed.
git clone https://github.com/cfarkas/oncotracer.git

# Enter the repository and confirm Nextflow is available.
cd oncotracer
nextflow -version
```

Use absolute paths for your reads, configuration, work, and result directories.

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
nextflow run main.nf --docker|--singularity -params-file generated.yml -resume
```

## Illumina step by step

### 1. Organize the FASTQs

For paired-end data, keep one R1 and one R2 file per sample directly inside one folder:

```text
/data/study42/input/fastq/
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

For single-end data, use one `<sample>.fastq.gz` or `<sample>.fq.gz` file per sample. Do not mix single-end and paired-end libraries in the same Automatic Setup command. Automatic Setup does not combine lane files recursively.

### 2. Create the sample table

```bash
# Open a new Illumina sample table.
nano /data/study42/input/samples.csv
```

Paste an exact name and role for every sample:

```csv
sample_name,status
TUMOR_01,TUMOR
TUMOR_02,TUMOR
CONTROL_01,NORMAL
CONTROL_02,NORMAL
```

Save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

The filename prefix must match `sample_name` exactly. `status` must be `TUMOR` or `NORMAL`.

Normal-control behavior is simple:

- zero `NORMAL` rows: no local panel of normals;
- one `NORMAL` row: configuration stops with an error;
- two or more `NORMAL` rows: Automatic Setup enables the local qDNAseq reference.

### 3. Generate the Illumina YAML and samplesheet

```bash
# Generate the Illumina configuration without starting the analysis.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /data/study42/input/fastq \
  --sample_table /data/study42/input/samples.csv \
  --auto_config_dir /data/study42/config \
  --auto_outdir /data/study42/results \
  -work-dir /data/study42/work/auto_params
```

Automatic Setup runs `gzip -t` on every compressed FASTQ. It stops when a file is missing, corrupt, ambiguous, or part of a mixed single-end/paired-end layout.

The generated directory contains:

```text
/data/study42/config/
├── auto_params_manifest.tsv
├── illumina.auto.yml
└── illumina.samplesheet.csv
```

### 4. Inspect the generated files

```bash
# Inspect the analysis settings.
sed -n '1,140p' /data/study42/config/illumina.auto.yml

# Inspect the FASTQ-to-sample mapping.
sed -n '1,30p' /data/study42/config/illumina.samplesheet.csv

# Inspect the sample counts and file hashes.
cat /data/study42/config/auto_params_manifest.tsv
```

For the table above, the generated YAML includes:

```yaml
mode: illumina
lpwgs_root: /data/study42
outdir: /data/study42/results
illumina_samplesheet: /data/study42/config/illumina.samplesheet.csv
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
TUMOR_01,/data/study42/input/fastq/TUMOR_01_R1.fastq.gz,/data/study42/input/fastq/TUMOR_01_R2.fastq.gz,tumor
TUMOR_02,/data/study42/input/fastq/TUMOR_02_R1.fastq.gz,/data/study42/input/fastq/TUMOR_02_R2.fastq.gz,tumor
CONTROL_01,/data/study42/input/fastq/CONTROL_01_R1.fastq.gz,/data/study42/input/fastq/CONTROL_01_R2.fastq.gz,normal
CONTROL_02,/data/study42/input/fastq/CONTROL_02_R1.fastq.gz,/data/study42/input/fastq/CONTROL_02_R2.fastq.gz,normal
```

### 5. Run the Illumina analysis

Docker:

```bash
# Run the generated Illumina YAML with Docker and resume support.
nextflow run main.nf --docker \
  -params-file /data/study42/config/illumina.auto.yml \
  -work-dir /data/study42/work/analysis \
  -resume
```

Singularity or Apptainer:

```bash
# Run the same YAML through the HPC container option.
nextflow run main.nf --singularity \
  -params-file /data/study42/config/illumina.auto.yml \
  -work-dir /data/study42/work/analysis \
  -resume
```

### 6. Check a local panel of normals

When two or more controls are used, verify the control list, control QC, tumor-only corrected files, and completion marker:

```bash
# Set convenient output paths.
OUT=/data/study42/results
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
/data/study42/fastq_pass/
├── barcode01/
│   └── reads_001.fastq.gz
├── barcode02/
│   └── reads_001.fastq.gz
└── barcode03/
    └── reads_001.fastq.gz
```

### 2. Create the barcode table

```bash
# Open a new ONT barcode-to-sample table.
nano /data/study42/fastq_pass/samples.csv
```

Paste:

```csv
barcode,sample_name,status
barcode01,TUMOR_01,TUMOR
barcode02,CONTROL_01,NORMAL
barcode03,TUMOR_02,TUMOR
```

The barcode value must match the directory name exactly. The explicit three-column form is recommended because it is easy to review.

### 3. Generate the ONT YAML

```bash
# Generate the ONT configuration without starting the analysis.
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder /data/study42/fastq_pass \
  --sample_table /data/study42/fastq_pass/samples.csv \
  --auto_config_dir /data/study42/config_ont \
  --auto_outdir /data/study42/results_ont \
  -work-dir /data/study42/work/auto_params_ont
```

### 4. Inspect and run the ONT YAML

```bash
# Inspect the generated ONT settings and manifest.
sed -n '1,140p' /data/study42/config_ont/ont.auto.yml
cat /data/study42/config_ont/auto_params_manifest.tsv

# Run the generated ONT YAML with Docker.
nextflow run main.nf --docker \
  -params-file /data/study42/config_ont/ont.auto.yml \
  -work-dir /data/study42/work/analysis_ont \
  -resume
```

For HPC, replace `--docker` with `--singularity` in the final command.

## Common setup errors

- The sample name does not match the FASTQ prefix.
- More than one R1 or R2 file matches the same sample.
- Single-end and paired-end Illumina files are mixed.
- A compressed FASTQ fails `gzip -t`.
- An ONT barcode in the table does not match a directory.
- Exactly one Illumina `NORMAL` row was supplied.
- Reads, configuration, and output paths do not share a usable project root.

Fix the input layout or table, rerun `--auto_params`, inspect the generated files, and then run the analysis YAML.
