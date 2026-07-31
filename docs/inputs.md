# Input Files

Choose the format that matches the sequencing platform. The optional pathology CSV does not replace sequencing input.

| Route | Sequencing input | Small metadata file |
| --- | --- | --- |
| Illumina | One single-end FASTQ or one R1/R2 pair per sample | `sample_name,status` for Automatic Setup, or a manual four-column samplesheet |
| ONT | One or more FASTQs inside each barcode directory | `barcode,sample_name,status` for Automatic Setup, or barcode/sample lists in YAML |
| Pathology comparison | Illumina or ONT input above | Pathology CSV with matching sample, case, and diagnosis columns |

## Recommended project layout

```text
/home/student/oncotracer/project/
├── input/
│   ├── illumina_fastq/
│   │   ├── Patient_A_R1.fastq.gz
│   │   ├── Patient_A_R2.fastq.gz
│   │   ├── Patient_B_R1.fastq.gz
│   │   ├── Patient_B_R2.fastq.gz
│   │   └── samples.csv
│   ├── fastq_pass/
│   │   ├── barcode01/
│   │   │   └── reads_001.fastq.gz
│   │   ├── barcode02/
│   │   │   └── reads_001.fastq.gz
│   │   └── samples.csv
│   └── pathology.csv
├── config/
├── work/
└── results/
```

Keep configured inputs and outputs below `lpwgs_root` so Docker or Singularity can access them.

## Illumina Automatic Setup

### Paired-end filenames

```text
Patient_A_R1.fastq.gz
Patient_A_R2.fastq.gz
Patient_B_R1.fastq.gz
Patient_B_R2.fastq.gz
```

The sample name is the text before `_R1` and `_R2`. Names ending in `_1` and `_2`, and equivalent `.fq.gz` files, are also accepted.

For single-end data, use one exact file per sample, such as `Patient_A.fastq.gz`. Do not mix single-end and paired-end samples in one run.

Create this table:

```csv
sample_name,status
Patient_A,TUMOR
Patient_B,TUMOR
Control_1,NORMAL
Control_2,NORMAL
```

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Generate the Illumina YAML and samplesheet.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/project/input/illumina_fastq \
  --sample_table /home/student/oncotracer/project/input/illumina_fastq/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/illumina \
  --auto_outdir /home/student/oncotracer/project/results/illumina
```

No `NORMAL` rows means no local normal reference. One normal is rejected. Two or more normals are used to build the run-local qDNAseq reference, and corrected CNA outputs contain tumor samples only.

## ONT Automatic Setup

Point `--reads_folder` to the parent of the barcode directories:

```text
/home/student/oncotracer/project/input/fastq_pass/
├── barcode01/
│   └── reads_001.fastq.gz
└── barcode02/
    └── reads_001.fastq.gz
```

Create this table:

```csv
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Patient_B,NORMAL
```

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Generate the ONT YAML.
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder /home/student/oncotracer/project/input/fastq_pass \
  --sample_table /home/student/oncotracer/project/input/fastq_pass/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/ont \
  --auto_outdir /home/student/oncotracer/project/results/ont
```

Each barcode value must match a directory name exactly. At least one row must be `TUMOR`.

## Manual Illumina samplesheet

Use a manual samplesheet when filenames do not follow the supported automatic patterns.

```bash
# Create the manual Illumina samplesheet.
nano /home/student/oncotracer/project/input/illumina.samplesheet.csv
```

Paste exactly this paired-end example:

```csv
sample,fastq_1,fastq_2,status
Patient_A,/home/student/oncotracer/project/input/illumina_fastq/Patient_A_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Patient_A_R2.fastq.gz,tumor
Patient_B,/home/student/oncotracer/project/input/illumina_fastq/Patient_B_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Patient_B_R2.fastq.gz,tumor
Control_1,/home/student/oncotracer/project/input/illumina_fastq/Control_1_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Control_1_R2.fastq.gz,normal
Control_2,/home/student/oncotracer/project/input/illumina_fastq/Control_2_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Control_2_R2.fastq.gz,normal
```

For single-end data, keep the four-column header and leave `fastq_2` empty:

```csv
sample,fastq_1,fastq_2,status
Patient_SE,/home/student/oncotracer/project/input/illumina_fastq/Patient_SE.fastq.gz,,tumor
```

| Column | Required value |
| --- | --- |
| `sample` | Unique identifier using letters, digits, `.`, `_`, or `-` |
| `fastq_1` | Absolute R1 or single-end FASTQ path |
| `fastq_2` | Absolute R2 path, or empty for every row in a single-end run |
| `status` | `tumor` or `normal` |

```bash
# Inspect the saved samplesheet.
sed -n '1,20p' /home/student/oncotracer/project/input/illumina.samplesheet.csv

# Confirm that one R1 FASTQ exists and is not empty.
ls -lh /home/student/oncotracer/project/input/illumina_fastq/Patient_A_R1.fastq.gz

# Confirm that the matching R2 FASTQ exists and is not empty.
ls -lh /home/student/oncotracer/project/input/illumina_fastq/Patient_A_R2.fastq.gz

# Check R1 gzip integrity; success produces no output.
gzip -t /home/student/oncotracer/project/input/illumina_fastq/Patient_A_R1.fastq.gz

# Check R2 gzip integrity.
gzip -t /home/student/oncotracer/project/input/illumina_fastq/Patient_A_R2.fastq.gz
```

For a manual local panel of normals, the YAML list must contain every and only the samplesheet rows marked `normal`:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: Control_1,Control_2
illumina_pon_min_normals: 2
```

## Manual ONT barcode input

Manual ONT YAML lists are positional:

```yaml
ont_folder: /home/student/oncotracer/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
```

The first barcode maps to the first sample name. Normal barcode lists follow the same rule.

```bash
# List barcode directories.
find /home/student/oncotracer/project/input/fastq_pass \
  -maxdepth 2 -type d -print | sort

# List the first FASTQ files found below the barcode tree.
find /home/student/oncotracer/project/input/fastq_pass \
  -maxdepth 2 -type f -print | sort | sed -n '1,20p'
```

## Optional pathology CSV

Create a table whose sample identifier exactly matches the sequencing sample name:

```csv
illumina_sample_id,case_code,final_diagnosis
Patient_A,Case_001,Diffuse large B-cell lymphoma
Patient_B,Case_002,Reactive lymphoid tissue
```

```bash
# Copy the anonymized repository format example.
cp examples/pathology/anonymized_pathology_example.csv \
  /home/student/oncotracer/project/input/pathology.csv

# Edit the copied pathology table.
nano /home/student/oncotracer/project/input/pathology.csv

# Inspect sequencing sample identifiers.
head -5 /home/student/oncotracer/project/input/illumina.samplesheet.csv

# Inspect pathology sample identifiers.
head -5 /home/student/oncotracer/project/input/pathology.csv
```

Do not commit identifiable clinical data. See [Pathology and Classifier](configuration/pathology.md) for the complete matched example.

## Pre-run checklist

Confirm that:

- every configured path is absolute and below `lpwgs_root`;
- FASTQs exist, are non-empty, and compressed files pass `gzip -t`;
- Illumina R1 and R2 belong to the same sample;
- one Illumina run uses one consistent single-end or paired-end layout;
- ONT barcode and sample-name lists have matching order and length;
- sample names are unique and match pathology identifiers exactly; and
- `outdir` is a new directory for the experiment.
