# Native CLI and YAML parameter reference

Use [Automatic Setup](../auto_params.md) for a first analysis. This page documents the installed v2 command interface and the flat YAML fields used by the native engine.

## Command structure

```text
oncotracer install ...
oncotracer doctor ...
oncotracer quickstart 1|2 ...
oncotracer auto ...
oncotracer run ...
oncotracer provenance --json
```

Run `oncotracer <subcommand> --help` to inspect the executable installed on the system.

## `oncotracer install`

Exactly one backend flag is required.

```bash
oncotracer install --conda
oncotracer install --docker
oncotracer install --singularity
./oncotracer install --poetry
```

| Option | Meaning |
| --- | --- |
| `--conda` | Create/update five isolated Conda prefixes |
| `--docker` | Validate Docker, pull the native image, and run its host doctor |
| `--singularity` | Pull/reuse a SIF through Apptainer or Singularity |
| `--poetry` | Install the source-development launcher and the five Conda prefixes |
| `--prefix PATH` | Alternate parent for the five Conda prefixes |
| `--image IMAGE` | Override the default native container image |
| `--sif PATH` | Override the Singularity/Apptainer image path |
| `--force` | Recreate damaged/changed managed assets deliberately |
| `--dry-run` | Print installation commands without executing them |
| `--root PATH` | Explicit source or extracted payload root |

## `oncotracer doctor`

```bash
oncotracer doctor --backend conda
oncotracer doctor --backend docker
oncotracer doctor --backend singularity
oncotracer doctor --backend poetry
```

The command returns JSON and exits nonzero when required source identity, prefixes, packages, or semantic executable probes fail.

## `oncotracer quickstart`

```bash
cd /path/to/my/analyses_dir/

oncotracer quickstart 1 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart1"

oncotracer quickstart 2 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart2"
```

| Option | Meaning |
| --- | --- |
| `1` | Public one-sample Illumina plus one-sample ONT example |
| `2` | Public three-library HCC1143 Illumina example |
| `--test-root PATH` | Required isolated input/config/result root |
| `--download-only` | Download/validate inputs and generate YAML without analysis |
| `--backend NAME` | `host`, `conda`, `docker`, `singularity`, or `poetry` |
| `--threads N` | Native stage thread limit where supported |
| `--force` | Deliberately invalidate reusable stages |
| `--dry-run` | Print planned operations without computation |
| `--image IMAGE` | Container override for Docker |
| `--sif PATH` | Image override for Singularity/Apptainer |

## `oncotracer auto`

```bash
oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/project/input/fastq" \
  --sample-table "$PWD/project/input/samples.csv" \
  --config-dir "$PWD/project/config" \
  --outdir "$PWD/project/results"
```

| Option | Required | Meaning |
| --- | --- | --- |
| `--mode illumina|ont` | yes | Select input discovery and YAML route |
| `--reads-folder PATH` | yes | Illumina FASTQ folder or ONT barcode parent |
| `--sample-table FILE` | yes | Illumina `sample_name,status` or ONT `barcode,sample_name,status` CSV |
| `--config-dir PATH` | no | Generated YAML/manifest/samplesheet destination |
| `--outdir PATH` | no | Result directory written into the YAML |
| `--run-cna-classifier` | no | Write `run_cna_classifier: true` |
| `--dry-run` | no | Print generator command without writing analysis files |
| `--root PATH` | no | Explicit payload/source root |

Automatic Setup creates files and stops before alignment.

## `oncotracer run`

```bash
oncotracer run \
  --backend conda \
  --threads 16 \
  --config "$PWD/project/config/illumina.auto.yml"
```

| Option | Meaning |
| --- | --- |
| `--config FILE` | Required flat native YAML |
| `--backend NAME` | `host`, `conda`, `docker`, `singularity`, or `poetry`; saved backend used when omitted |
| `--threads N` | Thread limit passed to supported native stages |
| `--force` | Deliberate stage refresh |
| `--dry-run` | Validate and print native commands without launching tools |
| `--image IMAGE` | Docker image override |
| `--sif PATH` | Singularity/Apptainer image override |
| `--root PATH` | Explicit payload/source root |

Repeating the same command reuses valid content-matched stages automatically.

## Common YAML fields

| Field | Type/default | Meaning |
| --- | --- | --- |
| `mode` | required `illumina` or `ont` | Sequencing route |
| `lpwgs_root` | required absolute directory | Project/reference/cache root visible to the backend |
| `outdir` | required absolute directory | Numbered native result tree |
| `force` | Boolean, `false` | Scientific refresh request; normally keep false |
| `run_cna_classifier` | Boolean, `false` | Add stage `05_cna_classifier` |

## Illumina YAML fields

