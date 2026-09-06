# Input files and folder layouts

Use [setup](setup.md) for explicit FASTQ paths or [batch setup](auto_params.md)
for files with matching sample names. This page explains the input formats.
The CSV blocks create real files: paste through the closing `CSV` line.
`cat >` replaces the named file if it already exists. [Terminal basics](command_basics.md).

## Recommended project tree

The installed executable may live in `/usr/local/bin`; the project itself can be anywhere on storage visible to the selected backend.

```text
project/
├── input/
│   ├── illumina_fastq/
│   │   ├── Patient_A_R1.fastq.gz
│   │   ├── Patient_A_R2.fastq.gz
│   │   ├── Patient_B_R1.fastq.gz
│   │   ├── Patient_B_R2.fastq.gz
│   │   ├── Control_A_R1.fastq.gz
│   │   ├── Control_A_R2.fastq.gz
│   │   ├── Control_B_R1.fastq.gz
│   │   └── Control_B_R2.fastq.gz
│   ├── fastq_pass/
│   │   ├── barcode01/
│   │   │   └── reads_001.fastq.gz
│   │   └── barcode02/
│   │       └── reads_001.fastq.gz
│   ├── illumina_samples.csv
│   ├── ont_samples.csv
│   └── pathology.csv
├── config/
├── results/
└── reference/           reusable genome files; created at run time
```

Keep inputs, configuration, reference/cache, and results below a small number of absolute project roots. OncoTracer derives Docker and Singularity mounts from the YAML paths.

## Reference storage safety

Use `--hg38_build PATH` with a prepared OncoTracer reference, not an arbitrary
FASTA or index file. Shared references are checked and read without modification;
an incompatible reference stops the run. Without a path, setup/auto select
automatic prebuilt download at run time. `--build_reference` opts into local
construction. See [genome indexes](reference_indexes.md) for storage, RAM and
compatibility details. Do not edit reference metadata to bypass a failed check.

## Illumina input

### Automatic sample table

Create a two-column CSV:

```bash
mkdir -p "$PWD/project/input/illumina_fastq"

cat > "$PWD/project/input/illumina_samples.csv" <<'CSV'
sample_name,status
Patient_A,TUMOR
Patient_B,TUMOR
Control_A,NORMAL
Control_B,NORMAL
CSV
```

The sample name must match the filename stem before a supported read suffix such as `_R1`, `_R2`, `_1`, or `_2`.

Generate the YAML and four-column samplesheet:

```bash
oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/project/input/illumina_fastq" \
  --sample-table "$PWD/project/input/illumina_samples.csv" \
  --config-dir "$PWD/project/config/illumina" \
  --outdir "$PWD/project/results/illumina"
```

### Supported layouts

Paired-end:

```text
Patient_A_R1.fastq.gz
Patient_A_R2.fastq.gz
Patient_B_R1.fastq.gz
Patient_B_R2.fastq.gz
```

Single-end:

```text
Patient_A.fastq.gz
Patient_B.fastq.gz
```

Use one layout per analysis. Do not mix paired-end and single-end rows in the same generated samplesheet.

### Manual Illumina samplesheet

Use a manual samplesheet for unusual filenames:

```bash
mkdir -p "$PWD/project/config"
cat > "$PWD/project/config/illumina.samplesheet.csv" <<CSV
sample,fastq_1,fastq_2,status
Patient_A,"$PWD/project/input/illumina_fastq/Patient_A_R1.fastq.gz","$PWD/project/input/illumina_fastq/Patient_A_R2.fastq.gz",tumor
Patient_B,"$PWD/project/input/illumina_fastq/Patient_B_R1.fastq.gz","$PWD/project/input/illumina_fastq/Patient_B_R2.fastq.gz",tumor
Control_A,"$PWD/project/input/illumina_fastq/Control_A_R1.fastq.gz","$PWD/project/input/illumina_fastq/Control_A_R2.fastq.gz",normal
Control_B,"$PWD/project/input/illumina_fastq/Control_B_R1.fastq.gz","$PWD/project/input/illumina_fastq/Control_B_R2.fastq.gz",normal
CSV
```

| Column | Required content |
| --- | --- |
| `sample` | Unique sample ID using letters, digits, `.`, `_`, or `-` |
| `fastq_1` | Absolute single-end or R1 FASTQ path |
| `fastq_2` | Absolute R2 path, or empty for every row in a single-end run |
| `status` | `tumor` or `normal` |

Use this file with ordinary setup; no manual YAML is needed:

```bash
oncotracer setup --non-interactive \
  --mode illumina --analysis cna \
  --project "$PWD/project/manual-analysis" \
  --samplesheet "$PWD/project/config/illumina.samplesheet.csv"
oncotracer check --config "$PWD/project/manual-analysis/config/run.yml"
```

## Illumina normal rows

Every samplesheet row is an analysis sample. The `normal` value records the
submitted sample status, but does not make that row a reference input. Native
qDNAseq analyzes normal and tumor rows independently and writes per-sample
outputs for both. OncoTracer does not create a local panel from the normal rows.

## ONT input

`ont_folder` is the parent of barcode directories:

```text
project/input/fastq_pass/
├── barcode01/
│   ├── reads_001.fastq.gz
│   └── reads_002.fastq.gz
└── barcode02/
    └── reads_001.fastq.gz
```

FASTQs may end in `.fastq`, `.fq`, `.fastq.gz`, or `.fq.gz` and should be placed directly inside each selected barcode directory.

Create a mapping table:

```bash
mkdir -p "$PWD/project/input"
cat > "$PWD/project/input/ont_samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Patient_B,TUMOR
CSV

oncotracer auto \
  --mode ont \
  --reads-folder "$PWD/project/input/fastq_pass" \
  --sample-table "$PWD/project/input/ont_samples.csv" \
  --config-dir "$PWD/project/config/ont" \
  --outdir "$PWD/project/results/ont"
```

Manual YAML lists are positional:

```yaml
ont_folder: /absolute/path/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
```

The two lists must have identical lengths and order.

## Optional pathology CSV

A matched pathology table needs a sequencing sample identifier, case identifier, and diagnosis text:

```bash
mkdir -p "$PWD/project/input"
cat > "$PWD/project/input/pathology.csv" <<'CSV'
illumina_sample_id,case_code,final_diagnosis
Patient_A,Case_001,Diffuse large B-cell lymphoma
Patient_B,Case_002,Reactive lymphoid tissue
CSV
```

Flat YAML:

```yaml
run_cna_classifier: true
pathology_csv: /absolute/path/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
```

The sample identifier must match the sequencing sample exactly. Do not commit identifiable clinical data to a public repository.

## Pre-run validation

```bash
gzip -t "$PWD/project/input/illumina_fastq/Patient_A_R1.fastq.gz"
gzip -t "$PWD/project/input/illumina_fastq/Patient_A_R2.fastq.gz"
sed -n '1,20p' "$PWD/project/config/illumina/illumina.samplesheet.csv"
sed -n '1,160p' "$PWD/project/config/illumina/illumina.auto.yml"
oncotracer check --config "$PWD/project/config/illumina/illumina.auto.yml"
```

Confirm that:

- every path is absolute;
- FASTQs are non-empty and compressed files pass `gzip -t`;
- R1 and R2 belong to the same sample;
- all Illumina samples use one layout;
- every tumor/normal status matches the intended sample identity;
- ONT barcode and sample lists align positionally;
- sample names are unique and match pathology identifiers;
- the result directory is dedicated to the experiment.
