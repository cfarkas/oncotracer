# Input Files

Choose the row that matches your data. The optional pathology file does not replace sequencing input.

| Route | Required sequencing input | Required small metadata |
| --- | --- | --- |
| Illumina | One single-end FASTQ or one paired R1/R2 FASTQ pair per sample | Automatic `sample_name,status` table, or manual four-column samplesheet |
| ONT | One or more FASTQs inside each selected barcode directory | Automatic `barcode,sample_name,status` table, or manual barcode/sample lists in YAML |
| Classifier + pathology | Illumina or ONT input above | Pathology CSV with matching sample, case, and diagnosis columns |

## Keep one understandable project tree

This page uses `/home/student/oncotracer` as an example repository location.
Replace that prefix with the location of your clone. Put the reads and
`samples.csv` in the input folders shown below. OncoTracer creates the
`config` and `runs` folders when Automatic Setup is run.

```text
oncotracer/
├── main.nf
├── params/
│   ├── my_illumina.yml
│   └── my_ont.yml
└── project/                             # lpwgs_root in this example
    ├── input/
    │   ├── illumina_fastq/
    │   │   ├── Patient_A_R1.fastq.gz
    │   │   ├── Patient_A_R2.fastq.gz
    │   │   ├── Patient_B_R1.fastq.gz
    │   │   ├── Patient_B_R2.fastq.gz
    │   │   └── samples.csv
    │   ├── pathology.csv
    │   └── fastq_pass/
    │       ├── barcode01/
    │       │   └── reads_001.fastq.gz
    │       ├── barcode02/
    │       │   └── reads_001.fastq.gz
    │       └── samples.csv
    ├── config/                         # created by Automatic Setup
    └── runs/                           # created by Automatic Setup
```

## Recommended: let OncoTracer map the inputs

Automatic Setup checks supported filenames and writes the analysis YAML. It
is a configuration step and stops before analysis. The reads folder and
sample table must already exist; the two destination folders do not.

| Option | What it means |
| --- | --- |
| `--reads_folder` | The existing folder containing the FASTQ files. |
| `--sample_table` | The existing CSV that connects file or barcode names to sample names and `TUMOR`/`NORMAL`. |
| `--auto_config_dir` | Where Automatic Setup creates the YAML and, for Illumina, the generated samplesheet. The folder is created if needed. |
| `--auto_outdir` | Where the later real analysis will save BAMs, CNA tables, plots, and reports. Automatic Setup creates this folder if needed and writes it as `outdir:` in the YAML, but no reads are analyzed yet. |

Use absolute paths because Automatic Setup runs inside a Nextflow task.

For Illumina, create `project/input/illumina_fastq/samples.csv`:

```csv
sample_name,status
Patient_A,TUMOR
Patient_B,NORMAL
```

Then run:

```bash
cd /home/student/oncotracer
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/project/input/illumina_fastq \
  --sample_table /home/student/oncotracer/project/input/illumina_fastq/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/illumina \
  --auto_outdir /home/student/oncotracer/project/runs/illumina_auto
```

For ONT, create `project/input/fastq_pass/samples.csv`:

```csv
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Patient_B,NORMAL
```

Then run:

```bash
cd /home/student/oncotracer
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder /home/student/oncotracer/project/input/fastq_pass \
  --sample_table /home/student/oncotracer/project/input/fastq_pass/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/ont \
  --auto_outdir /home/student/oncotracer/project/runs/ont_auto
```

See [Automatic Setup](auto_params.md) for exact filename detection and complete run commands.

## Manual Illumina samplesheet

Use a manual samplesheet when filenames do not follow the supported automatic
single-end or R1/R2 detection patterns. Create it with Nano:

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
