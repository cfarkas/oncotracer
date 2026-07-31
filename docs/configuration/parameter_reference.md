# Parameter Reference

Most users need Automatic Setup and one generated YAML. Use this page to look up a field or default.

## Supply a YAML

```yaml
mode: illumina
outdir: /home/student/oncotracer/project/results/sample_a
force: false
```

```bash
# Run or resume a YAML with Docker.
nextflow run main.nf --docker \
  -params-file params/my_illumina.yml \
  -resume
```

A pipeline parameter begins with two hyphens, such as `--mode illumina`. Nextflow options such as `-resume` and `-stub-run` use one hyphen.

## Preparation routes

| Goal | Route |
| --- | --- |
| Prepare one runtime | `--install` plus one of `--docker`, `--singularity`, or `--conda` |
| Generate Illumina configuration | `--auto_params --mode illumina --reads_folder PATH --sample_table FILE` |
| Generate ONT configuration | `--auto_params --mode ont --reads_folder PATH --sample_table FILE` |
| Prepare the small public tests | `--make_test`; optional `--test_root PATH` |
| Download the 12-run PRJNA754199 archive | `--make_prjna754199`; optional `--test_root PATH` |

Preparation routes write their documented files and stop before the main CNA workflow.

## Preparation parameters

| Parameter | Default | Purpose |
| --- | --- | --- |
| `install` | `false` | Prepare and test the selected runtime, cache SAMURAI, and write an installation manifest |
| `install_dir` | `<repository>/.oncotracer/install` | Alternate manifest directory |
| `make_test` | `false` | Download/reuse public QuickStart reads and write two YAML files |
| `make_prjna754199` | `false` | Download and validate the 12 public PRJNA754199 FASTQs |
| `test_root` | `<repository>/test` | Root for public inputs, configurations, work, and results |
| `auto_params` | `false` | Generate a YAML from a reads folder and sample table |
| `reads_folder` | `null` | Illumina FASTQ folder or ONT `fastq_pass` folder |
| `sample_table` | `null` | Illumina `sample_name,status` or ONT `barcode,sample_name,status` table |
| `auto_config_dir` | `<reads_folder>/oncotracer_config` | Generated YAML, manifest, and Illumina samplesheet directory |
| `auto_outdir` | `<reads_folder>/oncotracer_results` | Result path written into the generated YAML |

## Common analysis parameters

| Parameter | Default | Purpose |
| --- | --- | --- |
| `mode` | `null` | `illumina` or `ont` |
| `lpwgs_root` | site-specific | Common parent containing configured inputs, caches, and outputs |
| `outdir` | `null` | Final result directory for one run |
| `force` | `false` | Supported refresh behavior; keep `false` for normal project runs |

## Runtime parameters

Use one runtime option per installation or analysis command.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `docker` | `false` | Enable Docker with `--docker` |
| `singularity` | `false` | Enable Singularity/Apptainer with `--singularity` |
| `conda` | `false` | Enable the Conda fallback with `--conda` |
| `docker_image` | `carlosfarkas/oncotracer:latest` | Docker image |
| `singularity_image` | `docker://carlosfarkas/oncotracer:latest` | Singularity/Apptainer image URI |
| `docker_user` | `1000:1000` | Numeric user and group inside Docker |
| `docker_container_options` | `--entrypoint ""` | Advanced Docker options used by Nextflow |

## Illumina parameters

| Parameter | Default | Purpose |
| --- | --- | --- |
| `illumina_samplesheet` | `null` | Absolute CSV path with `sample,fastq_1,fastq_2,status` |
| `illumina_analysis_type` | `solid_biopsy` | SAMURAI preset |
| `illumina_caller` | `qdnaseq` | Illumina CNA caller |
| `illumina_binsize_kb` | `100` | Initial qDNAseq bin width in kb |
| `illumina_build_pon` | `false` | Build a run-local qDNAseq normal reference |
| `illumina_pon_normal_samples` | `null` | Exact comma-separated normal sample IDs |
| `illumina_pon_min_normals` | `2` | Minimum selected normal controls |
| `illumina_pon_name` | `illumina_local_PoN` | Name stored in normal-reference files |
| `illumina_pon_min_mapq` | `37` | Minimum mapping quality for normal-reference analysis |
| `illumina_pon_r_container` | `docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1` | qDNAseq R image |

