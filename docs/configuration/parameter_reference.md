# Parameter Reference

This page documents every top-level parameter declared in `nextflow.config`. Beginners normally need only the automatic-setup command or one minimal YAML; keep the remaining defaults until the standard run works.

## How parameters are supplied

A YAML file contains one `name: value` setting per line:

```yaml
mode: illumina
outdir: /home/student/oncotracer/project/runs/sample_a
force: false
```

Pass it with:

```bash
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume
```

A command-line pipeline parameter begins with two hyphens, for example `--mode illumina`. A command-line value overrides the same value in the YAML. Nextflow options such as `-resume` and `-stub-run` use one hyphen and are not YAML parameters.

## Choose a route

| Goal | Required route settings |
| --- | --- |
| Generate configuration from Illumina reads | `--auto_params --mode illumina --reads_folder PATH --sample_table FILE` |
| Generate configuration from ONT reads | `--auto_params --mode ont --reads_folder PATH --sample_table FILE` |
| Prepare public tests | `--make_test`; optionally `--test_root PATH` |
| Run a real Illumina analysis | `mode`, `lpwgs_root`, `outdir`, `illumina_samplesheet` |
| Run a real ONT analysis | `mode`, `lpwgs_root`, `outdir`, `ont_folder`, `ont_barcodes` |

`--make_test` and `--auto_params` are preparation routes: each writes files and stops. They do not perform the real CNA workflow.

## Preparation parameters

| Parameter | Type / accepted values | Effective default | Meaning |
| --- | --- | --- | --- |
| `make_test` | Boolean | `false` | Download or reuse public FASTQs and write quick-start YAML files. Use as `--make_test`. |
| `test_root` | absolute directory or `null` | `<repository>/test` | Alternate destination for public inputs, configurations, and results. |
| `auto_params` | Boolean | `false` | Generate a run YAML from a reads folder and sample table. Use as `--auto_params`. |
| `reads_folder` | absolute directory | `null` | Flat paired-FASTQ folder for Illumina, or the ONT `fastq_pass` folder containing barcode directories. Required with `auto_params`. |
| `sample_table` | absolute CSV, TSV, or whitespace-delimited TXT path | `null` | Illumina `sample_name,status` table or ONT `barcode,sample_name,status` table. Required with `auto_params`. |
| `auto_config_dir` | absolute directory or `null` | `<reads_folder>/oncotracer_config` | Destination for generated YAML and, for Illumina, the generated samplesheet. |
| `auto_outdir` | absolute directory or `null` | `<reads_folder>/oncotracer_results` | Result path written into the generated YAML. |

## Common analysis parameters

| Parameter | Type / accepted values | Default | Meaning |
| --- | --- | --- | --- |
| `mode` | `illumina` or `ont` | `null` | Selects the sequencing route. Required for automatic setup and real analysis. |
| `lpwgs_root` | absolute directory | `/media/server/STORAGE/LPWGS_2025` | Common parent mounted into Docker/Singularity. This repository default is site-specific: set it explicitly and keep all configured inputs, references, and outputs below it. |
| `outdir` | absolute directory | `null` | Main result directory. Required for real analysis. |
| `force` | Boolean | `false` | Requests supported overwrite/recompute behavior in wrappers. Keep `false` for real projects; use a new `outdir` for a new experiment. |

## Runtime parameters

Use exactly one of `--docker`, `--singularity`, or `--conda` for a real run.

| Parameter | Type / accepted values | Default | Meaning |
| --- | --- | --- | --- |
| `docker` | Boolean | `false` | Enables Docker when passed as `--docker`. Recommended for a workstation. |
| `singularity` | Boolean | `false` | Enables Singularity/Apptainer when passed as `--singularity`. Common on HPC. |
| `conda` | Boolean | `false` | Enables the Conda fallback when passed as `--conda`. |
| `docker_image` | container image name | `carlosfarkas/oncotracer:latest` | Image used for Docker processes. |
| `singularity_image` | container URI | `docker://carlosfarkas/oncotracer:latest` | Image pulled by Singularity/Apptainer. |
| `docker_user` | text in `UID:GID` form | `1000:1000` | User and group used inside Docker to avoid root-owned results. |
| `docker_container_options` | Docker option string | `--entrypoint ""` | Clears the image entry point for Nextflow-launched tasks. Advanced runtime setting. |

## Illumina parameters

| Parameter | Type / accepted values | Default | Meaning |
| --- | --- | --- | --- |
| `illumina_samplesheet` | absolute CSV path | `null` | Required. Columns are `sample,fastq_1,fastq_2,status`; FASTQ paths must be absolute. |
| `illumina_analysis_type` | text; standard route `solid_biopsy` | `solid_biopsy` | Analysis preset passed to SAMURAI. |
| `illumina_caller` | text; current route `qdnaseq` | `qdnaseq` | CNA caller. Downstream Illumina paths expect qDNAseq output. |
| `illumina_binsize_kb` | positive integer, kilobases | `100` | Initial qDNAseq copy-number bin width. |

The upstream directory is always derived as `outdir/01_samurai_illumina`; there is no user-facing SAMURAI output parameter.

## ONT parameters

Comma-separated barcode and sample-name lists are positional and must have matching lengths.

