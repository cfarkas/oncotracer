# Pathology and native classifier configuration

Pathology is optional. Native v2 first completes the CNA workflow and then, when `run_cna_classifier: true`, derives CNA features, context-aware classifications, optional GISTIC2 recurrence results, knowledge summaries, HTML/PDF reports, clinician-oriented summaries, and a matched pathology compatibility table.

The classifier does not replace the stage-02/03 CNA tables. Start with a standard CNA-only run, confirm its QC and outputs, and then enable this stage in a new YAML and `outdir`.

## Files that must agree

| File | Purpose | Matching identifier |
| --- | --- | --- |
| Illumina samplesheet or ONT mapping | Defines the sequencing sample | `sample` or generated sample name |
| Pathology CSV | Supplies case and diagnosis fields | Column selected by `pathology_sample_col` |
| Flat YAML | Points to both inputs and names the columns | No sample rows are stored in YAML |

Matching is exact and case-sensitive.

## Recommended project layout

```text
project/
├── input/
│   ├── fastq/
│   │   ├── I7738_R1.fastq.gz
│   │   ├── I7738_R2.fastq.gz
│   │   ├── V480_R1.fastq.gz
│   │   └── V480_R2.fastq.gz
│   ├── samples.csv
│   └── pathology.csv
├── config/
└── results/
```

## Step 1. Create the sequencing sample table

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/input/fastq" "$PROJECT_DIR/config" "$PROJECT_DIR/results"

cat > "$PROJECT_DIR/input/samples.csv" <<'CSV'
sample_name,status
I7738,TUMOR
V480,TUMOR
CSV
```

Place the matching R1/R2 FASTQs under `$PROJECT_DIR/input/fastq`.

## Step 2. Create the de-identified pathology CSV

```bash
PROJECT_DIR="$PWD/project"

cat > "$PROJECT_DIR/input/pathology.csv" <<'CSV'
illumina_sample_id,case_code,final_diagnosis
I7738,Case_07738,"Glioblastoma, IDH-wildtype."
V480,Case_00480,"Diffuse large B-cell lymphoma, NOS."
CSV

cat "$PROJECT_DIR/input/pathology.csv"
```

Quotes protect diagnosis text containing commas. Use research identifiers and include only fields required by the approved analysis.

## Step 3. Verify exact sample matching

```bash
PROJECT_DIR="$PWD/project"

python3 - \
  "$PROJECT_DIR/input/samples.csv" \
  "$PROJECT_DIR/input/pathology.csv" <<'PY'
import csv
import sys

sample_table, pathology = sys.argv[1:3]
with open(sample_table, newline="", encoding="utf-8") as handle:
    sequencing_ids = {
        row["sample_name"].strip() for row in csv.DictReader(handle)
    }
with open(pathology, newline="", encoding="utf-8") as handle:
    pathology_ids = {
        row["illumina_sample_id"].strip() for row in csv.DictReader(handle)
    }

missing_pathology = sequencing_ids - pathology_ids
missing_sequence = pathology_ids - sequencing_ids
if missing_pathology or missing_sequence:
    raise SystemExit(
        f"Missing pathology={sorted(missing_pathology)}; "
        f"missing sequencing={sorted(missing_sequence)}"
    )
print("OK: every pathology identifier matches a sequencing sample")
PY
```

## Step 4. Generate the YAML with the classifier enabled

```bash
PROJECT_DIR="$PWD/project"

oncotracer auto \
  --mode illumina \
  --reads-folder "$PROJECT_DIR/input/fastq" \
  --sample-table "$PROJECT_DIR/input/samples.csv" \
  --config-dir "$PROJECT_DIR/config" \
  --outdir "$PROJECT_DIR/results/illumina_pathology" \
  --run-cna-classifier
```

Add the study-defined context and pathology columns with the real absolute pathology path:

```bash
PROJECT_DIR="$PWD/project"
CONFIG="$PROJECT_DIR/config/illumina.auto.yml"

