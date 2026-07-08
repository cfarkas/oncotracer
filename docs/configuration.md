# YAML Configuration

A YAML file is a small plain-text settings file. In OncoTracer, YAML files tell the workflow where your input files are, where results should be written, and which optional steps to run.

Each setting is written as:

```yaml
field_name: value
```

Keep the field name and colon, then edit the value after the colon. Use spaces, not tabs. Lines beginning with `#` are comments for humans and are ignored by the workflow.

## Where Are The YAML Files?

Example YAML files live in the repository folder:

```text
params/illumina.example.yml
params/ont.from_existing_samurai.example.yml
params/ont.example.yml
```

Do not edit the original examples directly. Copy the closest one and edit your copy:

```bash
cp params/illumina.example.yml params/my_illumina.yml
nano params/my_illumina.yml
```

Run your edited YAML with Docker:

```bash
nextflow run main.nf --docker \
  -params-file params/my_illumina.yml \
  -resume
```

!!! tip "Absolute paths are easiest"
    Use full paths such as `/data/project/sample.bam` instead of relative paths. This makes Docker, Singularity/Apptainer, and HPC runs easier to reproduce.

## Common Fields

These fields appear in most YAML files.

| Field | Required | Meaning |
|---|---:|---|
| `mode` | yes | Workflow branch. Use `illumina` for Illumina qDNAseq/SAMURAI inputs or `ont` for Oxford Nanopore inputs. |
| `lpwgs_root` | recommended | Root folder for this project/data area. OncoTracer also uses this as the default folder to expose to Docker containers. |
| `outdir` | yes | Main output folder. Numbered result folders are written inside this path. |
| `run_cna_classifier` | no | `true` runs classifier, reports, and optional pathology concordance. `false` stops after core CNA plots. |
| `force` | no | Allows underlying steps to overwrite/recompute outputs. Useful for testing; use carefully with important result folders. |
| `docker_user` | advanced | Docker UID:GID used internally when `--docker` is set. Default is `1000:1000`. |
| `docker_run_options` | advanced | Extra Docker options if your site needs them. Most users leave this blank. |
| `singularity_run_options` | advanced | Extra Singularity/Apptainer options if your HPC site needs them. Most users leave this blank. |

## Illumina YAML

Use this when you already have Illumina SAMURAI/qDNAseq outputs and aligned BAM files.

Example file: `params/illumina.example.yml`

```yaml
# Workflow branch. Use "illumina" for Illumina SAMURAI/qDNAseq + BAM inputs.
mode: illumina

# Root folder for this project/data area.
lpwgs_root: /media/server/STORAGE/LPWGS_2025

# Main output folder.
outdir: /media/server/STORAGE/LPWGS_2025/CNA_analyses/OncoTracer_illumina_test

# Folder containing existing qDNAseq/SAMURAI CNA output files.
illumina_qdnaseq_dir: /media/server/STORAGE/LPWGS_2025/samurai_results_100kb/qdnaseq

# Folder containing aligned Illumina BAM files for the same samples.
illumina_bam_dir: /media/server/STORAGE/LPWGS_2025/samurai_results_100kb/alignment

# Prior segmentation table from the upstream Illumina CNA caller.
illumina_prior_seg: /media/server/STORAGE/LPWGS_2025/samurai_results_100kb/qdnaseq/all_segments.seg

# Coarse bin size, in kb, used by the upstream Illumina CNA analysis.
illumina_binsize_kb: 100

# Run optional classifier/report stages after the core CNA workflow.
run_cna_classifier: false

# Optional pathology table. Use null when unavailable.
pathology_csv: null

# Column names used if pathology_csv is provided.
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis

# Optional biomedical model agreement scoring for pathology concordance.
pathology_use_biomed_models: true
pathology_biomed_local_files_only: false

# Allow overwrite/recompute behavior.
force: true
```

Field-by-field:

