# Input Files

Choose the row that matches your data. The optional pathology file does not replace sequencing input.

| Route | Required sequencing input | Required small metadata |
| --- | --- | --- |
| Illumina | One single-end FASTQ or one paired R1/R2 FASTQ pair per sample | Automatic paired-read `sample_name,status` table, or manual four-column samplesheet |
| ONT | One or more FASTQs inside each selected barcode directory | Automatic `barcode,sample_name,status` table, or manual barcode/sample lists in YAML |
| Classifier + pathology | Illumina or ONT input above | Pathology CSV with matching sample, case, and diagnosis columns |

## Keep one understandable project tree

This layout keeps every configured path below one `lpwgs_root`:

```text
oncotracer/                              # lpwgs_root in this example
├── main.nf
├── params/
│   ├── my_illumina.yml
│   └── my_ont.yml
└── project/
    ├── input/
    │   ├── illumina_fastq/
    │   │   ├── Patient_A_R1.fastq.gz
    │   │   └── Patient_A_R2.fastq.gz
    │   ├── illumina.samplesheet.csv
    │   ├── pathology.csv
    │   └── fastq_pass/
    │       ├── barcode01/
    │       │   └── reads_001.fastq.gz
    │       └── barcode02/
    │           └── reads_001.fastq.gz
    ├── config/
    └── runs/
```

Create the empty directories from the repository root:

```bash
cd oncotracer                                                        # enter the cloned repository
mkdir -p project/input/illumina_fastq project/input/fastq_pass        # sequencing input directories
mkdir -p project/config project/runs                                  # generated configuration and result directories
ROOT=$(pwd)                                                           # save the absolute lpwgs_root used in examples below
echo "$ROOT"                                                        # verify it before editing files
```

## Recommended: let OncoTracer map the inputs

Automatic setup checks supported filenames and writes the analysis YAML. It is a configuration step and stops before analysis.

For Illumina, create `project/input/illumina_fastq/samples.csv`:

```csv
sample_name,status
Patient_A,TUMOR
Patient_B,NORMAL
```

Then run:

```bash
ROOT=$(pwd)                                                           # run from the repository root
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$ROOT/project/input/illumina_fastq" \
  --sample_table "$ROOT/project/input/illumina_fastq/samples.csv" \
  --auto_config_dir "$ROOT/project/config/illumina" \
  --auto_outdir "$ROOT/project/runs/illumina_auto"
```

For ONT, create `project/input/fastq_pass/samples.csv`:

```csv
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Patient_B,NORMAL
```

Then run:

```bash
ROOT=$(pwd)                                                           # run from the repository root
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder "$ROOT/project/input/fastq_pass" \
  --sample_table "$ROOT/project/input/fastq_pass/samples.csv" \
  --auto_config_dir "$ROOT/project/config/ont" \
  --auto_outdir "$ROOT/project/runs/ont_auto"
```

See [Automatic Setup](auto_params.md) for exact filename detection and complete run commands.

## Manual Illumina samplesheet

Use a manual samplesheet when filenames do not follow automatic R1/R2 detection. Create it with Nano:

```bash
nano project/input/illumina.samplesheet.csv                            # create or edit the CSV
```

This example assumes the clone is `/home/student/oncotracer`; replace that prefix with the output of `pwd`:

```csv
sample,fastq_1,fastq_2,status
Patient_A,/home/student/oncotracer/project/input/illumina_fastq/Patient_A_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Patient_A_R2.fastq.gz,tumor
Patient_B,/home/student/oncotracer/project/input/illumina_fastq/Patient_B_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Patient_B_R2.fastq.gz,normal
```