python3 - "$CONFIG" "$PROJECT_DIR/input/pathology.csv" <<'PY'
from pathlib import Path
import sys

config = Path(sys.argv[1])
pathology = Path(sys.argv[2]).resolve()
with config.open("a", encoding="utf-8") as handle:
    handle.write(
        f"""cna_classifier_sample_set: broad_cancer

pathology_csv: {pathology}
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true

run_gistic: true
gistic_required: false
gistic_min_samples: 2

knowledge_web: false
knowledge_literature_llm: false
knowledge_deep_literature: false
"""
    )
PY

sed -n '1,240p' "$CONFIG"
```

## Context selection

Choose `cna_classifier_sample_set` from the study design before reviewing results. Supported contexts include:

- `broad_cancer`, `lymphoma`, `brain_cns`, `breast`, `pancreas`, `colorectal`;
- `leukemia`, `lung`, `prostate`, `ovarian`, `gastric_esophageal`;
- `sarcoma`, `renal`, `urothelial`, `thyroid`, `melanoma`, `liver`;
- `head_neck`, `germ_cell`, `myeloma`, `neuroblastoma`, `neuroendocrine`;
- `pediatric_solid`.

Do not choose a context because it produces the desired classification.

## Step 5. Dry-run and execute

```bash
PROJECT_DIR="$PWD/project"
CONFIG="$PROJECT_DIR/config/illumina.auto.yml"

oncotracer run \
  --backend conda \
  --config "$CONFIG" \
  --dry-run

oncotracer run \
  --backend conda \
  --config "$CONFIG"
```

The same YAML can be run through Docker or Singularity after installing that backend.

## Step 6. Inspect classifier and pathology outputs

```bash
OUT="$PWD/project/results/illumina_pathology"
CLASSIFIER="$OUT/05_cna_classifier"

cat "$OUT/06_workflow_summary/workflow_summary.txt"
sed -n '1,12p' \
  "$CLASSIFIER/01_prepared/sample_cna_summary.tsv"
sed -n '1,12p' \
  "$CLASSIFIER/02_classification/cna_patient_classification.tsv"
sed -n '1,12p' \
  "$CLASSIFIER/07_pathology/pathology_concordance.tsv"
sed -n '1,120p' \
  "$CLASSIFIER/07_pathology/pathology_status.txt"
ls -lh "$CLASSIFIER/03_report/cna_classifier_report.html"
find "$CLASSIFIER/03_report/clinician_reports" \
  -maxdepth 1 -type f -print | sort
```

| Directory | Main contents | Interpretation level |
| --- | --- | --- |
| `01_prepared/` | CNA feature and matrix inputs | Derived from stage 03 |
| `02_classification/` | Context scores and labels | Research interpretation |
| `03_report/` | HTML/PDF and clinician summaries | Presentation layer |
| `04_gistic2/`, `05_gistic2_parsed/` | Optional cohort recurrence analysis | Cohort research output |
| `06_knowledge/` | Driver-region and literature summaries | Requires manual verification |
| `07_pathology/` | Matching, compatibility, status, model trials | Research comparison |

## Deterministic and model-assisted modes

Recommended initial mode:

```yaml
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true
knowledge_web: false
knowledge_literature_llm: false
knowledge_deep_literature: false
```

After the deterministic analysis succeeds, optional model or network enrichment can be enabled only when governance, privacy, connectivity, and reproducibility requirements permit it. Preserve `pathology_model_trials.tsv` and manually verify generated references and summaries.

## Limitations

CNA/pathology concordance asks whether the observed copy-number profile is compatible with the supplied diagnosis in the selected context. An indeterminate or discordant result can reflect low tumor fraction, low depth, CNA-quiet biology, sample mismatch, caller assumptions, or alterations that LP-WGS does not measure.

Pathology concordance cannot replace morphology, immunohistochemistry, cytogenetics, methylation, RNA/fusion assays, validated clinical sequencing, or expert review. These are research outputs, not diagnostic confirmation.
