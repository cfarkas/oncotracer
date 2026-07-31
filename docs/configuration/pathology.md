# Pathology and Classifier Configuration

Pathology is an optional section in the same Illumina run YAML. OncoTracer first completes the CNA workflow, then the optional classifier compares CNA-derived results with a supplied pathology table.

Start without this feature when only CNA calls and plots are needed. Enable it after a standard Illumina run works.

The examples use `/path/to/my/directory/oncotracer` as the repository path. The example FASTQs are not distributed with the repository; replace them with your own research data.

## Files that must match

| File | Purpose | Matching identifier |
| --- | --- | --- |
| Illumina samplesheet | Points to each FASTQ or R1/R2 pair | `sample` |
| Pathology CSV | Contains sample, case, and diagnosis fields | `illumina_sample_id` in this example |
| Run YAML | Points to both files and names the columns | No sample rows are stored in YAML |

Matching is exact and case-sensitive.

## Recommended layout

```text
/path/to/my/directory/oncotracer/
├── main.nf
├── params/
│   └── my_illumina_pathology.yml
└── project/
    ├── input/
    │   ├── I7738_R1.fastq.gz
    │   ├── I7738_R2.fastq.gz
    │   ├── V480_R1.fastq.gz
    │   ├── V480_R2.fastq.gz
    │   ├── illumina.samplesheet.csv
    │   └── pathology.csv
    └── results/
```

## 1. Create the directories and copy the template

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
mkdir -p "$PROJECT_DIR/input" "$PROJECT_DIR/results"

# Copy the pathology-enabled YAML template.
cp "$REPO_DIR/params/illumina.pathology.example.yml" \
  "$REPO_DIR/params/my_illumina_pathology.yml"
```

Place the four example-named FASTQs, or your own equivalently named FASTQs, under `project/input/` before continuing.

## 2. Create the Illumina samplesheet

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Create or replace the paired-end samplesheet.
cat > "$PROJECT_DIR/input/illumina.samplesheet.csv" <<CSV
sample,fastq_1,fastq_2,status
I7738,$PROJECT_DIR/input/I7738_R1.fastq.gz,$PROJECT_DIR/input/I7738_R2.fastq.gz,tumor
V480,$PROJECT_DIR/input/V480_R1.fastq.gz,$PROJECT_DIR/input/V480_R2.fastq.gz,tumor
CSV

# Display the saved samplesheet.
cat "$PROJECT_DIR/input/illumina.samplesheet.csv"
```

## 3. Create the matching pathology CSV

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Create or replace the matched pathology table.
cat > "$PROJECT_DIR/input/pathology.csv" <<'CSV'
illumina_sample_id,case_code,final_diagnosis
I7738,2023-07738,"Glioblastoma, IDH-wildtype."
V480,2024-00480,"Diffuse large B-cell lymphoma, NOS."
CSV

# Display the saved pathology table.
cat "$PROJECT_DIR/input/pathology.csv"
```

Quotes protect diagnosis text containing commas. Use anonymized case identifiers and include only the fields required for the analysis.

## 4. Verify sample matching

```bash
# Set the standard project path.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Confirm that sequencing and pathology sample IDs match exactly.
python3 - "$PROJECT_DIR/input/illumina.samplesheet.csv" "$PROJECT_DIR/input/pathology.csv" <<'PY'
import csv
import sys

samplesheet, pathology = sys.argv[1:3]
with open(samplesheet, newline="") as handle:
    fastq_ids = {row["sample"].strip() for row in csv.DictReader(handle)}
with open(pathology, newline="") as handle:
    pathology_ids = {
        row["illumina_sample_id"].strip() for row in csv.DictReader(handle)
    }

missing_pathology = fastq_ids - pathology_ids
missing_fastq = pathology_ids - fastq_ids
if missing_pathology or missing_fastq:
    raise SystemExit(
        f"ERROR: missing pathology={sorted(missing_pathology)}; "
        f"missing FASTQ={sorted(missing_fastq)}"
    )
print("OK: every sample identifier matches exactly")
PY
```

## 5. Edit the copied YAML

```bash
# Open the copied pathology-enabled YAML.
nano /path/to/my/directory/oncotracer/params/my_illumina_pathology.yml
```

Use:

```yaml
mode: illumina
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results/illumina_pathology
illumina_samplesheet: /path/to/my/directory/oncotracer/project/input/illumina.samplesheet.csv

illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100

run_cna_classifier: true
cna_classifier_sample_set: broad_cancer
cna_classifier_profile: conda

pathology_csv: /path/to/my/directory/oncotracer/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true
force: false
```

Save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

## 6. Check and run

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Check parameters and workflow connections.
nextflow run "$REPO_DIR/main.nf" -stub-run --docker \
  -params-file "$REPO_DIR/params/my_illumina_pathology.yml"

# Run the CNA analysis and optional pathology comparison.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/params/my_illumina_pathology.yml" \
  -resume
```

On HPC, replace `--docker` with `--singularity`.

## 7. Inspect the result

```bash
# Set the standard output path.
REPO_DIR=/path/to/my/directory/oncotracer
OUT="$REPO_DIR/project/results/illumina_pathology"

# Read the workflow summary and optional classifier tables.
cat "$OUT/06_workflow_summary/workflow_summary.txt"
sed -n '1,8p' \
  "$OUT/05_cna_classifier/02_classification/cna_patient_classification.tsv"
sed -n '1,8p' \
  "$OUT/05_cna_classifier/07_pathology/pathology_concordance.tsv"
```

## Pathology settings

| Field | Meaning |
| --- | --- |
| `run_cna_classifier` | Enables the optional classifier/report stage |
| `cna_classifier_sample_set` | Biological context selected from the study design |
| `pathology_csv` | Absolute path to the matched pathology table |
| `pathology_sample_col` | Header matching samplesheet `sample` values |
| `pathology_case_col` | Header containing anonymized case identifiers |
| `pathology_diagnosis_col` | Header containing diagnosis text |
| `pathology_use_biomed_models` | Enables optional biomedical model assistance |
| `pathology_biomed_local_files_only` | Restricts models to an existing local cache |

Pathology concordance is a research comparison. It cannot replace morphology, immunohistochemistry, tumor-fraction assessment, orthogonal molecular tests, or expert review.