Automatic Setup writes `illumina_build_pon: false` with no normal rows, rejects one normal, and enables the local reference with two or more normals. Corrected output contains tumor samples only.

## ONT parameters

Comma-separated barcode and sample-name lists are positional and must have equal lengths.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `ont_folder` | `null` | Parent containing tumor barcode directories |
| `ont_barcodes` | `null` | Selected tumor barcode directories |
| `ont_sample_names` | `null` | Tumor sample names in matching order |
| `ont_analysis_type` | `liquid_biopsy` | SAMURAI preset |
| `ont_caller` | `ichorcna` | ONT CNA caller |
| `ont_binsize_kb` | `500` | Initial ichorCNA bin width in kb |
| `ont_ref` | `null` | Optional reference FASTA below `lpwgs_root` |
| `ont_normal_folder` | `null` | Parent containing normal barcode directories |
| `ont_normal_barcodes` | `null` | Selected normal barcode directories |
| `ont_normal_sample_names` | `null` | Normal sample names in matching order |
| `ont_build_pon` | `false` | Request the supported ONT normal-control route |
| `ont_min_age_minutes` | `0` | Minimum input-file age; use `0` for completed data |
| `ont_force_realign` | `false` | Recreate supported ONT alignments |

## Classifier and pathology parameters

| Parameter | Default | Purpose |
| --- | --- | --- |
| `run_cna_classifier` | `false` | Enable stage `05_cna_classifier` |
| `cna_classifier_sample_set` | `broad_cancer` | Biological context chosen from study design |
| `cna_classifier_profile` | `conda` | Runtime for the nested classifier |
| `pathology_csv` | `null` | Optional matched pathology table |
| `pathology_sample_col` | `illumina_sample_id` | Column matching sequencing sample IDs |
| `pathology_case_col` | `case_code` | Case identifier column |
| `pathology_diagnosis_col` | `final_diagnosis` | Diagnosis text column |
| `pathology_use_biomed_models` | `true` | Enable optional biomedical model assistance |
| `pathology_biomed_local_files_only` | `false` | Restrict models to an existing local cache |

## Boundary-refinement parameters

Stage `02_bam_refinement` runs by default.

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `refine_skip_install` | `false` | Prefer an existing refinement environment |
| `fine_bin_kb_illumina` | `10` | Illumina local bin width in kb |
| `fine_bin_kb_ont` | `25` | ONT local bin width in kb |
| `search_radius_bins` | `2` | Coarse bins searched on each side |
| `min_mapq` | `20` | Minimum mapping quality |
| `min_local_log2_diff_illumina` | `0.10` | Minimum Illumina local depth step |
| `min_local_log2_diff_ont` | `0.12` | Minimum ONT local depth step |
| `min_adjacent_seg_delta` | `0.10` | Minimum adjacent coarse-segment difference |
| `min_bic_gain` | `6` | Minimum model-fit improvement |
| `permutations` | `300` | Empirical permutations; `0` disables them |
| `permutation_p` | `0.05` | Empirical p-value threshold |
| `accept_rule` | `p_and_bic` | Boundary-shift acceptance rule |
| `max_ci_fraction_of_coarse` | `1.0` | Maximum confidence-interval width relative to a coarse bin |
| `zipcnv_mode` | `adapted` | ZIPcnv comparison mode |
| `zipcnv_window_bins` | `5` | Adapted ZIPcnv window |
| `zipcnv_k` | `0.05` | Adapted ZIPcnv tuning constant |
| `zipcnv_min_segment_bins` | `3` | Minimum retained segment length |
| `zipcnv_min_abs_log2` | `0.25` | Minimum retained absolute signal |
| `zipcnv_compare_min_overlap` | `0.50` | Minimum overlap for comparison |

Keep defaults for the first successful run. See [Boundary Refinement](refinement.md) for a controlled comparison example.