Save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`.

| Column | Required content |
| --- | --- |
| `sample` | Unique sample ID. Use letters, numbers, `_`, or `-`. Pathology matching is exact and case-sensitive. |
| `fastq_1` | Absolute path to this sample's R1 `.fastq.gz`. |
| `fastq_2` | Absolute path to this sample's R2 `.fastq.gz`, or an empty cell when every library in the run is single-end. |
| `status` | `tumor` or `normal`. |

For single-end data, keep the header unchanged and leave the third field empty:

```csv
sample,fastq_1,fastq_2,status
Patient_SE,/home/student/oncotracer/project/input/illumina_fastq/Patient_SE.fastq.gz,,tumor
```

Do not mix single-end and paired-end rows in one workflow invocation.

Inspect every row and test both mates before running:

```bash
sed -n '1,20p' project/input/illumina.samplesheet.csv                 # inspect header and rows
ls -lh project/input/illumina_fastq/Patient_A_R1.fastq.gz             # confirm R1 is present and non-empty
ls -lh project/input/illumina_fastq/Patient_A_R2.fastq.gz             # confirm R2 is present and non-empty
gzip -t project/input/illumina_fastq/Patient_A_R1.fastq.gz            # no output means the gzip is complete
gzip -t project/input/illumina_fastq/Patient_A_R2.fastq.gz            # test the mate too
```

## Manual ONT barcode input

`ont_folder` must be the parent of the barcode directories:

```text
project/input/fastq_pass/
├── barcode01/
│   ├── reads_001.fastq.gz
│   └── reads_002.fastq.gz
└── barcode02/
    └── reads_001.fastq.gz
```

FASTQs may end in `.fastq`, `.fq`, `.fastq.gz`, or `.fq.gz`. Put them directly inside the barcode directory, not another nested directory.

Inspect the input:

```bash
find project/input/fastq_pass -maxdepth 2 -type d -print | sort        # list barcode directories
find project/input/fastq_pass -maxdepth 2 -type f -print | sort | head -20 # show FASTQs
ls -lh project/input/fastq_pass/barcode01                             # confirm one barcode is populated
```

The YAML lists match by position:

```yaml
ont_folder: /home/student/oncotracer/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
```

Here `barcode01` is `Patient_A` and `barcode02` is `Patient_B`. For normal inputs, `ont_normal_folder`, `ont_normal_barcodes`, and `ont_normal_sample_names` follow the same rule.

## Optional pathology CSV

Pathology concordance requires three concepts:

1. a sample identifier that exactly matches an OncoTracer sample;
2. a case/accession identifier; and
3. diagnosis text.

Start from the anonymized repository example:

```bash
cp examples/pathology/anonymized_pathology_example.csv project/input/pathology.csv # copy a safe format example
nano project/input/pathology.csv                                                   # replace example rows
```

Save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`. A minimal file is:

```csv
illumina_sample_id,case_code,final_diagnosis
Patient_A,Case_001,Diffuse large B-cell lymphoma
Patient_B,Case_002,Reactive lymphoid tissue
```

The column headers can differ, but the YAML must name the real headers:

```yaml
run_cna_classifier: true
pathology_csv: /home/student/oncotracer/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
```

`Patient_A` must appear exactly the same in the FASTQ samplesheet or generated ONT sample names. `Patient_A`, `patient_a`, and `Patient-A` are different identifiers.

Do not commit identifiable clinical data. Export only the columns required for the analysis; remove names, national identifiers, birth dates, addresses, insurance identifiers, and unnecessary free text.

Inspect the IDs before analysis:

```bash
head -5 project/input/illumina.samplesheet.csv                         # inspect sequencing sample IDs
head -5 project/input/pathology.csv                                    # inspect pathology sample IDs
```

Continue with the [Pathology and Classifier tutorial](configuration/pathology.md).

## Pre-run checklist

Before the real command, confirm:

- every configured path is absolute and below `lpwgs_root`;
- FASTQs exist, are non-empty, and compressed files pass `gzip -t`;
- Illumina R1 and R2 belong to the same sample;
- ONT barcode and sample lists have the same length and order;
- sample names are unique and match pathology exactly; and
- `outdir` is a new directory for this experiment.

An optional `-stub-run` checks workflow wiring only. It does not replace these input checks or the real analysis.
