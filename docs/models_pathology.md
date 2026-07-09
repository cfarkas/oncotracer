# Models & Pathology

OncoTracer can optionally run CNA classifier/report steps and pathology concordance summaries after the core CNA workflow.

Enable this in an Illumina pathology YAML:

```yaml
run_cna_classifier: true
pathology_csv: /path/to/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
```

## Pathology CSV

The pathology CSV should contain one row per sample or case, with columns for:

- sample identifier matching the OncoTracer sample name;
- case identifier;
- diagnosis text.

## Research Use

Classifier and pathology concordance outputs organize evidence for review. They do not replace expert interpretation or diagnostic validation.