| Field | Typical/default | Meaning |
| --- | --- | --- |
| `illumina_samplesheet` | required path | Four columns: `sample,fastq_1,fastq_2,status` |
| `illumina_analysis_type` | `solid_biopsy` | Analysis preset |
| `illumina_caller` | `qdnaseq` | Native Illumina CNA caller |
| `illumina_binsize_kb` | `100` | Coarse qDNAseq bin width |
| `illumina_build_pon` | `false` | Build/apply local qDNAseq normal panel |
| `illumina_pon_normal_samples` | comma-separated IDs | Every and only selected normal samples |
| `illumina_pon_min_normals` | `2` | Minimum selected controls; must be at least two |
| `illumina_pon_name` | `illumina_local_PoN` | Safe panel artifact name |
| `illumina_pon_min_mapq` | `37` | Panel construction mapping-quality threshold |

Automatic Setup writes no panel for zero normals, rejects exactly one normal, and enables the local panel for two or more. Normal samples remain reference/QC inputs; downstream corrected outputs contain tumors.

## ONT YAML fields

| Field | Typical/default | Meaning |
| --- | --- | --- |
| `ont_folder` | required path | Parent containing selected barcode directories |
| `ont_barcodes` | required comma-separated names | Tumor barcode selection |
| `ont_sample_names` | barcode names when omitted | Positional biological sample names |
| `ont_analysis_type` | `liquid_biopsy` | Analysis preset |
| `ont_caller` | `ichorcna` | `ichorcna`, or `qdnaseq` only with explicit `ont_analysis_type: solid_biopsy` |
| `ont_binsize_kb` | `500` | Coarse caller bin width; set explicitly for qDNAseq solid-biopsy runs |
| `ont_ref` | optional FASTA | Custom reference |
| `ont_normal_folder` | optional path | Parent containing normal barcodes |
| `ont_normal_barcodes` | optional names | Positional normal barcode selection |
| `ont_normal_sample_names` | optional names | Positional normal sample names |
| `ont_build_pon` | `false` | Explicit local-normal route request |
| `ont_min_age_minutes` | `0` | Exclude very new FASTQs in active run folders |
| `ont_force_realign` | `false` | Deliberately recreate supported ONT alignments |

`ont_barcodes` and `ont_sample_names` must have equal lengths and order.

## Native classifier and pathology fields

| Field | Typical/default | Meaning |
| --- | --- | --- |
| `cna_classifier_sample_set` | `broad_cancer` | Study-defined cancer context |
| `cna_classifier_samples` | optional IDs | Optional subset of sequencing samples |
| `pathology_csv` | optional path | De-identified matched pathology table |
| `pathology_sample_col` | `illumina_sample_id` | Column matching sequencing sample IDs |
| `pathology_case_col` | `case_code` | De-identified case/accession column |
| `pathology_diagnosis_col` | `final_diagnosis` | Diagnosis text column |
| `pathology_use_biomed_models` | `false` recommended initially | Optional biomedical model assistance |
| `pathology_biomed_local_files_only` | `true` recommended initially | Restrict models to local cache |
| `run_gistic` | `false` unless enabled | Optional cohort recurrence branch |
| `gistic_required` | `false` | Make GISTIC2 failure fatal only when justified |
| `gistic_min_samples` | `2` | Minimum cohort size for requested recurrence analysis |
| `knowledge_web` | `false` recommended | Optional network enrichment |
| `knowledge_literature_llm` | `false` recommended | Optional model-assisted literature synthesis |
| `knowledge_deep_literature` | `false` recommended | Optional expanded literature workflow |

Nested YAML is deliberately rejected. Keep this section flat.

## Boundary-refinement fields

| Field | Default | Meaning |
| --- | ---: | --- |
| `fine_bin_kb_illumina` | `10` | Local Illumina depth bin |
| `fine_bin_kb_ont` | `25` | Local ONT depth bin |
| `search_radius_bins` | `2` | Coarse-bin search distance |
| `min_mapq` | `20` | Minimum BAM mapping quality |
| `min_local_log2_diff_illumina` | `0.10` | Minimum Illumina local step |
| `min_local_log2_diff_ont` | `0.12` | Minimum ONT local step |
| `min_adjacent_seg_delta` | `0.10` | Minimum adjacent coarse difference |
| `min_bic_gain` | `6` | Minimum model-fit improvement |
| `permutations` | `300` | Empirical permutations |
| `permutation_p` | `0.05` | Empirical threshold |
| `accept_rule` | `p_and_bic` | Boundary acceptance rule |
| `max_ci_fraction_of_coarse` | `1.0` | Maximum CI width relative to coarse bin |
| `zipcnv_mode` | `adapted` | ZIPcnv comparison mode |
| `zipcnv_window_bins` | `5` | Adapted comparison window |
| `zipcnv_k` | `0.05` | Adapted tuning constant |
| `zipcnv_min_segment_bins` | `3` | Minimum retained segment length |
| `zipcnv_min_abs_log2` | `0.25` | Minimum retained absolute signal |
| `zipcnv_compare_min_overlap` | `0.50` | Minimum comparison overlap |

Keep defaults for an initial analysis. Use a new YAML and new `outdir` for methods comparisons.

## Provenance command

```bash
oncotracer provenance --json
```

The record includes version, source commit, deterministic source digest definition/value, source-tree cleanliness, and copied-binary SHA-256 when bound into a release build.
