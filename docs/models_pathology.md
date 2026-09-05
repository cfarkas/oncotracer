# Models and pathology interpretation

This page explains the optional native stage enabled by `run_cna_classifier: true`. For matched CSV creation and complete commands, begin with [Pathology and classifier configuration](configuration/pathology.md).

## Classifier input

The classifier does not read FASTQs directly. Core analysis first creates:

```text
03_cna_codification/cna_events.tsv
```

The classifier derives CNA burden, recurrent regions, cytobands, gene-region overlaps, and context-associated patterns from those final event/refined-bin products. A pathology CSV is joined by exact sample identifier.

## Choose the context before analysis

`cna_classifier_sample_set` limits labels and knowledge resources used during interpretation. Select the narrowest context justified by inclusion criteria, not by the result you prefer.

| Example context | Appropriate use |
| --- | --- |
| `broad_cancer` | Exploratory pan-cancer cohort |
| `lymphoma` | Cohort already established as lymphoma-focused |
| `brain_cns` | CNS tumor cohort defined independently of the CNA result |
| `breast` | Breast-tumor/cell-line study |
| `colorectal` | Colorectal study |
| `sarcoma` | Sarcoma-focused study |
| `leukemia` | Hematologic study for which CNA profiling is appropriate |
| `pediatric_solid` | Pediatric solid-tumor cohort |

The full context list is in [Native YAML configuration](configuration_v2.md).

## Deterministic initial mode

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer

pathology_csv: /absolute/path/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true

run_gistic: true
gistic_required: false
knowledge_web: false
knowledge_literature_llm: false
knowledge_deep_literature: false
knowledge_deep_enable_llm_ranker: false
```

This mode is easiest to reproduce and review. Enable network/model assistance only after the deterministic route succeeds and governance permits it.

## Optional pathology matching models

```yaml
pathology_use_biomed_models: true
pathology_biomed_local_files_only: false
```

These models score compatibility with supplied pathology text; they do not write
the report's literature paragraphs. Attempts are recorded in
`07_pathology/pathology_model_trials.tsv`. The first run may download model weights.

For the machinery that writes report text, see [LLM-assisted reports](llm_reports.md).

## What CNA data can support

The classifier can summarize:

- broad and focal gains/losses;
- high-level amplifications and deep losses;
- altered-genome burden and aneuploidy;
- recurrent cytobands and cataloged driver regions;
- cohort recurrence when GISTIC2 is enabled and scientifically appropriate.

LP-WGS read-depth CNA analysis does not reliably determine:

- SNVs or small insertions/deletions;
- balanced translocations or most gene fusions;
- methylation class or RNA/protein expression;
- copy-neutral loss of heterozygosity;
- clonality or biallelic inactivation without orthogonal evidence.

## Read the outputs

```bash
OUT="$PWD/project/results/illumina_pathology/05_cna_classifier"

sed -n '1,12p' "$OUT/01_prepared/sample_cna_summary.tsv"
sed -n '1,12p' "$OUT/02_classification/cna_patient_classification.tsv"
sed -n '1,12p' "$OUT/06_knowledge/sample_knowledge_summary.tsv"
sed -n '1,12p' "$OUT/07_pathology/pathology_concordance.tsv"
sed -n '1,120p' "$OUT/07_pathology/pathology_status.txt"
ls -lh "$OUT/03_report/cna_classifier_report.html"
```

| Location | Contents | Status |
| --- | --- | --- |
| `01_prepared/` | Event and feature tables | Derived classifier input |
| `02_classification/` | Context scores and classes | Research interpretation |
| `03_report/` | HTML, PDF, and clinician summaries | Presentation layer |
| `04_gistic2/`, `05_gistic2_parsed/` | Optional recurrence analysis | Cohort-level research output |
| `06_knowledge/` | Driver-region/literature summaries | Requires expert verification |
| `07_pathology/` | Matching, concordance, status, model trials | Compatibility assessment |

## Interpret concordance carefully

Concordance asks whether CNA features are compatible with the supplied diagnosis in the selected context. A disagreement or indeterminate result may reflect low tumor fraction, low sequencing depth, CNA-quiet biology, sample mismatch, an incomplete catalog, or alterations that LP-WGS cannot detect.

Review sample identity, coverage, segmentation, event tables, morphology, immunohistochemistry, cytogenetics, methylation/fusion testing, and validated molecular assays before drawing conclusions.

## Reproducibility and privacy

- Record OncoTracer version/commit, YAML, context, backend, and image/prefix identity.
- Preserve de-identified pathology-column mappings and matching results.
- Do not include names, national identifiers, birth dates, or unnecessary clinical text.
- Enable network retrieval only when approved.
- Manually verify literature references and generated summaries.

Classifier scores and pathology compatibility are research outputs, not diagnostic confirmation or a medical-device result.
