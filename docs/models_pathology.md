# Models & Pathology

OncoTracer can add a cancer-context-aware CNA classifier, sample reports, literature-linked driver summaries, cohort plots, and pathology concordance after the core CNA workflow. This stage is optional and intended for research interpretation.

## Enable the optional stage

```yaml
run_cna_classifier: true                          # enable classifier, knowledge, and report processes
cna_classifier_sample_set: broad_cancer          # restrict labels and knowledge to the intended disease context
cna_classifier_profile: conda                    # runtime used by the nested classifier workflow
pathology_csv: /home/user/project/input/pathology.csv # optional pathology table; use null when unavailable
pathology_sample_col: illumina_sample_id          # column matching OncoTracer sample identifiers
pathology_case_col: case_code                     # case or patient identifier column
pathology_diagnosis_col: final_diagnosis          # reference diagnosis text column
pathology_use_biomed_models: true                 # enable biomedical-model-assisted concordance
pathology_biomed_local_files_only: false          # permit model retrieval; true requires a populated local cache
```

## Choose the sample-set context

`cna_classifier_sample_set` controls which CNA labels, catalogs, and interpretations are allowed. Use the narrowest defensible context.

| Context | Intended use |
| --- | --- |
| `broad_cancer` | exploratory pan-cancer screening when no narrower cohort context is justified |
| `lymphoma` | lymphoma-focused catalog and labels; suppresses unrelated solid-tumor patterns |
| `breast`, `brain`, `colon`, `pancreas`, and other supported contexts | organ- or disease-focused interpretation |

The classifier estimates a probable CNA-pattern class, not a histologic diagnosis. Context selection must come from study design or known specimen provenance, not from searching for the most favorable label.

## Pathology table

CSV, TSV, XLS, and XLSX tables are supported by the nested classifier. The sample column must match OncoTracer sample names exactly.

```csv
illumina_sample_id,case_code,final_diagnosis
Sample_A,Case_001,Diffuse large B-cell lymphoma
Sample_B,Case_002,Reactive lymphoid tissue
```

Before running, check matching identifiers:

```bash
head -5 /home/user/project/input/pathology.csv                  # inspect headers and identifiers
cut -d, -f1 /home/user/project/input/pathology.csv | sort -u   # list unique pathology sample identifiers
```

## What the models use

The classifier uses broad and focal CNA events, gains, losses, amplifications, deep losses, altered-genome burden, aneuploidy, recurrent cytobands, and context-specific driver regions. Optional biomedical models can help rank or summarize pathology compatibility and literature evidence.

Low-pass WGS CNA data cannot reliably determine SNVs, indels, balanced translocations, gene fusions, methylation class, expression, protein status, copy-neutral LOH, or biallelic inactivation. Those require orthogonal assays.

## Main outputs

The optional result directory contains prepared CNA inputs, classifications, plots/reports, optional GISTIC cohort results, knowledge summaries, and pathology concordance. Important files include:

- per-sample probable CNA classifications and confidence/probability fields;
- HTML/PDF sample reports with evidence tiers and limitations;
- driver-CNA and literature tables;
- clinician-oriented summaries;
- `07_pathology/pathology_concordance.tsv` when pathology is supplied.

## Interpret pathology agreement carefully

Agreement means the observed CNA pattern is compatible with the supplied pathology label under the selected context. Disagreement can reflect low tumor fraction, limited depth, a biologically quiet genome, sample mismatch, an incomplete CNA catalog, or a diagnosis driven by alterations that LP-WGS cannot observe. It is a review flag, not an automated correction of pathology.

## Reproducibility and privacy

- Record the OncoTracer commit, container tag, selected sample-set context, and model settings.
- Set `pathology_biomed_local_files_only: true` for offline use only after required models are cached.
- Do not send identifiable clinical text to external services.
- Review literature links and evidence tiers manually.

!!! danger "Not diagnostic"
    Classifier probabilities, driver summaries, and pathology agreement are supportive research outputs. They do not replace morphology, IHC, cytogenetics, clinical-grade sequencing, or expert pathology review.