| Parameter | Type / accepted values | Default | Meaning |
| --- | --- | --- | --- |
| `ont_folder` | absolute directory | `null` | Required. Parent containing tumor barcode FASTQ directories. |
| `ont_barcodes` | comma-separated directory names | `null` | Required. Tumor barcode selection. |
| `ont_sample_names` | comma-separated sample names or `null` | `null` | Output names corresponding one-to-one with `ont_barcodes`; strongly recommended. |
| `ont_analysis_type` | `liquid_biopsy` or `solid_biopsy` | `liquid_biopsy` | SAMURAI analysis preset. |
| `ont_caller` | text; current downstream route `ichorcna` | `ichorcna` | CNA caller. Current OncoTracer ONT refinement expects ichorCNA outputs. |
| `ont_binsize_kb` | positive integer, kilobases | `500` | Initial ichorCNA copy-number bin width. |
| `ont_ref` | absolute FASTA path or `null` | `null` | Optional custom reference. Keep it below `lpwgs_root`. |
| `ont_normal_folder` | absolute directory or `null` | `null` | Optional parent containing normal/control barcode directories. |
| `ont_normal_barcodes` | comma-separated directory names or `null` | `null` | Normal barcode selection. Supply with `ont_normal_folder`. |
| `ont_normal_sample_names` | comma-separated names or `null` | `null` | Names corresponding one-to-one with normal barcodes. |
| `ont_build_pon` | Boolean | `false` | Explicitly requests the supported local panel-of-normals route. Supplying normal inputs also activates the wrapper's default local PoN behavior. |
| `ont_min_age_minutes` | non-negative integer, minutes | `0` | Minimum FASTQ age. Use `0` for completed data; use a positive value to avoid files still being written. |
| `ont_force_realign` | Boolean | `false` | Recreate supported ONT alignments instead of reusing existing ones. |

The upstream directory is always derived as `outdir/01_samurai_ont`; there is no user-facing SAMURAI output parameter.

## Classifier and pathology parameters

These settings matter only when `run_cna_classifier: true`. A pathology CSV is optional even when the classifier is enabled.

| Parameter | Type / accepted values | Default | Meaning |
| --- | --- | --- | --- |
| `run_cna_classifier` | Boolean | `false` | Adds stage `05_cna_classifier`. |
| `cna_classifier_sample_set` | context name | `broad_cancer` | Classification context, for example `broad_cancer`, `lymphoma`, `breast`, `pancreas`, `colorectal`, `leukemia`, `brain_cns`, `lung`, `prostate`, `ovarian`, `gastric`, or `sarcoma`. |
| `cna_classifier_profile` | `conda`, `docker`, `singularity`, or `local_gistic` | `conda` | Nextflow profile used by the nested classifier workflow. Advanced setting. |
| `pathology_csv` | absolute CSV path or `null` | `null` | Optional pathology metadata; keep it below `lpwgs_root`. |
| `pathology_sample_col` | CSV column name | `illumina_sample_id` | Column whose values exactly match OncoTracer sample IDs. |
| `pathology_case_col` | CSV column name | `case_code` | Case/accession identifier column. |
| `pathology_diagnosis_col` | CSV column name | `final_diagnosis` | Diagnosis-text column. |
| `pathology_use_biomed_models` | Boolean | `true` | Attempts supported biomedical-model pathology scoring when pathology data are supplied. Failures are reported without disabling the CNA-only workflow. |
| `pathology_biomed_local_files_only` | Boolean | `false` | When `true`, use only already-cached biomedical model files. |

See [Pathology and Classifier](pathology.md) before enabling these settings.

## Boundary-refinement parameters

Stage `02_bam_refinement` runs by default. These parameters tune it; they do not enable it.

| Parameter | Type / accepted values | Default | Meaning |
| --- | --- | --- | --- |
| `refine_skip_install` | Boolean | `false` | Prefer an existing refinement environment. A missing environment is still created and required packages may be repaired. |
| `fine_bin_kb_illumina` | positive integer, kb | `10` | Illumina local read-depth bin width. |
| `fine_bin_kb_ont` | positive integer, kb | `25` | ONT local read-depth bin width. |
| `search_radius_bins` | non-negative integer, coarse bins per side | `2` | Search distance around each original boundary. |
| `min_mapq` | non-negative integer, MAPQ | `20` | Minimum read mapping quality. |
| `min_local_log2_diff_illumina` | non-negative number, log2 ratio | `0.10` | Minimum local Illumina depth step. |
| `min_local_log2_diff_ont` | non-negative number, log2 ratio | `0.12` | Minimum local ONT depth step. |
| `min_adjacent_seg_delta` | non-negative number, log2 ratio | `0.10` | Minimum difference between adjacent coarse segments. |
| `min_bic_gain` | number, BIC units | `6` | Minimum local model-fit improvement. |
| `permutations` | non-negative integer | `300` | Empirical permutations; `0` disables this calculation. |
| `permutation_p` | number from `0` to `1` | `0.05` | Empirical p-value threshold used by `p_and_bic`. |
| `accept_rule` | `p_and_bic`, `bic_only`, or `permissive` | `p_and_bic` | Rule for accepting a boundary shift. |
| `max_ci_fraction_of_coarse` | non-negative number, fraction | `1.0` | Maximum confidence-interval width relative to the coarse bin. |
| `zipcnv_mode` | `off`, `adapted`, `official`, or `both` | `adapted` | ZIPcnv comparison mode. |
| `zipcnv_window_bins` | positive integer, fine bins | `5` | Adapted ZIPcnv local window. |
| `zipcnv_k` | non-negative number | `0.05` | Adapted ZIPcnv tuning constant. |
| `zipcnv_min_segment_bins` | positive integer, bins | `3` | Minimum retained ZIPcnv segment length. |
| `zipcnv_min_abs_log2` | non-negative number, log2 ratio | `0.25` | Minimum retained absolute ZIPcnv signal. |
| `zipcnv_compare_min_overlap` | number from `0` to `1` | `0.50` | Minimum overlap for refined/ZIPcnv comparison. |

See [Boundary Refinement](refinement.md) for a complete controlled experiment. Keep the defaults for an initial run.
