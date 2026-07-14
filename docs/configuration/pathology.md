# Pathology and classifier configuration

Pathology is **not a second pipeline**. It is an optional section in the same Illumina run YAML. When enabled, OncoTracer first completes the CNA workflow and then sends the CNA event table to the research classifier. If a pathology table is also supplied, the classifier compares its CNA-based result with the diagnosis text.

Start without this feature if you only need CNA calls and plots. Enable it after a normal Illumina run works.

## The three files and how they connect

| File | Purpose | Identifier that must match |
| --- | --- | --- |
| Illumina samplesheet | Points to each R1/R2 FASTQ pair | `sample` |
| Pathology CSV | Holds the case identifier and diagnosis | `illumina_sample_id` in this example |
| Run YAML | Points OncoTracer to both files and names the pathology columns | No sample rows are stored here |

The join is exact and case-sensitive. `I7738` matches `I7738`; `i7738`, `I7738_R1`, and `Case_001` do not.

## Recommended folder layout

Keep every input and output below `lpwgs_root` so Docker or Singularity can see it.

```text
oncotracer/
├── main.nf
├── params/
│   ├── illumina.pathology.example.yml     # repository template
│   └── my_illumina_pathology.yml          # your editable copy
└── project/                               # lpwgs_root in this example
    ├── input/
    │   ├── I7738_R1.fastq.gz
    │   ├── I7738_R2.fastq.gz
    │   ├── V480_R1.fastq.gz
    │   ├── V480_R2.fastq.gz
    │   ├── illumina.samplesheet.csv
    │   └── pathology.csv
    └── runs/
```

The anonymized repository table at `examples/pathology/anonymized_pathology_example.csv` demonstrates the accepted three-column format. It is a format example, not FASTQ data.

## Complete matched example

The following walkthrough deliberately uses `I7738` and `V480` in both CSV files. Their FASTQs are not distributed with this repository, so this is a matched file-configuration example rather than a public sequencing test. Substitute your own research identifiers and FASTQs in a real run.

### 1. Clone the repository and make folders

```bash
git clone https://github.com/cfarkas/oncotracer.git  # download OncoTracer
cd oncotracer                                        # main.nf is here
mkdir -p project/input project/runs                  # inputs and outputs remain under one visible root
cp params/illumina.pathology.example.yml params/my_illumina_pathology.yml # preserve the original template
realpath project                                     # copy this absolute path for the YAML
```

Copy your four FASTQ files into `project/input/` before continuing. A symbolic link is safe only when its target is also below `lpwgs_root`; otherwise the container cannot follow it. Confirm their names:

```bash
find project/input -maxdepth 1 -type f -name '*.fastq.gz' -print | sort # expect two files per sample
```

### 2. Create the Illumina samplesheet

Open a new file:

```bash
nano project/input/illumina.samplesheet.csv
```

Paste this content, replacing `/absolute/path/oncotracer` with the result of `realpath .`:

```csv
sample,fastq_1,fastq_2,status
I7738,/absolute/path/oncotracer/project/input/I7738_R1.fastq.gz,/absolute/path/oncotracer/project/input/I7738_R2.fastq.gz,tumor
V480,/absolute/path/oncotracer/project/input/V480_R1.fastq.gz,/absolute/path/oncotracer/project/input/V480_R2.fastq.gz,tumor
```

Save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`.

### 3. Create the matching pathology CSV

```bash
nano project/input/pathology.csv
```

Paste:

```csv
illumina_sample_id,case_code,final_diagnosis
I7738,2023-07738,"Glioblastoma, IDH-wildtype."
V480,2024-00480,"Infiltration by diffuse large B-cell non-Hodgkin lymphoma, NOS, in fibroadipose and skeletal muscle tissue."
```

The quotes protect diagnoses that contain commas. Save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`.

Your headers may be different. For example, a table headed `sample_id,case_id,diagnosis` is valid when the YAML names those three headers exactly.

### 4. Verify the join with Python

