# Native YAML configuration

OncoTracer v2 reads flat YAML. Automatic Setup is recommended; manual files are useful for unusual layouts.

## Minimal Illumina

```yaml
mode: illumina
lpwgs_root: /data/study
outdir: /data/study/results
illumina_samplesheet: /data/study/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
```

## Minimal ONT

```yaml
mode: ont
lpwgs_root: /data/study
outdir: /data/study/results
ont_folder: /data/study/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: SAMPLE_01,SAMPLE_02
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
run_cna_classifier: false
force: false
```

## Optional native CNA classifier

The v1.1 classifier/report branch is available through the native executable; it is not delegated to Nextflow.

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer

# Optional matched pathology table.
pathology_csv: /data/study/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false

# GISTIC2 is optional and non-fatal unless explicitly required.
run_gistic: true
gistic_required: false
gistic_min_samples: 2

# Disable web/model enrichment for deterministic offline analyses.
knowledge_web: false
knowledge_literature_llm: false
knowledge_deep_literature: false
```

Supported cancer contexts include `broad_cancer`, `lymphoma`, `brain_cns`, `breast`, `pancreas`, `colorectal`, `leukemia`, `lung`, `prostate`, `ovarian`, `gastric_esophageal`, `sarcoma`, `renal`, `urothelial`, `thyroid`, `melanoma`, `liver`, `head_neck`, `germ_cell`, `myeloma`, `neuroblastoma`, `neuroendocrine`, and `pediatric_solid`. Compact sample filtering is accepted as `breast:S1,S2` or through `cna_classifier_samples`.

Classifier outputs are written under `05_cna_classifier/`, including prepared matrices, classifications, optional GISTIC results, knowledge and pathology tables, HTML/PDF reports, clinician summaries, and `native_classifier_summary.json`.

Nested YAML is deliberately rejected by the standalone parser. Paths should be absolute for containers and HPC systems.
