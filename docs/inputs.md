# Inputs

OncoTracer is FASTQ-first. The public entry points start from Illumina paired-end FASTQ files or ONT `fastq_pass` barcode FASTQ files.

## Illumina FASTQ

Create a CSV samplesheet with this header:

```csv
sample,fastq_1,fastq_2,status
```

Required columns:

| Column | Meaning |
| --- | --- |
| `sample` | Unique sample name. Use letters, numbers, `_`, or `-`. |
| `fastq_1` | Absolute path to R1 FASTQ.gz. |
| `fastq_2` | Absolute path to R2 FASTQ.gz. |
| `status` | Use `tumor` for tumor-only LP-WGS runs. |

## ONT FASTQ

Point `ont_folder` to a run folder, `fastq_pass`, or a directory containing barcode folders.

```text
fastq_pass/
  barcode01/
    *.fastq.gz
  barcode02/
    *.fastq.gz
```

Set matching barcode and sample-name lists:

```yaml
ont_barcodes: barcode01,barcode02
ont_sample_names: caseA,caseB
```

## Pathology CSV

For pathology concordance, provide a CSV with at least:

- one sample identifier column matching OncoTracer sample names;
- one case identifier column;
- one diagnosis text column.

Configure the exact column names in `params/illumina.pathology.example.yml`.
