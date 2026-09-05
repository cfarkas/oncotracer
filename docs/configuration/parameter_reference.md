# Native CLI and YAML parameter reference

Start with [guided setup](../setup.md), or [batch setup](../auto_params.md) for many libraries. This page lists the commands and YAML fields.

## Command structure

```text
oncotracer install ...
oncotracer doctor ...
oncotracer setup --project PATH ...
oncotracer check --config FILE
oncotracer system --path PATH
oncotracer reference install ...
oncotracer uninstall ...
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

| Option | Accepted with | Meaning |
| --- | --- | --- |
| `--conda` | exactly one backend | Create/update five isolated Conda prefixes |
| `--docker` | exactly one backend | Validate Docker, pull the native image, and run its host doctor |
| `--singularity` | exactly one backend | Pull/reuse a SIF through Apptainer or Singularity |
| `--poetry` | exactly one backend | Install an isolated source-development launcher and the five Conda prefixes |
| `--prefix PATH` | Conda, Poetry | Dedicated owned parent for fixed managed children |
| `--image IMAGE` | Docker, Singularity | Override the default native container image |
| `--sif PATH` | Singularity | Override the managed Singularity/Apptainer image path |
| `--force` | Conda, Poetry, Singularity | Transactionally replace intact, owned assets only |
| `--dry-run` | every backend | Print installation commands without executing them or writing state |
| `--root PATH` | Conda, Poetry | Explicit source or extracted payload root |

Backend-irrelevant options are errors; they are never silently ignored. Conda
and Poetry prefixes must be absent, empty, or have exact root and child
ownership markers. A SIF destination must be absent on first installation or
form an intact file/sidecar pair already owned by OncoTracer. `--force` cannot
adopt, unlink, or recursively remove an unowned target.

## `oncotracer doctor`

```bash
oncotracer doctor --backend conda
oncotracer doctor --backend docker
oncotracer doctor --backend singularity
oncotracer doctor --backend poetry
```

The command returns JSON and exits nonzero when required source identity, prefixes, packages, or semantic executable probes fail.

## `oncotracer setup` and `oncotracer check`

`setup` saves the same YAML used by ordinary runs. It asks for missing inputs
unless `--non-interactive` is supplied. It does not start analysis.

| Option | Meaning |
| --- | --- |
| `--project PATH` | New project containing `config/run.yml` and future results |
| `--mode illumina` or `--mode ont` | Sequencing platform |
| `--analysis cna`, `methylation` or `both` | Requested analysis; methylation requires ONT |
| `--fastq-1 FILE`, `--fastq-2 FILE`, `--sample-name NAME` | One Illumina library; omit R2 for single-end |
| `--samplesheet FILE` | Multiple Illumina libraries in a sample/R1/R2/status CSV |
| `--reads-folder PATH` | ONT `fastq_pass` directory |
| `--barcodes LIST`, `--sample-names LIST` | Matching comma-separated barcode folders and sample names |
| `--reference-root PATH` | Optional shared reference directory; defaults to `PROJECT/reference` |
| `--threads NUMBER` | CPU workers to request |
| `--non-interactive` | Require inputs as flags rather than prompts |

`oncotracer check --config FILE` reports missing paths/settings and the planned
samples without running analysis. See [setup examples](../setup.md),
[QuickStart 1](../quick_start.md), and [QuickStart 2](../public_cohort.md) for
complete commands. [Reference installation](../reference_indexes.md) is optional
when compatible indexes are already present.

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
| `--methylation` | Add ONT methylation analysis to the copy-number workflow |
| `--sturgeon` | Select the supported CNS-tumor research classifier; mutually exclusive with `--marlin` |
| `--marlin` | Select the supported leukemia research classifier; mutually exclusive with `--sturgeon` |
| `--pod5-dir PATH` | Raw POD5 input for methylation basecalling; choose this or `--modbam` |
| `--modbam PATH` | reuse calls from an existing modified-base BAM or directory of BAMs; CPU alignment |
| `--methylation-only` | request methylation and skip copy-number analysis |
| `--cpu` | keep methylation on CPU, overriding GPU YAML settings |
| `--gpu` | Use `cuda:all` for Dorado and expose the GPU to MARLIN; requires `--methylation` |

Repeating the same command reuses valid content-matched stages automatically.

Optional methylation is ONT-only and supports the `host`, `conda`, and `poetry` backends with explicit user-installed resources. The stable container does not redistribute the licensed tools/models, so Docker and Singularity/Apptainer reject this branch.

## Common YAML fields

| Field | Type/default | Meaning |
| --- | --- | --- |
| `mode` | required `illumina` or `ont` | Sequencing route |
| `lpwgs_root` | required absolute directory | Project/reference/cache root visible to the backend |
| `outdir` | required absolute directory | Dedicated absent, empty, or exact-runtime-owned native result tree |
| `force` | Boolean, `false` | Scientific refresh request; normally keep false |
| `run_cna_classifier` | Boolean, `false` | Add stage `05_cna_classifier` |

## Illumina YAML fields

| Field | Typical/default | Meaning |
| --- | --- | --- |
| `illumina_samplesheet` | required path | Four columns: `sample,fastq_1,fastq_2,status` |
| `illumina_analysis_type` | `solid_biopsy` | Analysis preset |
| `illumina_caller` | `qdnaseq` | Native Illumina CNA caller |
| `illumina_binsize_kb` | `100` | Coarse qDNAseq bin width |

The samplesheet `status` column preserves `tumor` or `normal` metadata. Every
row is analyzed independently; normal rows are not used to construct or apply a
local sample-derived reference.

## ONT YAML fields

| Field | Typical/default | Meaning |
| --- | --- | --- |
| `ont_folder` | required path | Parent containing selected barcode directories |
| `ont_barcodes` | required comma-separated names | Tumor barcode selection |
| `ont_sample_names` | barcode names when omitted | Positional biological sample names |
| `ont_normal_folder` | optional path | Parent containing independent NORMAL barcode directories; requires solid-biopsy qDNAseq |
| `ont_normal_barcodes` | required with `ont_normal_folder` | NORMAL barcode selection; each barcode is analyzed as its own sample |
| `ont_normal_sample_names` | NORMAL barcode names when omitted | Positional NORMAL sample names; never pooled into a panel |
| `ont_analysis_type` | `liquid_biopsy` | Analysis preset |
| `ont_caller` | `ichorcna` | `ichorcna`, or `qdnaseq` only with explicit `ont_analysis_type: solid_biopsy` |
| `ont_binsize_kb` | `500` | Coarse caller bin width; set explicitly for qDNAseq solid-biopsy runs |
| `ont_ref` | optional FASTA | Custom reference |
| `ont_min_age_minutes` | `0` | Exclude very new FASTQs in active run folders |
| `ont_force_realign` | `false` | Deliberately recreate supported ONT alignments |

`ont_barcodes` and `ont_sample_names` must have equal lengths and order. The corresponding NORMAL lists must also match, sample names and resolved barcode directories must be unique across both groups, and a mixed TUMOR/NORMAL run uses native qDNAseq rather than the frozen Nextflow comparator.

## Optional ONT methylation YAML fields

CLI values override methylation, classifier, input, and GPU YAML values. Choose one explicit methylation input: POD5 or modified-base BAM. Conflicting input types in flags and YAML are rejected.

| Field | Typical/default | Meaning |
| --- | --- | --- |
| `methylation` | `false` | Enable optional ONT methylation when not using `--methylation` |
| `methylation_classifier` | required when enabled | Exactly `sturgeon` or `marlin` |
| `methylation_pod5_dir` | one input required | Explicit directory containing non-empty `.pod5` files |
| `methylation_modbam` | one input required | existing modified-base BAM file or directory; replaces POD5 input |
| `methylation_only` | `false` | skip CNA; enabled by `setup --analysis methylation` |
| `threads` | up to 16 if not set | CPU worker threads; `--threads` overrides this value |
| `methylation_gpu` | `false` | Dorado `cuda:all`; MARLIN GPU visibility; Modkit/Sturgeon remain CPU |
| `methylation_reference_build` | `hg38` | Only supported methylation reference build in v2.0.0 |
| `methylation_dorado_executable` | required local executable | Dorado binary; no download or installation |
| `methylation_modkit_executable` | required local executable | Modkit binary; no download or installation |
| `methylation_samtools_executable` | `samtools` | Explicit or PATH-resolved SAMtools binary |
| `methylation_dorado_model` | required for POD5 | Explicit compatible Dorado basecalling model |
| `methylation_dorado_model_sha256` | optional expected digest | OncoTracer directory-tree digest; always recorded |
| `methylation_dorado_modbase_model` | required for POD5 | Explicit compatible 5mCG/5hmCG model |
| `methylation_dorado_modbase_model_sha256` | optional expected digest | OncoTracer directory-tree digest; always recorded |
| `sturgeon_interface_contract_commit` | fixed supported upstream interface commit; does not authenticate the installed package | `4c742ddea49b0077a8f8ff3d99daafb238d00706` |
| `sturgeon_license_acknowledged` | required `true` | User attests to separately obtaining/accepting the Sturgeon license |
| `sturgeon_executable` | required | User-installed Sturgeon executable |
| `sturgeon_model`, `sturgeon_model_sha256` | required pair | Explicit model and exact file SHA-256 |
| `sturgeon_probes`, `sturgeon_probes_sha256` | required pair | Explicit hg38 probes and exact file SHA-256 |
| `marlin_interface_contract_commit` | fixed supported upstream interface commit; does not authenticate an external runtime | `37c9836cc325ff2edccbdff06736604163db2c15` |
| `marlin_rscript` | required | Rscript from the user-prepared MARLIN environment |
| `marlin_python` | required | Exact Python with h5py, NumPy, and TensorFlow; automatic environments are forbidden |
| `marlin_model`, `marlin_model_sha256` | required pair | Explicit MARLIN model and exact file SHA-256 |
| `marlin_features`, `marlin_features_sha256` | required pair | Explicit feature RData and exact file SHA-256 |
| `marlin_class_annotations`, `marlin_class_annotations_sha256` | required pair | Explicit class workbook and exact file SHA-256 |
| `marlin_probe_bed`, `marlin_probe_bed_sha256` | required pair | Explicit hg38 probe BED and exact file SHA-256 |

The classifier is not invoked for a sample with zero usable modified-CpG calls. CNA continues, and the final status reports the two branches independently. See [Optional ONT methylation](methylation.md) for the complete setup, dry-run, resume, license, and outputs.

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
| `knowledge_literature_llm_models` | model IDs or absolute model paths | Comma-separated generation fallbacks; Hub IDs accept `@commit` |
| `knowledge_literature_llm_local_files_only` | `false` | Set `true` to forbid generation-model downloads |
| `knowledge_llm_threads` | `4` | CPU threads for report generation and reference ranking |
| `knowledge_literature_llm_max_features` | `24` | Maximum features to synthesize |
| `knowledge_literature_llm_max_input_chars` | `2800` | Prompt character cap; model token limits also apply |
| `knowledge_literature_llm_max_new_tokens` | `96` | Generation token cap; `192` allows more room for structured citations |
| `knowledge_literature_reference_llm_selection` | `true` | Optional per-feature ranking when synthesis is enabled |
| `knowledge_deep_enable_llm_ranker` | `true` | Separate sample-wide LLM paper selection |
| `knowledge_deep_llm_ranker_models` | model IDs or absolute model paths | Models for sample-wide ranking |
| `knowledge_deep_llm_ranker_local_files_only` | `false` | Set `true` to forbid ranker-model downloads |
| `knowledge_deep_llm_ranker_max_candidates_per_sample` | `18` | Maximum papers submitted to the sample-wide ranker |
| `knowledge_deep_literature` | `false` recommended | Optional expanded literature workflow |

See [LLM-assisted reports](../llm_reports.md) for a short working configuration and
the difference between local generation and public literature retrieval.

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