| Field | Meaning |
|---|---|
| `mode` | Must be `illumina` for this input type. |
| `lpwgs_root` | Root folder containing your project data. Keep it as an absolute path. |
| `outdir` | Folder where OncoTracer writes all outputs for this run. |
| `illumina_qdnaseq_dir` | Existing qDNAseq/SAMURAI CNA output directory. |
| `illumina_bam_dir` | Directory containing aligned Illumina BAM files. |
| `illumina_prior_seg` | Prior segment table from qDNAseq/SAMURAI, usually `all_segments.seg`. |
| `illumina_binsize_kb` | Coarse bin size used upstream. Use the same bin size as the original qDNAseq/SAMURAI run. |
| `run_cna_classifier` | Set `true` to run classifier/reports after plots. Start with `false` for a fast first test. |
| `pathology_csv` | Optional CSV/TSV/XLS/XLSX pathology table. Use `null` to disable pathology matching. |
| `pathology_sample_col` | Column in the pathology table that matches CNA sample names. |
| `pathology_case_col` | Column containing pathology case/accession IDs. |
| `pathology_diagnosis_col` | Column containing final diagnosis text. |
| `pathology_use_biomed_models` | Attempts optional biomedical transformer agreement scoring when pathology is provided. |
| `pathology_biomed_local_files_only` | If `true`, only locally cached Hugging Face models are used. |
| `force` | Allows overwrite/recompute behavior in underlying scripts. |

## ONT From Existing SAMURAI/ichorCNA YAML

Use this when SAMURAI/ichorCNA has already run and you want OncoTracer to start from BAM boundary refinement.

Example file: `params/ont.from_existing_samurai.example.yml`

```yaml
# Workflow branch. Use "ont" for Oxford Nanopore LP-WGS inputs.
mode: ont

# Root folder for this project/data area.
lpwgs_root: /media/server/STORAGE/LPWGS_2025

# Main output folder.
outdir: /media/server/STORAGE/LPWGS_2025/CNA_analyses/OncoTracer_ONT_existing_test

# Existing ONT ichorCNA result directory.
ont_ichor_dir: /media/server/STORAGE/LPWGS_2025/ONT_analyses/.../results/ichorcna

# Folder containing ONT BAM files for the same samples.
ont_bam_dir: /media/server/STORAGE/LPWGS_2025/ONT_analyses/.../bam

# Prior segmentation table from the upstream ONT ichorCNA analysis.
ont_prior_seg: /media/server/STORAGE/LPWGS_2025/ONT_analyses/.../segments_logR_corrected_gistic.seg

# Coarse bin size, in kb, used by the upstream ONT CNA analysis.
ont_binsize_kb: 500

# Run optional classifier/report stages after the core CNA workflow.
run_cna_classifier: false

# Allow overwrite/recompute behavior.
force: true
```

Field-by-field:

| Field | Meaning |
|---|---|
| `mode` | Must be `ont` for ONT input. |
| `lpwgs_root` | Root folder containing your project data. |
| `outdir` | Folder where OncoTracer writes all outputs for this run. |
| `ont_ichor_dir` | Existing ONT ichorCNA result directory. |
| `ont_bam_dir` | Directory containing ONT BAM files. |
| `ont_prior_seg` | Prior segment table used for boundary refinement. Usually `segments_logR_corrected_gistic.seg`. |
| `ont_binsize_kb` | Coarse bin size used by the upstream ONT analysis. |
| `run_cna_classifier` | Set `true` to run classifier/reports after plots. |
| `force` | Allows overwrite/recompute behavior in underlying scripts. |

When `ont_ichor_dir` is provided, OncoTracer skips `01_samurai_ont` and begins at `02_bam_refinement`.

## ONT From FASTQ/Barcodes YAML

Use this when you want OncoTracer to run the ONT SAMURAI step first.

Example file: `params/ont.example.yml`

