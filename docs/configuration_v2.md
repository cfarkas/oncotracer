# Canonical native YAML configuration

OncoTracer v2 reads a flat top-level YAML mapping. [Automatic Setup](auto_params.md) is recommended because it validates the input layout and writes absolute paths. Manual files remain useful for unusual filenames, custom references, pathology, classifier settings, or controlled refinement comparisons.

Nested YAML is deliberately rejected by the standalone parser.

## Minimal Illumina

```yaml
mode: illumina
lpwgs_root: /data/study
outdir: /data/study/results/illumina
illumina_samplesheet: /data/study/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
```

The samplesheet has four columns:

```csv
sample,fastq_1,fastq_2,status
Tumor_A,/data/study/input/Tumor_A_R1.fastq.gz,/data/study/input/Tumor_A_R2.fastq.gz,tumor
Tumor_B,/data/study/input/Tumor_B_R1.fastq.gz,/data/study/input/Tumor_B_R2.fastq.gz,tumor
```

For single-end data, keep the header and leave `fastq_2` empty for every row. Do not mix single-end and paired-end libraries in one analysis.

## Minimal ONT

```yaml
mode: ont
lpwgs_root: /data/study
outdir: /data/study/results/ont
ont_folder: /data/study/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: SAMPLE_01,SAMPLE_02
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
run_cna_classifier: false
force: false
```

`ont_barcodes` and `ont_sample_names` are positional and must have the same number of entries.

The default above is the liquid-biopsy ichorCNA route. For a separate solid-biopsy qDNAseq analysis, use a new `outdir` and set `ont_analysis_type: solid_biopsy`, `ont_caller: qdnaseq`, and an explicit qDNAseq bin width. The engine rejects qDNAseq for other ONT analysis types.

An optional ONT-only POD5 methylation branch is available with `--methylation` plus exactly one of `--sturgeon` or `--marlin`, and always requires `--pod5-dir /absolute/path`. It runs before CNA, but the two outcomes are preserved independently. See [Optional ONT methylation](configuration/methylation.md) for the required checksum-pinned external resources and license/backend limitations.

## Run a YAML

```bash
oncotracer run \
  --backend conda \
  --threads 16 \
  --config "$PWD/project/config/illumina.auto.yml"
```

Repeat the same command to reuse valid completed stages. Add `--dry-run` to inspect the native argument arrays, or `--force` only for a deliberate refresh.

## Normal samples

In an Illumina samplesheet, `status: normal` is preserved metadata for an
independently analyzed qDNAseq sample. Automatic Setup does not pool normal
rows, create a sample-derived reference, or exclude them from per-sample CNA
outputs. Review `qdnaseq_sample_status.json` for exact completion status.

## Optional native CNA classifier

The complete classifier/report branch runs through the native executable; it is not delegated to Nextflow.

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer

# Optional matched pathology table.
pathology_csv: /data/study/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true

# GISTIC2 is optional and non-fatal unless explicitly required.
run_gistic: true
gistic_required: false
gistic_min_samples: 2

# Deterministic offline defaults.
knowledge_web: false
knowledge_literature_llm: false
knowledge_deep_literature: false
```

Supported contexts include `broad_cancer`, `lymphoma`, `brain_cns`, `breast`, `pancreas`, `colorectal`, `leukemia`, `lung`, `prostate`, `ovarian`, `gastric_esophageal`, `sarcoma`, `renal`, `urothelial`, `thyroid`, `melanoma`, `liver`, `head_neck`, `germ_cell`, `myeloma`, `neuroblastoma`, `neuroendocrine`, and `pediatric_solid`.

Classifier outputs are written under `05_cna_classifier/`, including prepared matrices, classifications, optional recurrence results, knowledge/pathology tables, HTML/PDF reports, clinician summaries, and `native_classifier_summary.json`.

## Boundary-refinement overrides

Keep defaults for an initial analysis. A controlled comparison may add:

```yaml
fine_bin_kb_illumina: 20
search_radius_bins: 2
min_mapq: 30
min_local_log2_diff_illumina: 0.15
min_bic_gain: 8
permutations: 500
permutation_p: 0.05
accept_rule: p_and_bic
```

Use a new `outdir` and report every non-default setting. See [Advanced refinement](configuration/refinement.md).

## YAML rules

- Use one top-level `key: value` per line.
- Use `true` and `false` for booleans.
- Use comma-separated text for positional sample/barcode lists.
- Use absolute paths for Docker, Singularity/Apptainer, and shared systems.
- Do not use tabs.
- Do not embed shell variables such as `$PWD`; expand them while writing the file.
- Use a dedicated `outdir` for each scientifically distinct configuration.

## Detailed references

- [Input files and folder layouts](inputs.md)
- [Manual YAML editing](configuration/yaml_basics.md)
- [Illumina setup](configuration/illumina.md)
- [ONT setup](configuration/ont.md)
- [Pathology and classifier](configuration/pathology.md)
- [All native CLI and YAML fields](configuration/parameter_reference.md)
