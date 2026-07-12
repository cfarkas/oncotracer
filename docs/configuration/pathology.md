# Pathology and Classifier

## Is this another YAML?

It is a **separate copied run YAML**, but not a different file format or a different workflow. Start from the Illumina pathology template, edit it, and pass it to the same `main.nf`. It contains the normal Illumina FASTQ settings plus extra keys that enable CNA classification and pathology concordance.

```text
oncotracer/
├── main.nf
├── examples/
│   └── pathology/
│       └── anonymized_pathology_example.csv  # format example from the sanitized project table
├── params/
│   ├── illumina.pathology.example.yml         # versioned template; do not edit directly
│   └── my_illumina_pathology.yml              # your copied YAML
└── project/
    ├── input/
    │   ├── illumina.samplesheet.csv
    │   └── pathology.csv                      # your matched, minimized pathology table
    └── runs/
```

## The two files have different jobs

- The **YAML** tells OncoTracer where files are and which columns/settings to use.
- The **pathology CSV** contains one row per matched sample/case and the diagnosis text.

The public pathology example does not match the public DRR000542 FASTQ test. It teaches the format only. Your pathology sample identifiers must match your own Illumina samplesheet and CNA sample names exactly.

## Required pathology columns

A minimal table is:

```csv
illumina_sample_id,case_code,final_diagnosis
Sample_A,Case_001,Diffuse large B-cell lymphoma
Sample_B,Case_002,Reactive lymphoid tissue
```

| Column in this example | How OncoTracer uses it |
| --- | --- |
| `illumina_sample_id` | Joins the pathology row to the CNA sample. It must equal the samplesheet `sample` value exactly, including capitalization. |
| `case_code` | An anonymized case or patient grouping identifier. |
| `final_diagnosis` | Diagnosis text used for compatibility/concordance reporting. |

Your institution can use different header names. Point the YAML keys to the names you actually use.

## Complete step-by-step example

### 1. Clone and enter the repository

```bash
git clone https://github.com/cfarkas/oncotracer.git  # clone OncoTracer
cd oncotracer                                        # run main.nf from this directory
current_dir=$(pwd)                                   # save the repository path
echo $current_dir                                    # confirm the current directory
```

### 2. Create a simple project tree

```bash
mkdir -p project/input project/runs                  # create input and output folders inside the clone
cp examples/pathology/anonymized_pathology_example.csv project/input/pathology.csv # copy the format example
cp params/illumina.pathology.example.yml params/my_illumina_pathology.yml # copy the YAML template
```

For a real run, replace `project/input/pathology.csv` with a minimized export containing only matched research identifiers and the fields needed for analysis.

### 3. Make the Illumina samplesheet

```csv
sample,fastq_1,fastq_2,status
Sample_A,/absolute/path/oncotracer/project/input/Sample_A_R1.fastq.gz,/absolute/path/oncotracer/project/input/Sample_A_R2.fastq.gz,tumor
Sample_B,/absolute/path/Sample_B_R1.fastq.gz,/absolute/path/Sample_B_R2.fastq.gz,tumor
```

Save it as `project/input/illumina.samplesheet.csv`. The values `Sample_A` and `Sample_B` must also appear in the pathology sample column.

### 4. Edit the copied YAML with nano

```bash
nano params/my_illumina_pathology.yml               # open the copied YAML in nano
```

Replace its example values with absolute paths:

```yaml
mode: illumina
lpwgs_root: /absolute/path/oncotracer
outdir: /absolute/path/oncotracer/project/runs/illumina_pathology
illumina_samplesheet: /absolute/path/oncotracer/project/input/illumina.samplesheet.csv
illumina_samurai_outdir: /absolute/path/oncotracer/project/runs/illumina_pathology/01_samurai_illumina
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100

run_cna_classifier: true
cna_classifier_sample_set: broad_cancer
cna_classifier_profile: conda
pathology_csv: /absolute/path/oncotracer/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: true
pathology_biomed_local_files_only: false
force: false
```

In `nano`: use arrow keys to move, Backspace/Delete to remove example text, and type the new value after each colon. Press `Ctrl+O`, Enter, then `Ctrl+X`.

### 5. Check identifiers before running

```bash
head -5 project/input/illumina.samplesheet.csv       # inspect Illumina sample names
head -5 project/input/pathology.csv                  # inspect pathology sample names
csvcut -c sample project/input/illumina.samplesheet.csv | sort -u # list samplesheet identifiers
csvcut -c illumina_sample_id project/input/pathology.csv | sort -u # list pathology identifiers
```

If `csvcut` is unavailable, inspect the two files manually. Do not proceed until the identifiers match.

### 6. Validate and run

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina_pathology.yml # validate the YAML without analysis
nextflow run main.nf --docker -params-file params/my_illumina_pathology.yml -resume   # run Illumina CNA plus classifier/pathology reports
```

### 7. Find the results

Core CNA outputs remain under the numbered OncoTracer directories. Optional classifier outputs include sample classifications, reports, literature/driver summaries, and `07_pathology/pathology_concordance.tsv` inside the classifier result directory.

| YAML field | Meaning |
| --- | --- |
| `run_cna_classifier` | Enables optional classifier and report processes. |
| `cna_classifier_sample_set` | Restricts classification labels and knowledge to the chosen disease context. |
| `cna_classifier_profile` | Runtime profile for the nested classifier workflow. |
| `pathology_csv` | Absolute path to the matched pathology table; use `null` when no table is available. |
| `pathology_sample_col` | Header containing sample IDs matching OncoTracer sample names. |
| `pathology_case_col` | Header containing case IDs. |
| `pathology_diagnosis_col` | Header containing diagnosis text. |
| `pathology_use_biomed_models` | Enables biomedical-model-assisted concordance behavior. |
| `pathology_biomed_local_files_only` | Restricts model use to files already cached locally. |

!!! danger "Research use only"
    Pathology agreement means CNA compatibility, not diagnostic confirmation. Review morphology, IHC, tumor fraction, sequencing depth, and orthogonal molecular assays.