```yaml
# Workflow branch. Use "ont" for Oxford Nanopore LP-WGS inputs.
mode: ont

# Root folder for this project/data area.
lpwgs_root: /media/server/STORAGE/LPWGS_2025

# Main output folder.
outdir: /media/server/STORAGE/LPWGS_2025/CNA_analyses/OncoTracer_ONT_test

# Folder containing ONT FASTQ files, barcode folders, or a completed run folder.
ont_folder: /path/to/ont/run/or/fastq_pass

# Comma-separated barcode IDs to process. Order must match ont_sample_names.
ont_barcodes: barcode07,barcode08

# Comma-separated sample names assigned to the barcodes above.
ont_sample_names: sample1,sample2

# Output folder for the upstream ONT SAMURAI step.
ont_samurai_outdir: /media/server/STORAGE/LPWGS_2025/CNA_analyses/OncoTracer_ONT_test/01_samurai_ont

# SAMURAI analysis label, commonly liquid_biopsy or solid_biopsy.
ont_analysis_type: liquid_biopsy

# CNA caller used by the ONT SAMURAI wrapper. Usually ichorcna.
ont_caller: ichorcna

# Coarse ONT CNA bin size, in kb.
ont_binsize_kb: 500

# Wait time for active sequencing folders. Use 0 for completed data.
ont_min_age_minutes: 0

# Optional fields you can add when needed:
# ont_ref: /path/to/reference.fa
# ont_normal_folder: /path/to/normal_fastq_or_run
# ont_normal_barcodes: barcode01,barcode02
# ont_normal_sample_names: normal1,normal2
# ont_build_pon: false
# ont_force_realign: false

# Run optional classifier/report stages after the core CNA workflow.
run_cna_classifier: false

# Allow overwrite/recompute behavior.
force: true
```

Field-by-field:

| Field | Meaning |
|---|---|
| `mode` | Must be `ont` for ONT input. |
| `lpwgs_root` | Root folder containing your project data. |
| `outdir` | Folder where OncoTracer writes all outputs for this run. |
| `ont_folder` | Folder with ONT FASTQ files, barcode folders, or a completed run folder. |
| `ont_barcodes` | Comma-separated barcode names to process. Order must match `ont_sample_names`. |
| `ont_sample_names` | Comma-separated sample names assigned to the barcode list. |
| `ont_samurai_outdir` | Folder where the ONT SAMURAI step writes its outputs. |
| `ont_analysis_type` | Analysis label passed to SAMURAI, commonly `liquid_biopsy` or `solid_biopsy`. |
| `ont_caller` | CNA caller used by SAMURAI; usually `ichorcna`. |
| `ont_binsize_kb` | Coarse ONT CNA bin size in kb. |
| `ont_min_age_minutes` | Optional wait time for files in active sequencing folders. Use `0` for completed data. |
| `ont_ref` | Optional reference FASTA if required by your ONT wrapper setup. |
| `ont_normal_folder` | Optional folder containing normal/PoN FASTQ data. |
| `ont_normal_barcodes` | Optional comma-separated normal barcode list. |
| `ont_normal_sample_names` | Optional comma-separated names for normal barcode samples. |
| `ont_build_pon` | Optional `true`/`false` setting to build/use a panel of normals when supported. |
| `ont_force_realign` | Optional `true`/`false` setting to force realignment in the SAMURAI step. |
| `run_cna_classifier` | Set `true` to run classifier/reports after plots. |
| `force` | Allows overwrite/recompute behavior in underlying scripts. |

## Refinement Parameters

Most users should leave these defaults alone. They can be overridden in YAML or on the command line when you need advanced tuning.

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

Any YAML value can be overridden at run time. Command-line values win over the YAML file:

```bash
nextflow run main.nf --docker \
  -params-file params/illumina.example.yml \
  --run_cna_classifier true \
  --illumina_binsize_kb 100 \
  --force true \
  -resume
```
