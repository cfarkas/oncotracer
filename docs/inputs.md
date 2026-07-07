# Inputs

## ONT FASTQ/Barcode Inputs

Use `params/ont.example.yml` when starting from ONT FASTQ/barcodes.

Expected input:

```text
ont_folder/
  barcode07/
    *.fastq or *.fastq.gz
  barcode08/
    *.fastq or *.fastq.gz
```

The barcode names are supplied as:

```yaml
ont_barcodes: barcode07,barcode08
ont_sample_names: sample1,sample2
```

The order matters. `barcode07` becomes `sample1`; `barcode08` becomes `sample2`.

## Existing ONT SAMURAI/ichorCNA Inputs

Use `params/ont.from_existing_samurai.example.yml` when these already exist:

```text
ONT_run/
  results/ichorcna/
    segments_logR_corrected_gistic.seg
    ... other ichorCNA outputs ...
  bam/
    sample1.bam
    sample2.bam
```

Required paths:

```yaml
ont_ichor_dir: /path/to/results/ichorcna
ont_bam_dir: /path/to/bam
ont_prior_seg: /path/to/results/ichorcna/segments_logR_corrected_gistic.seg
```

## Illumina Inputs

Use `params/illumina.example.yml` when these already exist:

```text
samurai_results_100kb/
  qdnaseq/
    all_segments.seg
    ... qDNAseq bin/segment outputs ...
  alignment/
    sample1.bam
    sample2.bam
```

Required paths:

```yaml
illumina_qdnaseq_dir: /path/to/qdnaseq
illumina_bam_dir: /path/to/alignment
illumina_prior_seg: /path/to/qdnaseq/all_segments.seg
```

## Pathology Table Input

The pathology table is optional and used only when classifier/reporting is enabled.

Supported formats:

- `.csv`
- `.tsv`
- `.xls`
- `.xlsx`

Minimum useful columns:

| Column type | Example |
|---|---|
| sample ID | `illumina_sample_id` |
| case/accession ID | `case_code` |
| diagnosis | `final_diagnosis` |

Additional columns improve pathology inference:

| Information | Example column names |
|---|---|
| diagnosis categories | `diagnosis_category_1`, `diagnosis_category_2` |
| clinical diagnosis | `clinical_diagnosis_1`, `clinical_diagnosis_2` |
| organ/site | `specimen_organ`, `anatomical_site_1`, `anatomical_site_2` |
| microscopic text | `microscopic_summary_en` |
| macroscopic text | `macroscopic_summary_en` |
| IHC/marker summary | `marker_results_standardized` |
| age/report metadata | `age_years`, `report_datetime` |

Example YAML:

```yaml
pathology_csv: /path/to/complete_biopsy_database_sanitized.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: true
```
