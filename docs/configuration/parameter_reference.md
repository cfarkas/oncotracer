# Parameter Reference

This page documents the top-level parameters declared in `nextflow.config`. For a first run, use Automatic Setup or a minimal YAML and keep the remaining defaults.

## Clone OncoTracer

```bash
# Clone OncoTracer into a given directory.

git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
```

## How parameters are supplied

```yaml
mode: illumina
lpwgs_root: project
outdir: project/results/sample_a
force: false
```

```bash
# Run this step from the cloned oncotracer directory.
nextflow run main.nf --docker \
  -params-file "params/my_illumina.yml" \
  -resume
```

Pipeline parameters use two hyphens, for example `--mode illumina`. Nextflow options such as `-resume` and `-stub-run` use one hyphen. A command-line value overrides the same YAML value.

## Choose a route

| Goal | Route settings |
| --- | --- |
| Prepare one runtime | `--install` plus one of `--docker`, `--singularity`, or `--conda` |
| Generate Illumina configuration | `--auto_params --mode illumina --reads_folder PATH --sample_table FILE` |
| Generate ONT configuration | `--auto_params --mode ont --reads_folder PATH --sample_table FILE` |
| Prepare the small public tests | `--make_test`; optional `--test_root PATH` |
| Download the PRJNA754199 archive | `--make_prjna754199`; optional `--test_root PATH` |
| Run Illumina | `mode`, `lpwgs_root`, `outdir`, `illumina_samplesheet` |
| Run ONT | `mode`, `lpwgs_root`, `outdir`, `ont_folder`, `ont_barcodes` |

Preparation routes create files and stop before the CNA analysis.

## Preparation parameters

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `install` | Boolean | `false` | Prepare and test one selected runtime, cache SAMURAI v1.4.0, write a manifest, and stop. |
| `install_dir` | absolute directory or `null` | `<repository>/.oncotracer/install` | Optional alternate manifest destination. |
| `make_test` | Boolean | `false` | Download/reuse small public FASTQs and write QuickStart YAMLs. |
| `make_prjna754199` | Boolean | `false` | Download and validate the 12 PRJNA754199 FASTQs. |
| `test_root` | absolute directory or `null` | `<repository>/test` | Public input, configuration, work, and result root. |
| `auto_params` | Boolean | `false` | Generate a YAML from a reads folder and sample table. |
| `reads_folder` | absolute directory | `null` | Illumina FASTQ folder or ONT `fastq_pass` parent. |
| `sample_table` | CSV/TSV/TXT path | `null` | Illumina `sample_name,status` or ONT `barcode,sample_name,status` table. |
| `auto_config_dir` | absolute directory or `null` | `<reads_folder>/oncotracer_config` | Generated YAML, manifest, and Illumina samplesheet destination. |
| `auto_outdir` | absolute directory or `null` | `<reads_folder>/oncotracer_results` | Result path written into the generated YAML. |

## Common analysis parameters

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `mode` | `illumina` or `ont` | `null` | Selects the sequencing route. |
| `lpwgs_root` | absolute directory | site-specific repository default | Common parent visible to the selected runtime. Set it explicitly. |
| `outdir` | absolute directory | `null` | Main result directory. |
| `force` | Boolean | `false` | Requests supported refresh behavior. Keep false for real projects. |

## Runtime parameters

Use exactly one runtime option for installation or analysis.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `docker` | Boolean | `false` | Enables Docker with `--docker`. |
| `singularity` | Boolean | `false` | Enables Singularity/Apptainer with `--singularity`. |
| `conda` | Boolean | `false` | Enables the Conda fallback with `--conda`. |
| `docker_image` | image name | `carlosfarkas/oncotracer:latest` | Docker image. |
| `singularity_image` | image URI | `docker://carlosfarkas/oncotracer:latest` | Singularity/Apptainer image. |
| `docker_user` | `UID:GID` | `1000:1000` | Container user/group for output ownership. |
| `docker_container_options` | option string | `--entrypoint ""` | Advanced Docker settings. |

## Illumina parameters

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `illumina_samplesheet` | absolute CSV path | `null` | Columns: `sample,fastq_1,fastq_2,status`. |
| `illumina_analysis_type` | text | `solid_biopsy` | SAMURAI analysis preset. |
| `illumina_caller` | text | `qdnaseq` | Illumina CNA caller. |
| `illumina_binsize_kb` | positive integer | `100` | Initial qDNAseq bin width in kb. |
| `illumina_build_pon` | Boolean | `false` | Build and apply a local qDNAseq normal reference. |
| `illumina_pon_normal_samples` | comma-separated IDs or `null` | `null` | Every and only samplesheet IDs marked normal. |
| `illumina_pon_min_normals` | integer ≥2 | `2` | Required number of selected normal controls. |
| `illumina_pon_name` | safe identifier | `illumina_local_PoN` | Name used in local-panel artifacts. |
| `illumina_pon_min_mapq` | non-negative integer | `37` | Mapping-quality threshold for panel construction. |
| `illumina_pon_r_container` | image URI | `docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1` | Pinned qDNAseq runtime. |

