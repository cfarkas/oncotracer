# Configuration

OncoTracer uses YAML files for run settings. A YAML file is a plain-text file where each line usually looks like this:

```yaml
setting_name: value
```

Edit the value after the colon. Use absolute paths for files and folders. Do not use tabs.

## Which YAML Should I Copy?

| Use case | File |
| --- | --- |
| Illumina paired-end FASTQ | `params/illumina.example.yml` |
| ONT `fastq_pass` barcode FASTQ | `params/ont.example.yml` |
| Illumina FASTQ plus pathology CSV | `params/illumina.pathology.example.yml` |

## Common Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `mode` | yes | `illumina` or `ont`. |
| `lpwgs_root` | yes | Main project/data root. Docker and Singularity bind this folder by default. |
| `outdir` | yes | Main OncoTracer output folder. |
| `run_cna_classifier` | no | `true` to run classifier/report/pathology steps after core CNA outputs. |
| `force` | no | `true` lets underlying scripts recompute outputs in the selected output folder. |

## Illumina FASTQ YAML

```yaml
mode: illumina
lpwgs_root: /media/server/STORAGE/LPWGS_2025
outdir: /media/server/STORAGE/LPWGS_2025/CNA_analyses/OncoTracer_illumina_fastq_test
illumina_samplesheet: /media/server/STORAGE/LPWGS_2025/samurai_input/samplesheet.csv
illumina_samurai_outdir: /media/server/STORAGE/LPWGS_2025/CNA_analyses/OncoTracer_illumina_fastq_test/01_samurai_illumina
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: true
```

| Field | Meaning |
| --- | --- |
| `illumina_samplesheet` | CSV table listing paired FASTQ files. Columns: `sample,fastq_1,fastq_2,bam,gender,status`. |
| `illumina_samurai_outdir` | Where the upstream SAMURAI/qDNAseq step writes Illumina results. |
| `illumina_analysis_type` | SAMURAI analysis label, usually `solid_biopsy`. |
| `illumina_caller` | CNA caller for Illumina. Use `qdnaseq`. |
| `illumina_binsize_kb` | Coarse CNA bin size in kilobases, usually `100` for LP-WGS. |

## ONT FASTQ YAML

```yaml
mode: ont
lpwgs_root: /media/server/STORAGE/LPWGS_2025
outdir: /media/server/STORAGE/LPWGS_2025/CNA_analyses/OncoTracer_ONT_fastq_test
ont_folder: /path/to/ont/run/or/fastq_pass
ont_barcodes: barcode07,barcode08
ont_sample_names: sample1,sample2
ont_samurai_outdir: /media/server/STORAGE/LPWGS_2025/CNA_analyses/OncoTracer_ONT_fastq_test/01_samurai_ont
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
run_cna_classifier: false
force: true
```

| Field | Meaning |
| --- | --- |
| `ont_folder` | ONT run folder, `fastq_pass`, or folder containing barcode subfolders. |
| `ont_barcodes` | Comma-separated barcode folder names to process. |
| `ont_sample_names` | Comma-separated sample names in the same order as `ont_barcodes`. |
| `ont_samurai_outdir` | Where the upstream ONT SAMURAI step writes BAM and ichorCNA results. |
| `ont_analysis_type` | SAMURAI analysis label, often `liquid_biopsy` or `solid_biopsy`. |
| `ont_caller` | CNA caller for ONT. Use `ichorcna`. |
| `ont_binsize_kb` | Coarse CNA bin size in kilobases, usually `500`. |
| `ont_min_age_minutes` | Wait threshold for active sequencing folders. Use `0` for completed FASTQ data. |

## Pathology Fields

These fields are used when `run_cna_classifier: true` and a pathology CSV is available.

| Field | Meaning |
| --- | --- |
| `pathology_csv` | CSV file containing pathology annotations. Use `null` to skip pathology concordance. |
| `pathology_sample_col` | Column matching OncoTracer sample names. |
| `pathology_case_col` | Case or patient identifier column. |
| `pathology_diagnosis_col` | Diagnosis text column. |
| `pathology_use_biomed_models` | `true` to enable biomedical model agreement scoring. |
| `pathology_biomed_local_files_only` | `true` for offline runs that must not download model files. |

## Samplesheet Examples

Illumina FASTQ samplesheet:

```csv
sample,fastq_1,fastq_2,bam,gender,status
sample1,/data/sample1_R1.fastq.gz,/data/sample1_R2.fastq.gz,,,tumor
sample2,/data/sample2_R1.fastq.gz,/data/sample2_R2.fastq.gz,,,tumor
```

ONT FASTQ inputs are configured by folder and barcode names in YAML. A typical folder looks like:

```text
fastq_pass/
  barcode07/
    reads_001.fastq.gz
  barcode08/
    reads_001.fastq.gz
```