This uses Python's standard CSV module and prints a clear error if either file contains an unmatched sample:

```bash
python3 - <<'PY'
import csv

with open('project/input/illumina.samplesheet.csv', newline='') as handle:
    fastq_ids = {row['sample'].strip() for row in csv.DictReader(handle)}
with open('project/input/pathology.csv', newline='') as handle:
    pathology_ids = {row['illumina_sample_id'].strip() for row in csv.DictReader(handle)}

print('FASTQ samples:    ', sorted(fastq_ids))
print('Pathology samples:', sorted(pathology_ids))
missing_pathology = fastq_ids - pathology_ids
missing_fastq = pathology_ids - fastq_ids
if missing_pathology or missing_fastq:
    raise SystemExit(f'ERROR: missing pathology={sorted(missing_pathology)}; missing FASTQ={sorted(missing_fastq)}')
print('OK: every sample identifier matches exactly')
PY
```

Expected last line:

```text
OK: every sample identifier matches exactly
```

### 5. Edit the copied run YAML

```bash
nano params/my_illumina_pathology.yml
```

Make it look like this, using the real absolute path of your clone:

```yaml
mode: illumina
lpwgs_root: /absolute/path/oncotracer/project
outdir: /absolute/path/oncotracer/project/runs/illumina_pathology
illumina_samplesheet: /absolute/path/oncotracer/project/input/illumina.samplesheet.csv

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
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true
force: false
```

Save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`.

For a first classifier run, the example disables biomedical language models. This avoids large model downloads and makes setup easier to diagnose. Enable them later only after reading [Models and pathology](../models_pathology.md).

!!! note "The classifier uses Conda"
    `cna_classifier_profile: conda` starts the nested optional classifier environment. With `--docker`, Conda is supplied inside the OncoTracer image; with a native `--conda` run, confirm `conda --version` works on the host. A normal run with `run_cna_classifier: false` skips this optional environment.

### 6. Validate, then run

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina_pathology.yml # check parameters and workflow connections
nextflow run main.nf --docker -params-file params/my_illumina_pathology.yml -resume   # run CNA analysis and optional interpretation
```

The stub run checks workflow wiring but does not analyze FASTQs. Only the second command performs the real analysis.

### 7. Inspect the result

```bash
OUT="$PWD/project/runs/illumina_pathology"                                  # use the same outdir as the YAML
cat "$OUT/06_workflow_summary/workflow_summary.txt"                         # locate core outputs
sed -n '1,8p' "$OUT/05_cna_classifier/02_classification/cna_patient_classification.tsv" # CNA-based research classes
sed -n '1,8p' "$OUT/05_cna_classifier/07_pathology/pathology_concordance.tsv"            # matched pathology comparison
```

See [Output files](../outputs.md) for the difference between caller output, refined segments, final event tables, plots, and optional research interpretation.

## What each pathology setting means

| YAML field | Meaning |
| --- | --- |
| `run_cna_classifier` | Adds the optional classifier/report/pathology stage when `true`. |
| `cna_classifier_sample_set` | Sets the biological context. Choose it from study design, not from the result you prefer. |
| `cna_classifier_profile` | Runtime used by the nested classifier; the supplied template uses Conda. |
| `pathology_csv` | Absolute path to the matched table. Use `null` when no pathology table is available. |
| `pathology_sample_col` | Header containing identifiers equal to samplesheet `sample` values. |
| `pathology_case_col` | Header containing anonymized case or patient identifiers. |
| `pathology_diagnosis_col` | Header containing diagnosis text. |
| `pathology_use_biomed_models` | Allows optional biomedical language-model assistance. Start with `false`. |
| `pathology_biomed_local_files_only` | When `true`, prevents model retrieval and uses only an existing local cache. |

!!! danger "Research use only"
    Pathology concordance means that a CNA pattern is more or less compatible with supplied text under the chosen context. It is not diagnostic confirmation and cannot replace morphology, IHC, tumor-fraction assessment, orthogonal molecular tests, or expert review.
