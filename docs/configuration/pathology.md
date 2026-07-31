# Pathology and classifier configuration

Pathology is an optional section in the same Illumina YAML. OncoTracer completes the CNA workflow first, then runs the research classifier. When a pathology CSV is supplied, the classifier compares the CNA-based result with the diagnosis text.

The example identifiers and FASTQ filenames below are placeholders. The repository includes a sample pathology CSV format, but it does not include the corresponding FASTQs.

## Files that must match

| File | Matching field |
| --- | --- |
| Illumina samplesheet | `sample` |
| Pathology CSV | `illumina_sample_id` in this example |
| Run YAML | Names the pathology file and its columns |

Matching is exact and case-sensitive.

## 1. Create the project folders

```bash
# Clone the repository.
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer

# Enter the repository.
cd /home/student/oncotracer

# Create input and result directories.
mkdir -p project/input project/results

# Copy the pathology-enabled Illumina template.
cp params/illumina.pathology.example.yml params/my_illumina_pathology.yml
```

Place your FASTQs in `project/input/` before continuing.

```bash
# List the FASTQs supplied by the user.
find project/input -maxdepth 1 -type f -name '*.fastq.gz' -print | sort
```

## 2. Create the Illumina samplesheet

```bash
# Create the FASTQ-to-sample table.
nano project/input/illumina.samplesheet.csv
```

Paste exactly this content after replacing `/home/student/oncotracer` when the repository is elsewhere:

```csv
sample,fastq_1,fastq_2,status
I7738,/home/student/oncotracer/project/input/I7738_R1.fastq.gz,/home/student/oncotracer/project/input/I7738_R2.fastq.gz,tumor
V480,/home/student/oncotracer/project/input/V480_R1.fastq.gz,/home/student/oncotracer/project/input/V480_R2.fastq.gz,tumor
```

## 3. Create the matching pathology CSV

```bash
# Create the pathology table.
nano project/input/pathology.csv
```

Paste exactly this content:

```csv
illumina_sample_id,case_code,final_diagnosis
I7738,2023-07738,"Glioblastoma, IDH-wildtype."
V480,2024-00480,"Infiltration by diffuse large B-cell non-Hodgkin lymphoma, NOS, in fibroadipose and skeletal muscle tissue."
```

Quotes are required when diagnosis text contains commas.

## 4. Verify the sample identifiers

```bash
# Compare the samplesheet and pathology identifiers with Python.
python3 - <<'PY'
import csv

with open('project/input/illumina.samplesheet.csv', newline='') as handle:
    fastq_ids = {row['sample'].strip() for row in csv.DictReader(handle)}
with open('project/input/pathology.csv', newline='') as handle:
    pathology_ids = {row['illumina_sample_id'].strip() for row in csv.DictReader(handle)}

missing_pathology = fastq_ids - pathology_ids
missing_fastq = pathology_ids - fastq_ids
print('FASTQ samples:    ', sorted(fastq_ids))
print('Pathology samples:', sorted(pathology_ids))
if missing_pathology or missing_fastq:
    raise SystemExit(
        f'ERROR: missing pathology={sorted(missing_pathology)}; '
        f'missing FASTQ={sorted(missing_fastq)}'
    )
print('OK: every sample identifier matches exactly')
PY
```

## 5. Edit the YAML

```bash
# Edit the copied pathology-enabled YAML.
nano params/my_illumina_pathology.yml
```

Use the real absolute paths for your project:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/results/illumina_pathology
illumina_samplesheet: /home/student/oncotracer/project/input/illumina.samplesheet.csv

illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100

run_cna_classifier: true
cna_classifier_sample_set: broad_cancer
cna_classifier_profile: conda

pathology_csv: /home/student/oncotracer/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true
force: false
```

The first classifier run disables biomedical language models to avoid large model downloads. Enable them only when the model cache and study plan are prepared.

## 6. Check and run

```bash
# Inspect the saved YAML.
sed -n '1,200p' params/my_illumina_pathology.yml

# Check workflow wiring without running the scientific tools.
nextflow run main.nf -stub-run --docker \
  -params-file params/my_illumina_pathology.yml

# Run or resume the CNA and pathology workflow with Docker.
nextflow run main.nf --docker \
  -params-file params/my_illumina_pathology.yml \
  -resume
```

Use `--singularity` instead of `--docker` on a configured HPC system.

## 7. Inspect the result

```bash
# Set the output directory used in the YAML.
OUT="$PWD/project/results/illumina_pathology"

# Read the workflow summary.
cat "$OUT/06_workflow_summary/workflow_summary.txt"

# Inspect CNA-based research classifications.
sed -n '1,12p' "$OUT/05_cna_classifier/02_classification/cna_patient_classification.tsv"

# Inspect the matched pathology comparison.
sed -n '1,12p' "$OUT/05_cna_classifier/07_pathology/pathology_concordance.tsv"
```

## Pathology settings

| YAML field | Purpose |
| --- | --- |
| `run_cna_classifier` | Enable the optional classifier and reports |
| `cna_classifier_sample_set` | Biological context selected before examining results |
| `pathology_csv` | Absolute path to the matched pathology table |
| `pathology_sample_col` | Column matching samplesheet `sample` values |
| `pathology_case_col` | Anonymized case identifier column |
| `pathology_diagnosis_col` | Diagnosis text column |
| `pathology_use_biomed_models` | Enable optional biomedical language models |
| `pathology_biomed_local_files_only` | Use only an existing local model cache |

Pathology comparison is a research interpretation. It does not replace morphology, IHC, tumor-fraction assessment, orthogonal molecular testing, or expert review.
