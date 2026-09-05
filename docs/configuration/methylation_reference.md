# Methylation resource and processing reference

For the short workflow and an explanation of flags and paths, start with the [methylation guide](methylation.md). This page retains the manual POD5 configuration supported by v2.0.0 and describes the additions on current source.

## Obtain the optional resources

Install these separately, following their own instructions and licenses:

| Resource | Official source | Path to give OncoTracer |
| --- | --- | --- |
| Dorado | [ONT Dorado releases](https://github.com/nanoporetech/dorado/releases) | The `dorado` executable; not `dorado_basecall_server` |
| Modkit | [ONT Modkit releases](https://github.com/nanoporetech/modkit/releases) | The `modkit` executable |
| samtools | [samtools installation](https://www.htslib.org/download/) | The `samtools` executable |
| MARLIN | [MARLIN installation and model download](https://github.com/hovestadt/MARLIN#installation) | Rscript/Python in the MARLIN environment and the files below |
| Sturgeon | [Sturgeon installation and model instructions](https://github.com/UMCUGenetics/sturgeon) | Its executable, model archive, and hg38 probe BED |

MARLIN's files are `marlin_v1.model.hdf5`, `marlin_v1.features.RData`, `marlin_v1.class_annotations.xlsx`, and `marlin_v1.probes_hg38.bed.gz` in `MARLIN_realtime/files/`. Obtain the model through the upstream download link. Decompress the hg38 probe file to `.bed` before supplying it to OncoTracer; do not use the hg19 or t2t probe file with the hg38 alignment route.

MARLIN needs compatible R/Keras and Python/TensorFlow packages. `marlin_rscript` and `marlin_python` must point to that environment, not an unrelated system installation. With current source, `oncotracer setup` collects these paths and records hashes; `--resources` can reuse them for another project. A locally computed hash records the file you chose; it does not authenticate the file's upstream origin.

OncoTracer can run an optional methylation branch for Oxford Nanopore Technologies (ONT) analyses. Choose Sturgeon for a CNS-tumor research classifier or MARLIN for a leukemia research classifier. OncoTracer does not infer the disease type: the user must select exactly one classifier explicitly.

This branch is research-only. A methylation class is not a diagnosis and must be reviewed with morphology, immunophenotyping, cytogenetics, validated molecular assays, and the assay-specific quality controls.

## What the branch does

```text
explicit POD5 directory + selected barcode FASTQ read IDs
  -> Dorado hg38 basecalling with an explicit 5mCG/5hmCG model
  -> one aligned modBAM, then one read-ID-filtered modBAM per sample
  -> Modkit conversion of 5hmC to 5mC at CpG sites
  -> deterministic CpG bedMethyl pileup
  -> stop this sample's methylation branch if zero usable modified-CpG calls
  -> Sturgeon (CNS) or MARLIN (leukemia) classification
```

Methylation starts before the CNA branch. The two outcomes are independent:

- if methylation fails or has no usable modified-CpG calls, CNA still runs;
- if CNA fails, a completed methylation result remains available;
- the final summary records both statuses and returns nonzero when a requested branch is incomplete.

No classifier is launched for a sample with zero usable modified-CpG calls. Its status is `no_cpg_modifications`, not a fabricated negative classification.

## What you must provide

The v2.0.0 POD5 route requires an explicit non-empty POD5 directory:

```text
--pod5-dir /absolute/path/to/pod5_pass
```

Current source also accepts `--modbam PATH` or `methylation_modbam: PATH` instead. PATH may be one completed BAM or a directory of batches. These BAMs must contain MM/ML modification tags. OncoTracer selects matching primary reads, then aligns them to its hg38 reference on CPU, preserving the existing base/modification calls. No POD5 or Dorado basecalling-model directories are required for this route. Do not combine both input types in the same configuration.

OncoTracer never searches the server for POD5 data. It rejects symlinks anywhere in the explicit POD5 tree, rejects empty POD5 files, and requires at least one regular non-empty `.pod5` file. The POD5 data must correspond to the read IDs in the selected `ont_folder` barcode FASTQs; those FASTQs define the sample-to-read mapping.

Also provide:

- an hg38 FASTA through the normal OncoTracer reference route;
- a local Dorado executable;
- a local Modkit executable;
- an explicit Dorado basecalling-model directory (POD5 route only);
- an explicit, compatible Dorado 5mCG/5hmCG model directory (POD5 route only);
- classifier-specific executable/model/probe resources and exact SHA-256 values.

OncoTracer does not download, install, discover, or update these optional resources. It writes only below `<outdir>/07_methylation/`; it never indexes or modifies the source POD5/FASTQ directories.

## Backend restriction

Use `host`, `conda`, or `poetry` with explicit local executable/resource paths. The v2.0.0 Docker and Singularity/Apptainer image deliberately does not redistribute Dorado, Sturgeon, their models, or user-licensed classifier resources, so the CLI rejects optional methylation with those container backends.

## CNS example: Sturgeon

Add the resource fields below to the same flat ONT YAML. Replace every path and SHA-256 with the exact local value:

```yaml
methylation_dorado_executable: /opt/ont/dorado/bin/dorado
methylation_modkit_executable: /opt/ont/modkit/bin/modkit
methylation_samtools_executable: /usr/bin/samtools
methylation_dorado_model: /opt/ont/models/dna_r10.4.1_e8.2_400bps_sup
methylation_dorado_model_sha256: replace_with_model_tree_sha256
methylation_dorado_modbase_model: /opt/ont/models/dna_r10.4.1_e8.2_400bps_sup_5mCG_5hmCG
methylation_dorado_modbase_model_sha256: replace_with_modbase_model_tree_sha256

sturgeon_interface_contract_commit: 4c742ddea49b0077a8f8ff3d99daafb238d00706
sturgeon_license_acknowledged: true
sturgeon_executable: /opt/sturgeon/bin/sturgeon
sturgeon_model: /opt/sturgeon/models/general.zip
sturgeon_model_sha256: replace_with_64_character_sha256
sturgeon_probes: /opt/sturgeon/probes/probes_hg38.bed
sturgeon_probes_sha256: replace_with_64_character_sha256
```

Then run:

```bash
cd /path/to/my/analyses_dir/

oncotracer run \
  --backend conda \
  --config "$PWD/project/config/ont.yml" \
  --methylation \
  --sturgeon \
  --pod5-dir /absolute/path/to/pod5_pass \
  --gpu
```

Sturgeon is not distributed by OncoTracer. The pinned commit above identifies the upstream interface this adapter was developed against; it does **not** authenticate the source of the separately installed Sturgeon package. OncoTracer records the explicit external launcher path and SHA-256 and marks its installed source as unauthenticated. The user must separately obtain, install, and accept the applicable Sturgeon license. `sturgeon_license_acknowledged: true` is an explicit user attestation; it does not grant or replace that license.

## Leukemia example: MARLIN

Use the same Dorado/Modkit fields and add:

```yaml
marlin_interface_contract_commit: 37c9836cc325ff2edccbdff06736604163db2c15
marlin_rscript: /opt/marlin/bin/Rscript
marlin_python: /opt/marlin/bin/python
marlin_model: /opt/marlin/assets/marlin_v1.model.hdf5
marlin_model_sha256: replace_with_64_character_sha256
marlin_features: /opt/marlin/assets/marlin_v1.features.RData
marlin_features_sha256: replace_with_64_character_sha256
marlin_class_annotations: /opt/marlin/assets/marlin_v1.class_annotations.xlsx
marlin_class_annotations_sha256: replace_with_64_character_sha256
marlin_probe_bed: /opt/marlin/assets/marlin_v1.probes_hg38.bed
marlin_probe_bed_sha256: replace_with_64_character_sha256
```

Then run:

```bash
cd /path/to/my/analyses_dir/

oncotracer run \
  --backend conda \
  --config "$PWD/project/config/ont.yml" \
  --methylation \
  --marlin \
  --pod5-dir /absolute/path/to/pod5_pass \
  --gpu
```

The explicit Python must already provide `h5py`, NumPy, and TensorFlow. OncoTracer disables reticulate's managed environments and offline-locks its cache, so a missing dependency fails before POD5 basecalling instead of triggering a download. The native adapter preserves the preprocessing defined by the supported MARLIN interface commit: per-probe beta is modified coverage divided by valid coverage; model features are ordered exactly, binarized at beta `0.5`, and uncovered features remain zero. OncoTracer does not alter classifier thresholds.

The adapted MARLIN preprocessing code retains its upstream MIT terms; the complete copyright and permission notice is shipped in the executable payload as `bin/scripts/MARLIN-MIT-LICENSE.txt`.

## GPU behavior

`--gpu` selects `cuda:all` for Dorado modified-base basecalling. It also makes the GPU visible to the external MARLIN R/Keras process; whether MARLIN uses it depends on that user-supplied environment. Modkit and Sturgeon do not gain GPU acceleration from this flag and remain CPU-threaded.

Without a GPU request in the flags or YAML, Dorado is forced to `cpu`, MARLIN receives no visible GPU, and all methylation tools run without GPU access. Current source adds `--cpu` to override `methylation_gpu: true` in an existing YAML. The selected device and executable hashes are written to methylation provenance.

Current source accepts `--methylation-only` or `methylation_only: true` to skip the CNA branch. `setup --analysis methylation` saves this setting automatically. The summary then reports `cna_status: not_requested`.

## Validate before running

Compute exact file hashes with:

```bash
sha256sum \
  /absolute/path/to/classifier-model \
  /absolute/path/to/classifier-probes
```

Dorado model directories use an OncoTracer tree digest over sorted relative paths and file contents. Omit the two optional Dorado expected-tree hash fields on the first dry-run, copy the reported `asset_sha256` values from the JSON plan, then place those exact values in the YAML for the real run.

Run a side-effect-free validation first:

```bash
cd /path/to/my/analyses_dir/

oncotracer run \
  --backend conda \
  --config "$PWD/project/config/ont.yml" \
  --methylation \
  --sturgeon \
  --pod5-dir /absolute/path/to/pod5_pass \
  --gpu \
  --dry-run
```

The dry-run scans and hashes only the explicitly supplied inputs, prints the methylation-before-CNA plan, and creates no output, environment, image, model, or persistent configuration.

## Resume and force

Repeat the identical command to reuse content-matched Dorado, Modkit, and classifier outputs. Read-ID manifests are written deterministically so their timestamps remain stable when content is unchanged.

Use `--force` only to deliberately invalidate these outputs. For a different POD5 set, model, classifier, reference, or biological analysis, use a new `outdir`.

## Output and status files

```text
07_methylation/
├── read_ids/
├── modbam/
├── bedmethyl/
├── sturgeon/ or marlin/
├── logs/
├── sample_status/
├── methylation_status.json
└── methylation_provenance.json
```

`methylation_status.json` reports each sample as `complete`, `no_cpg_modifications`, `no_classifier_probes` (MARLIN, current source), or `failed`. `methylation_provenance.json` records the explicit POD5 or BAM inventory digest, device choice, supported classifier interface commit, external-runtime disclosure, executable SHA-256 values, resource SHA-256 values, and final status. The interface commit is not an assertion about the installed external package's source. Review these files before interpreting any prediction.

Current source excludes secondary and supplementary alignments before Modkit conversion: these may lack MM tags and must not be double-counted. Pileup uses explicit sampling settings. MARLIN probe overlap is checked before prediction; zero overlap produces `no_classifier_probes` with `covered_classifier_probes: 0`.
