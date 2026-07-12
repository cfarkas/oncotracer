# Inputs

OncoTracer is FASTQ-first. Keep small editable files such as YAML, samplesheets, and pathology CSVs in predictable folders. Large FASTQ files may be stored elsewhere, but they must share a container-visible parent with the output directory or be reachable through the configured `lpwgs_root` mount.

## Suggested project tree

```text
oncotracer/
├── main.nf
├── params/
│   ├── my_illumina.yml
│   ├── my_ont.yml
│   └── my_illumina_pathology.yml
└── project/
    ├── input/
    │   ├── illumina.samplesheet.csv
    │   ├── pathology.csv
    │   └── fastq_pass/
    │       ├── barcode01/
    │       └── barcode02/
    └── runs/
```

Create it with:

```bash
cd oncotracer                              # enter the cloned repository
mkdir -p project/input project/runs        # create table/input and result folders
mkdir -p project/input/fastq_pass          # create the ONT folder only when needed
```

## Editing text files with nano

Open a file with `nano path/to/file`. Move with arrow keys, type normally, use Backspace/Delete to correct text, search with `Ctrl+W`, save with `Ctrl+O` followed by Enter, and exit with `Ctrl+X`.

<video controls preload="metadata" poster="../assets/tutorial/edit_yaml_with_nano_poster.png" style="width:100%;max-width:960px">
  <source src="../assets/tutorial/edit_yaml_with_nano.mp4" type="video/mp4">
  Your browser cannot play the embedded video. <a href="../assets/tutorial/edit_yaml_with_nano.mp4">Download the MP4</a>.
</video>

## Illumina FASTQ samplesheet

Create `project/input/illumina.samplesheet.csv`:

```bash
nano project/input/illumina.samplesheet.csv # open a new samplesheet in nano
```

```csv
sample,fastq_1,fastq_2,status
Sample_A,/absolute/path/oncotracer/project/input/Sample_A_R1.fastq.gz,/absolute/path/oncotracer/project/input/Sample_A_R2.fastq.gz,tumor
```

| Column | Meaning |
| --- | --- |
| `sample` | Unique sample name. Use letters, numbers, `_`, or `-`. This identifier must also match pathology when concordance is enabled. |
| `fastq_1` | Absolute path to the paired R1 FASTQ.gz. |
| `fastq_2` | Absolute path to the paired R2 FASTQ.gz. |
| `status` | Use `tumor` for tumor-only LP-WGS runs. |

Verify both FASTQs:

```bash
ls -lh /absolute/path/oncotracer/project/input/Sample_A_R1.fastq.gz # confirm R1 exists and is non-empty
ls -lh /absolute/path/oncotracer/project/input/Sample_A_R2.fastq.gz # confirm R2 exists and is non-empty
gzip -t /absolute/path/oncotracer/project/input/Sample_A_R1.fastq.gz # test R1 gzip integrity
gzip -t /absolute/path/oncotracer/project/input/Sample_A_R2.fastq.gz # test R2 gzip integrity
```

## ONT barcode FASTQ folders

Point `ont_folder` to the `fastq_pass` directory containing barcode folders:

```text
fastq_pass/
├── barcode01/
│   └── reads_001.fastq.gz
└── barcode02/
    └── reads_001.fastq.gz
```

The YAML lists must match by position:

```yaml
ont_barcodes: barcode01,barcode02     # folder names
ont_sample_names: Patient_A,Patient_B # Patient_A maps to barcode01; Patient_B maps to barcode02
```

Inspect the tree before running:

```bash
find project/input/fastq_pass -maxdepth 2 -type d | sort # list barcode folders
find project/input/fastq_pass -maxdepth 2 -type f | head # show FASTQ files
```

## Pathology CSV

Pathology concordance needs at least three concepts: a sample identifier matching OncoTracer, a case identifier, and diagnosis text. The header names can differ because the YAML tells OncoTracer which columns to use.

### Step 1: start from the minimized example

```bash
cp examples/pathology/anonymized_pathology_example.csv project/input/pathology.csv # copy the repository format example
nano project/input/pathology.csv                                                   # inspect or replace rows in nano
```

For real work, export only the minimum fields required from the pathology system. Do not add names, national identifiers, addresses, dates of birth, insurance data, or unnecessary narrative text.

### Step 2: use matching sample IDs

```csv
illumina_sample_id,case_code,final_diagnosis
Sample_A,Case_001,Diffuse large B-cell lymphoma
Sample_B,Case_002,Reactive lymphoid tissue
```

The samplesheet must contain the same IDs:

```csv
sample,fastq_1,fastq_2,status
Sample_A,/absolute/path/oncotracer/project/input/Sample_A_R1.fastq.gz,/absolute/path/oncotracer/project/input/Sample_A_R2.fastq.gz,tumor
Sample_B,/absolute/path/Sample_B_R1.fastq.gz,/absolute/path/Sample_B_R2.fastq.gz,tumor
```

`Sample_A` is not the same as `sample_a`; matching is exact.

### Step 3: point the pathology YAML at those columns

Copy the pathology-enabled template:

```bash
cp params/illumina.pathology.example.yml params/my_illumina_pathology.yml # make an editable run YAML
nano params/my_illumina_pathology.yml                                    # replace example paths and column names
```

```yaml
run_cna_classifier: true
pathology_csv: /absolute/path/oncotracer/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
```

### Step 4: validate before computation

```bash
head -5 project/input/illumina.samplesheet.csv # inspect sample IDs
head -5 project/input/pathology.csv            # inspect matching pathology IDs
nextflow run main.nf -stub-run --docker -params-file params/my_illumina_pathology.yml # validate the complete configuration
```

Continue with the [complete Pathology and Classifier tutorial](configuration/pathology.md).
