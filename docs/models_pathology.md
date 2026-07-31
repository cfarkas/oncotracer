# Models and Pathology

This page explains the optional stage enabled by `run_cna_classifier: true`. For a complete matched-file example, see [Pathology and Classifier Configuration](configuration/pathology.md).

## What enters the classifier

The classifier reads the final CNA event table, not the FASTQs directly:

```text
03_cna_codification/cna_events.tsv
```

It summarizes CNA burden, recurrent regions, cytobands, and context-associated patterns. A pathology CSV, when supplied, is joined by the exact sample identifier.

## Choose the context before examining results

`cna_classifier_sample_set` selects the biological context used for research interpretation.

| Context | Use |
| --- | --- |
| `broad_cancer` | Exploratory pan-cancer cohort without a defensible narrower context |
| `lymphoma` | Cohort established as lymphoma-focused |
| `breast`, `brain_cns`, `colorectal`, `pancreas`, and other supported contexts | Study whose inclusion criteria already establish that context |

Do not try several contexts and report only the preferred result.

## Start without language models

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer
cna_classifier_profile: conda
pathology_csv: /home/student/oncotracer/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true
```

After the deterministic run succeeds, optional biomedical-model assistance can be enabled:

```yaml
pathology_use_biomed_models: true
pathology_biomed_local_files_only: false
```

The first model-assisted run may download several model packages. On an offline system, prepare the cache first and keep `pathology_biomed_local_files_only: true`.

## What CNA data can and cannot show

The classifier can use broad and focal gains or losses, amplifications, deep losses, altered-genome burden, aneuploidy, recurrent cytobands, and cataloged driver regions.

Low-pass read-depth CNA analysis does not reliably determine SNVs, small indels, balanced translocations, most fusions, methylation class, RNA or protein expression, copy-neutral LOH, clonality, or biallelic inactivation.

## Read outputs in this order

```bash
# Set the classifier result directory.
OUT="$PWD/project/results/illumina_pathology/05_cna_classifier"

# Inspect the CNA features supplied to classification.
sed -n '1,12p' "$OUT/01_prepared/sample_cna_summary.tsv"

# Inspect the CNA-pattern research classifications.
sed -n '1,12p' "$OUT/02_classification/cna_patient_classification.tsv"

# Inspect the comparison with supplied pathology.
sed -n '1,12p' "$OUT/07_pathology/pathology_concordance.tsv"

# Inspect matching, model status, and warnings.
sed -n '1,120p' "$OUT/07_pathology/pathology_status.txt"
```

```bash
# Open the cohort HTML report on a graphical Linux workstation.
xdg-open "$OUT/03_report/cna_classifier_report.html"
```

On a remote server, copy the HTML/PDF report directory to a workstation.

| Directory | Contents |
| --- | --- |
| `01_prepared/` | Per-sample CNA features supplied to the classifier |
| `02_classification/` | CNA-pattern classes and scores |
| `03_report/` | Cohort and per-sample reports |
| `04_gistic2/`, `05_gistic2_parsed/` | Optional cohort recurrence analysis |
| `06_knowledge/` | Driver-region and literature-linked summaries |
| `07_pathology/` | Matching, comparison, status, and model trials |

## Interpreting pathology comparison

A compatible or incompatible result is a research review flag. It can be affected by tumor fraction, sequencing depth, CNA-flat biology, sample mismatch, an incomplete knowledge catalog, or alterations that LP-WGS cannot detect.

Review it in this order:

1. confirm exact sequencing-to-pathology identifier matching;
2. inspect coverage, segmentation, and tumor content;
3. review the primary CNA events and supporting regions;
4. compare with morphology, IHC, cytogenetics, and clinical-grade molecular tests;
5. document the final human interpretation separately.

## Reproducibility and privacy

- Record the OncoTracer commit, image identity, sample-set context, and YAML.
- Preserve a de-identified case key under appropriate governance.
- Do not include names, national identifiers, birth dates, or unnecessary clinical text.
- Do not enable network model retrieval unless institutional policy permits it.
- Verify literature references and model-produced summaries manually.

Classifier scores, driver summaries, literature links, and pathology compatibility are research outputs. They do not replace a validated diagnostic assay or expert pathology review.