Automatic Setup writes `illumina_build_pon: false` with no normals, rejects exactly one, and enables the local panel with two or more. Corrected panel outputs contain tumor samples only.

## ONT parameters

Comma-separated barcode and sample-name lists are positional and must have matching lengths.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `ont_folder` | absolute directory | `null` | Parent containing tumor barcode directories. |
| `ont_barcodes` | comma-separated names | `null` | Tumor barcode selection. |
| `ont_sample_names` | comma-separated names or `null` | `null` | Output names matching `ont_barcodes`. |
| `ont_analysis_type` | `liquid_biopsy` or `solid_biopsy` | `liquid_biopsy` | SAMURAI preset. |
| `ont_caller` | text | `ichorcna` | ONT CNA caller. |
| `ont_binsize_kb` | positive integer | `500` | Initial ichorCNA bin width in kb. |
| `ont_ref` | absolute FASTA or `null` | `null` | Optional custom reference below `lpwgs_root`. |
| `ont_normal_folder` | absolute directory or `null` | `null` | Optional normal barcode parent. |
| `ont_normal_barcodes` | comma-separated names or `null` | `null` | Normal barcode selection. |
| `ont_normal_sample_names` | comma-separated names or `null` | `null` | Names matching normal barcodes. |
| `ont_build_pon` | Boolean | `false` | Explicitly requests the supported local normal route. |
| `ont_min_age_minutes` | non-negative integer | `0` | Minimum FASTQ age before use. |
| `ont_force_realign` | Boolean | `false` | Recreate supported ONT alignments. |

## Classifier and pathology parameters

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `run_cna_classifier` | Boolean | `false` | Adds stage `05_cna_classifier`. |
| `cna_classifier_sample_set` | context name | `broad_cancer` | Classification context selected from study design. |
| `cna_classifier_profile` | runtime profile | `conda` | Nested classifier runtime. |
| `pathology_csv` | absolute CSV or `null` | `null` | Optional matched pathology table. |
| `pathology_sample_col` | column name | `illumina_sample_id` | Column matching OncoTracer sample IDs. |
| `pathology_case_col` | column name | `case_code` | Case/accession column. |
| `pathology_diagnosis_col` | column name | `final_diagnosis` | Diagnosis-text column. |
| `pathology_use_biomed_models` | Boolean | `true` | Attempts optional biomedical model assistance. |
| `pathology_biomed_local_files_only` | Boolean | `false` | Restricts models to an existing local cache. |

## Boundary-refinement parameters

Stage `02_bam_refinement` runs by default. These settings tune it.

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `refine_skip_install` | `false` | Prefer an existing refinement environment. |
| `fine_bin_kb_illumina` | `10` | Illumina local depth-bin width in kb. |
| `fine_bin_kb_ont` | `25` | ONT local depth-bin width in kb. |
| `search_radius_bins` | `2` | Search distance around each original boundary. |
| `min_mapq` | `20` | Minimum read mapping quality. |
| `min_local_log2_diff_illumina` | `0.10` | Minimum local Illumina depth step. |
| `min_local_log2_diff_ont` | `0.12` | Minimum local ONT depth step. |
| `min_adjacent_seg_delta` | `0.10` | Minimum adjacent coarse-segment difference. |
| `min_bic_gain` | `6` | Minimum local model-fit improvement. |
| `permutations` | `300` | Empirical permutations; `0` disables them. |
| `permutation_p` | `0.05` | Empirical p-value threshold. |
| `accept_rule` | `p_and_bic` | Boundary-shift acceptance rule. |
| `max_ci_fraction_of_coarse` | `1.0` | Maximum confidence-interval width relative to a coarse bin. |
| `zipcnv_mode` | `adapted` | ZIPcnv comparison mode. |
| `zipcnv_window_bins` | `5` | Adapted ZIPcnv local window. |
| `zipcnv_k` | `0.05` | Adapted ZIPcnv tuning constant. |
| `zipcnv_min_segment_bins` | `3` | Minimum retained ZIPcnv segment length. |
| `zipcnv_min_abs_log2` | `0.25` | Minimum retained absolute signal. |
| `zipcnv_compare_min_overlap` | `0.50` | Minimum overlap for comparison. |

See [Boundary Refinement](refinement.md) for a controlled comparison. Keep defaults for an initial analysis.
