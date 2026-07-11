# Pathology and Classifier

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer
pathology_csv: /home/student/oncotracer_project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: true
pathology_biomed_local_files_only: false
```

| Field | Meaning |
| --- | --- |
| `run_cna_classifier` | Enables optional classifier/report/pathology outputs. |
| `cna_classifier_sample_set` | Classifier sample set; default `broad_cancer`. |
| `cna_classifier_profile` | Runtime profile for the nested classifier workflow; default `conda`. |
| `pathology_csv` | CSV file with pathology labels; use `null` for no pathology table. |
| `pathology_sample_col` | Column containing sample IDs matching OncoTracer names. |
| `pathology_case_col` | Column containing case IDs. |
| `pathology_diagnosis_col` | Column containing diagnosis text. |
| `pathology_use_biomed_models` | Enables biomedical-model concordance behavior. |
| `pathology_biomed_local_files_only` | Uses only locally cached model files. |

```csv
illumina_sample_id,case_code,final_diagnosis
Sample_A,Case_001,Example diagnosis text
```

OncoTracer remains a research workflow and not a standalone diagnostic system.
