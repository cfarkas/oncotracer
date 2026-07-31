# Models and Pathology

This page explains the optional stage enabled by `run_cna_classifier: true`. For matched CSV files and run commands, begin with [Pathology and classifier configuration](configuration/pathology.md).

## Classifier input

The classifier does not read FASTQs directly. The core workflow first creates:

```text
03_cna_codification/cna_events.tsv
```

The optional stage summarizes CNA burden, recurrent regions, cytobands, and context-associated patterns. A pathology CSV is joined by exact sample identifier.

## Choose the context before analysis

`cna_classifier_sample_set` limits the labels and knowledge catalog used during interpretation. Select the narrowest context justified by the study design, not the result you prefer.

| Example context | Use |
| --- | --- |
| `broad_cancer` | Exploratory pan-cancer cohort |
| `lymphoma` | Cohort already established as lymphoma-focused |
| `breast`, `brain`, `colon`, `pancreas`, and other supported contexts | Study whose inclusion criteria establish that context |

## Minimal and model-assisted modes

Start with deterministic pathology comparison:

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer
cna_classifier_profile: conda
pathology_csv: /path/to/my/directory/oncotracer/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true
```

After that succeeds, optional model assistance can be enabled:

```yaml
pathology_use_biomed_models: true
pathology_biomed_local_files_only: false
```

The first model-assisted run may download model packages and use substantial disk space. OncoTracer records attempts in `07_pathology/pathology_model_trials.tsv`.

## What CNA data can and cannot show

The classifier can use broad and focal gains/losses, amplifications, deep losses, altered-genome burden, aneuploidy, recurrent cytobands, and cataloged driver regions.

Low-pass read-depth CNA analysis does not reliably determine:

- single-nucleotide variants or small insertions/deletions;
- balanced translocations or most gene fusions;
- methylation class or RNA/protein expression;
- copy-neutral loss of heterozygosity;
- clonality or biallelic inactivation without other evidence.

## Read the outputs

```bash
# Set the standard classifier output path.
REPO_DIR=/path/to/my/directory/oncotracer
OUT="$REPO_DIR/project/results/illumina_pathology/05_cna_classifier"

# Inspect prepared features, classification, pathology comparison, and status.
sed -n '1,8p' "$OUT/01_prepared/sample_cna_summary.tsv"
sed -n '1,8p' "$OUT/02_classification/cna_patient_classification.tsv"
sed -n '1,8p' "$OUT/07_pathology/pathology_concordance.tsv"
sed -n '1,80p' "$OUT/07_pathology/pathology_status.txt"
```

```bash
# Open the cohort report on a workstation with a graphical desktop.
REPO_DIR=/path/to/my/directory/oncotracer
OUT="$REPO_DIR/project/results/illumina_pathology/05_cna_classifier"
xdg-open "$OUT/03_report/cna_classifier_report.html"
```

On a remote server, copy the report directory to a workstation.

| Location | Contents | Status |
| --- | --- | --- |
| `01_prepared/` | Event and feature tables | Derived classifier input |
| `02_classification/` | CNA-pattern class and scores | Research interpretation |
| `03_report/` | HTML and PDF reports | Presentation layer |
| `04_gistic2/`, `05_gistic2_parsed/` | Optional cohort recurrence analysis | Cohort-level research output |
| `06_knowledge/` | Driver-region and literature summaries | Requires expert review |
| `07_pathology/` | Matching, concordance, status, and model trials | Compatibility assessment |

## Interpret concordance carefully

A concordance result asks whether CNA features are compatible with the supplied diagnosis in the selected context. A disagreement or indeterminate result may reflect low tumor fraction, low sequencing depth, a CNA-quiet tumor, sample mismatch, an incomplete knowledge catalog, or alterations that LP-WGS cannot detect.

Review sample matching, coverage, segmentation, CNA events, morphology, immunohistochemistry, cytogenetics, and validated molecular tests before drawing conclusions.

## Reproducibility and privacy

- Record the OncoTracer commit, image identity, sample-set context, and YAML.
- Preserve the de-identified pathology-column mapping.
- Do not include names, national identifiers, birth dates, or unnecessary clinical text.
- Enable network model retrieval only when governance permits it.
- Manually verify literature references and generated summaries.

Classifier scores and pathology compatibility are research outputs, not diagnostic confirmation or a medical device result.
