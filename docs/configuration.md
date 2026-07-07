# YAML Configuration

OncoTracer uses YAML files to keep run commands short and reproducible. The files live in `params/`.

Copy an example before editing:

```bash
cp params/illumina.example.yml params/project_illumina.yml
```

Run with:

```bash
nextflow run main.nf -profile local -params-file params/project_illumina.yml -resume
```

## Common Fields

These fields appear in most YAML files.

| Field | Required | Meaning |
|---|---:|---|
| `mode` | yes | `ont` or `illumina`. Selects the workflow branch. |
| `lpwgs_root` | usually | Project root used as fallback for bundled script lookup. For GitHub use, this can remain a local project root or be ignored when scripts are bundled in `bin/`. |
| `outdir` | yes | Top-level output directory for the run. All numbered result folders are written here. |
| `run_cna_classifier` | no | `true` runs CNA classification, literature enrichment, reports, and optional pathology concordance. `false` stops after custom plots. |
| `force` | no | Passes overwrite behavior to refinement/codification steps. Use with care when reusing an output folder. |

## ONT From FASTQ/Barcodes YAML

Example file: `params/ont.example.yml`

```yaml
mode: ont
lpwgs_root: /path/to/project
outdir: /path/to/results/OncoTracer_ONT_test

ont_folder: /path/to/ont/run/or/fastq_pass
ont_barcodes: barcode07,barcode08
ont_sample_names: sample1,sample2
ont_samurai_outdir: /path/to/results/OncoTracer_ONT_test/01_samurai_ont
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0

run_cna_classifier: false
force: true
```

Field-by-field:

| Field | Meaning |
|---|---|
| `mode: ont` | Selects the ONT branch. |
| `outdir` | Main OncoTracer result folder. |
| `ont_folder` | Folder with ONT FASTQ/run data. |
| `ont_barcodes` | Comma-separated barcode folders/samples to process. Order must match `ont_sample_names`. |
| `ont_sample_names` | Comma-separated sample labels assigned to each barcode. |
| `ont_samurai_outdir` | Where ONT SAMURAI results are written. Use `<outdir>/01_samurai_ont` for the standard structure. |
| `ont_analysis_type` | Analysis label passed to SAMURAI, commonly `liquid_biopsy` or `solid_biopsy`. |
| `ont_caller` | CNA caller for SAMURAI; typically `ichorcna`. |
| `ont_binsize_kb` | Coarse CNA bin size in kb. Current ONT runs use `500`. |
| `ont_min_age_minutes` | Optional wait time for files in active sequencing/run folders. Use `0` for existing completed data. |
| `run_cna_classifier` | Set `true` to run classifier/reports after plots. |
| `force` | Allows overwrite/recalculation behavior in underlying scripts. |

Optional ONT fields from `nextflow.config`:

| Field | Meaning |
|---|---|
| `ont_ref` | Reference FASTA if required by the SAMURI wrapper. |
| `ont_normal_folder` | Folder containing normal/PoN FASTQ data. |
| `ont_normal_barcodes` | Normal barcode list. |
| `ont_normal_sample_names` | Normal sample labels. |
| `ont_build_pon` | Build/use panel of normals when supported. |
| `ont_force_realign` | Force realignment in the SAMURAI step. |

## ONT From Existing SAMURAI/ichorCNA YAML

Example file: `params/ont.from_existing_samurai.example.yml`

Use this when ONT SAMURAI/ichorCNA has already run and you want OncoTracer to start from BAM boundary refinement.

```yaml
mode: ont
outdir: /path/to/results/OncoTracer_ONT_existing_test

ont_ichor_dir: /path/to/ONT_run/results/ichorcna
ont_bam_dir: /path/to/ONT_run/bam
ont_prior_seg: /path/to/ONT_run/results/ichorcna/segments_logR_corrected_gistic.seg
ont_binsize_kb: 500

run_cna_classifier: false
force: true
```

Field-by-field:

| Field | Meaning |
|---|---|
| `ont_ichor_dir` | Existing ONT ichorCNA result directory. |
| `ont_bam_dir` | Directory containing BAM files for the ONT samples. |
| `ont_prior_seg` | Prior segmentation file used by refinement. Usually `segments_logR_corrected_gistic.seg`. |
| `ont_binsize_kb` | Coarse bin size used in the prior CNA analysis. |

When `ont_ichor_dir` is provided, the top-level workflow skips `01_samurai_ont` and begins at `02_bam_refinement`.

## Illumina YAML

Example file: `params/illumina.example.yml`

```yaml
mode: illumina
lpwgs_root: /path/to/project
outdir: /path/to/results/OncoTracer_illumina_test

illumina_qdnaseq_dir: /path/to/samurai_results_100kb/qdnaseq
illumina_bam_dir: /path/to/samurai_results_100kb/alignment
illumina_prior_seg: /path/to/samurai_results_100kb/qdnaseq/all_segments.seg
illumina_binsize_kb: 100

run_cna_classifier: false
pathology_csv: null
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: true
pathology_biomed_local_files_only: false
force: true
```

Field-by-field:

| Field | Meaning |
|---|---|
| `mode: illumina` | Selects the Illumina branch. |
| `illumina_qdnaseq_dir` | qDNAseq/SAMURAI CNA output directory. |
| `illumina_bam_dir` | Folder with Illumina aligned BAM files. |
| `illumina_prior_seg` | Prior segment table, usually `all_segments.seg`. |
| `illumina_binsize_kb` | Coarse bin size. Current Illumina runs use `100`. |
| `pathology_csv` | Optional pathology table. Use `null` to disable pathology matching. |
| `pathology_sample_col` | Column in the pathology table containing sample IDs matching CNA sample names. |
| `pathology_case_col` | Column containing pathology case/accession ID. |
| `pathology_diagnosis_col` | Column containing final diagnosis text. |
| `pathology_use_biomed_models` | Attempts optional biomedical transformer agreement scoring when pathology is provided. |
| `pathology_biomed_local_files_only` | If `true`, only locally cached Hugging Face models are used. |

## Refinement Parameters

These are in `nextflow.config` and can be overridden on the command line or in YAML.

| Field | Default | Meaning |
|---|---:|---|
| `fine_bin_kb_ont` | 25 | Fine bin size used during ONT boundary refinement. |
| `fine_bin_kb_illumina` | 10 | Fine bin size used during Illumina boundary refinement. |
| `search_radius_bins` | 2 | Number of coarse bins around each boundary to search. |
| `min_mapq` | 20 | Minimum read mapping quality. |
| `min_local_log2_diff_ont` | 0.12 | Minimum local log2 difference for ONT acceptance. |
| `min_local_log2_diff_illumina` | 0.10 | Minimum local log2 difference for Illumina acceptance. |
| `min_adjacent_seg_delta` | 0.10 | Minimum adjacent segment delta. |
| `min_bic_gain` | 6 | Minimum BIC gain required by the acceptance rule. |
| `permutations` | 300 | Permutations for statistical boundary support. |
| `permutation_p` | 0.05 | Permutation p-value threshold. |
| `accept_rule` | `p_and_bic` | Rule for accepting refined boundaries. |

## Command-Line Overrides

Any YAML value can be overridden at run time:

```bash
nextflow run main.nf -profile local \
  -params-file params/illumina.example.yml \
  --run_cna_classifier true \
  --illumina_binsize_kb 100 \
  --force true \
  -resume
```
