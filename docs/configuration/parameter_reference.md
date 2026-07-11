# Complete Parameter Reference

This page lists top-level OncoTracer parameters from `nextflow.config`.

## Required Common Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `mode` | `null` | Required. `illumina` or `ont`. |
| `outdir` | `null` | Required. Main output folder. |
| `lpwgs_root` | server default | Absolute folder path bound into Docker/Singularity. Replace with your project root. |

## Runtime Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `docker` | `false` | Set by `--docker`; enables Docker. |
| `singularity` | `false` | Set by `--singularity`; enables Singularity/Apptainer. |
| `conda` | `false` | Set by `--conda`; enables Conda. |
| `docker_image` | `carlosfarkas/oncotracer:latest` | Docker image. |
| `singularity_image` | `docker://carlosfarkas/oncotracer:latest` | Singularity image URI. |
| `docker_user` | `1000:1000` | Docker user/group. |
| `docker_container_options` | `--entrypoint ""` | Docker options for Nextflow-launched containers. |
| `force` | `false` | Allows supported overwrite/recompute behavior. |

## Illumina Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `illumina_samplesheet` | `null` | Required CSV with `sample,fastq_1,fastq_2,status`. |
| `illumina_samurai_outdir` | `null` | Required upstream SAMURAI/qDNAseq output folder. |
| `illumina_analysis_type` | `solid_biopsy` | Upstream Illumina analysis type. |
| `illumina_caller` | `qdnaseq` | Upstream Illumina CNA caller. |
| `illumina_binsize_kb` | `100` | Illumina coarse bin size in kb. |

## ONT Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `ont_folder` | `null` | Required ONT FASTQ/barcode folder. |
| `ont_barcodes` | `null` | Required comma-separated barcode IDs. |
| `ont_sample_names` | `null` | Optional sample names matching barcode order. |
| `ont_samurai_outdir` | `null` | Required upstream SAMURAI/ichorCNA output folder. |
| `ont_analysis_type` | `liquid_biopsy` | Upstream ONT analysis type. |
| `ont_caller` | `ichorcna` | Upstream ONT CNA caller. |
| `ont_binsize_kb` | `500` | ONT coarse bin size in kb. |
| `ont_ref` | `null` | Optional reference FASTA. |
| `ont_normal_folder` | `null` | Optional normal/control FASTQ folder. |
| `ont_normal_barcodes` | `null` | Optional normal/control barcode IDs. |
| `ont_normal_sample_names` | `null` | Optional normal/control sample names. |
| `ont_build_pon` | `false` | Build/use panel-of-normals route when supported. |
| `ont_min_age_minutes` | `0` | Wait time for active sequencing folders. |
| `ont_force_realign` | `false` | Force ONT realignment. |

## Classifier and Pathology Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `run_cna_classifier` | `false` | Runs optional classifier/report/pathology steps. |
| `cna_classifier_sample_set` | `broad_cancer` | Sample set passed to classifier workflow. |
| `cna_classifier_profile` | `conda` | Runtime profile for nested classifier workflow. |
| `pathology_csv` | `null` | Optional pathology CSV. |
| `pathology_sample_col` | `illumina_sample_id` | Pathology sample ID column. |
| `pathology_case_col` | `case_code` | Pathology case ID column. |
| `pathology_diagnosis_col` | `final_diagnosis` | Pathology diagnosis column. |
| `pathology_use_biomed_models` | `true` | Biomedical-model pathology concordance behavior. |
| `pathology_biomed_local_files_only` | `false` | Restrict biomedical models to local cached files. |

## Boundary Refinement Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `refine_skip_install` | `false` | Skip refinement helper installation checks when supported. |
| `fine_bin_kb_ont` | `25` | ONT fine-bin size. |
| `fine_bin_kb_illumina` | `10` | Illumina fine-bin size. |
| `search_radius_bins` | `2` | Search radius around coarse boundaries. |
| `min_mapq` | `20` | Minimum mapping quality. |
| `min_local_log2_diff_ont` | `0.12` | ONT local log2 threshold. |
| `min_local_log2_diff_illumina` | `0.10` | Illumina local log2 threshold. |
| `min_adjacent_seg_delta` | `0.10` | Minimum adjacent segment delta. |
| `min_bic_gain` | `6` | Minimum BIC gain. |
| `permutations` | `300` | Number of permutations. |
| `permutation_p` | `0.05` | Permutation p-value threshold. |
| `accept_rule` | `p_and_bic` | Acceptance rule. |
| `max_ci_fraction_of_coarse` | `1.0` | Maximum CI fraction of coarse segment. |
| `zipcnv_mode` | `adapted` | ZIP-CNV mode. |
| `zipcnv_window_bins` | `5` | ZIP-CNV window size. |
| `zipcnv_k` | `0.05` | ZIP-CNV k value. |
| `zipcnv_min_segment_bins` | `3` | Minimum ZIP-CNV segment bins. |
| `zipcnv_min_abs_log2` | `0.25` | Minimum absolute log2 for ZIP-CNV. |
| `zipcnv_compare_min_overlap` | `0.50` | Minimum overlap for ZIP-CNV comparison. |
