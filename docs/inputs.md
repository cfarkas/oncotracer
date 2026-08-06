# Input Files

> **Legacy v1.1 documentation.** This unlisted command page is retained for the immutable Nextflow release. For native v2 input preparation, use [Automatic Setup](auto_params.md).

Choose the route that matches your data. The optional pathology file does not replace sequencing input.

| Route | Required sequencing input | Required small metadata |
| --- | --- | --- |
| Illumina | One single-end FASTQ or one paired R1/R2 pair per sample | Automatic `sample_name,status` table, or manual four-column samplesheet |
| ONT | One or more FASTQs inside each selected barcode directory | Automatic `barcode,sample_name,status` table, or manual barcode/sample lists in YAML |
| Classifier plus pathology | Illumina or ONT input above | Pathology CSV with matching sample, case, and diagnosis columns |

Run the commands from the cloned `oncotracer` directory.

## Recommended project tree

```text
/path/to/my/directory/oncotracer/
├── main.nf
├── params/
└── project/
    ├── input/
    │   ├── illumina_fastq/
    │   │   ├── Patient_A_R1.fastq.gz
    │   │   ├── Patient_A_R2.fastq.gz
    │   │   ├── Patient_B_R1.fastq.gz
    │   │   ├── Patient_B_R2.fastq.gz
    │   │   ├── Control_A_R1.fastq.gz
    │   │   ├── Control_A_R2.fastq.gz
    │   │   ├── Control_B_R1.fastq.gz
    │   │   ├── Control_B_R2.fastq.gz
    │   │   └── samples.csv
    │   ├── pathology.csv
    │   └── fastq_pass/
    │       ├── barcode01/
    │       │   └── reads_001.fastq.gz
    │       ├── barcode02/
    │       │   └── reads_001.fastq.gz
    │       └── samples.csv
    ├── config/
    ├── work/
    └── results/
```

Keep inputs, configuration, work, reference/cache, and results below a common project root visible to Docker or Singularity/Apptainer.

## Automatic Setup inputs

| Option | Meaning |
| --- | --- |
| `--reads_folder` | Existing folder containing the FASTQs. |
| `--sample_table` | Existing CSV mapping sample or barcode names to `TUMOR` or `NORMAL`. |
| `--auto_config_dir` | Destination for the generated YAML, audit manifest, and Illumina samplesheet. |
| `--auto_outdir` | Destination recorded in the YAML for the later analysis results. |

Automatic Setup creates configuration files and stops. It does not align reads or call CNAs.

### Illumina sample table

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"
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

The sample name must match the single-end filename or the text before `_R1`/`_R2`. Zero normal rows run without a local panel, one normal is rejected, and two or more normals enable the local qDNAseq reference.

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"

# Generate the Illumina YAML and samplesheet.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$PROJECT_DIR/input/illumina_fastq" \
  --sample_table "$PROJECT_DIR/input/illumina_fastq/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config/illumina" \
  --auto_outdir "$PROJECT_DIR/results/illumina" \
  -work-dir "$PROJECT_DIR/work/auto_params_illumina"
```

### ONT barcode table

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"
mkdir -p "$PROJECT_DIR/input/fastq_pass"

# Create or replace the explicit ONT barcode table.
cat > "$PROJECT_DIR/input/fastq_pass/samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Patient_B,NORMAL
CSV

# Display the saved table.
cat "$PROJECT_DIR/input/fastq_pass/samples.csv"
```

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"

# Generate the ONT YAML.
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder "$PROJECT_DIR/input/fastq_pass" \
  --sample_table "$PROJECT_DIR/input/fastq_pass/samples.csv" \
  --auto_config_dir "$PROJECT_DIR/config/ont" \
  --auto_outdir "$PROJECT_DIR/results/ont" \
  -work-dir "$PROJECT_DIR/work/auto_params_ont"
```

See [Automatic Setup](auto_params.md) for supported filename patterns and complete run commands.

## Manual Illumina samplesheet

Use a manual samplesheet when filenames do not follow the supported automatic patterns.

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"
mkdir -p "$PROJECT_DIR/input"

# Create or replace a paired-end samplesheet.
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

For single-end data, retain the four-column header and leave `fastq_2` empty. Do not mix single-end and paired-end rows in one run.

| Column | Required content |
| --- | --- |
| `sample` | Unique sample ID using letters, digits, `.`, `_`, or `-`. |
| `fastq_1` | Absolute path to the single read or R1 FASTQ. |
| `fastq_2` | Absolute R2 path, or empty for every row in a single-end run. |
| `status` | `tumor` or `normal`. |

For a manually configured local panel, the YAML must list every and only the normal samples:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: Control_A,Control_B
illumina_pon_min_normals: 2
```

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"

# Inspect the table and validate a representative read pair.
sed -n '1,20p' "$PROJECT_DIR/input/illumina.samplesheet.csv"
ls -lh "$PROJECT_DIR/input/illumina_fastq/Patient_A_R1.fastq.gz"
ls -lh "$PROJECT_DIR/input/illumina_fastq/Patient_A_R2.fastq.gz"
gzip -t "$PROJECT_DIR/input/illumina_fastq/Patient_A_R1.fastq.gz"
gzip -t "$PROJECT_DIR/input/illumina_fastq/Patient_A_R2.fastq.gz"
```

## Manual ONT input

`ont_folder` must be the parent of the barcode directories:

```text
/path/to/my/directory/oncotracer/project/input/fastq_pass/
├── barcode01/
│   ├── reads_001.fastq.gz
│   └── reads_002.fastq.gz
└── barcode02/
    └── reads_001.fastq.gz
```

FASTQs may end in `.fastq`, `.fq`, `.fastq.gz`, or `.fq.gz` and must be placed directly inside each barcode directory.

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"

# Inspect barcode directories and FASTQs.
find "$PROJECT_DIR/input/fastq_pass" -maxdepth 2 -type d -print | sort
find "$PROJECT_DIR/input/fastq_pass" -maxdepth 2 -type f -print | sort | head -20
```

The YAML lists map by position:

```yaml
ont_folder: /path/to/my/directory/oncotracer/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
```

## Optional pathology CSV

A pathology table needs a sequencing sample identifier, case identifier, and diagnosis text. The sample identifier must match exactly.

```bash
# Set the standard repository and project paths.
PROJECT_DIR="$(pwd)/project"

# Copy the anonymized format example and edit it.
cp "examples/pathology/anonymized_pathology_example.csv" \
  "$PROJECT_DIR/input/pathology.csv"
nano "$PROJECT_DIR/input/pathology.csv"
```

Save Nano with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

```csv
illumina_sample_id,case_code,final_diagnosis
Patient_A,Case_001,Diffuse large B-cell lymphoma
Patient_B,Case_002,Reactive lymphoid tissue
```

```yaml
run_cna_classifier: true
pathology_csv: /path/to/my/directory/oncotracer/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
```

Do not commit identifiable clinical data. Continue with [Pathology and classifier](configuration/pathology.md).

## Pre-run checklist

Confirm that:

- every configured path is absolute and below `lpwgs_root`;
- FASTQs exist, are non-empty, and compressed files pass `gzip -t`;
- Illumina R1 and R2 belong to the same sample;
- all Illumina rows use one consistent layout;
- an enabled Illumina panel selects at least two exact normal IDs;
- ONT barcode and sample lists have the same length and order;
- sample names are unique and match pathology exactly; and
- `outdir` is a new directory for the experiment.
