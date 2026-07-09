# Example Data

This page lists public FASTQ data that can be used to test the OncoTracer entry points.

!!! note "Public data purpose"
    These examples are for exercising the workflow and learning the input formats. They are not packaged clinical validation datasets.

## Illumina Low-Pass WGS FASTQ

ENA run `DRR000542` is a human Illumina paired-end WGS run with about 2.25 Gb of bases, roughly low-pass human genome coverage.

Archive metadata verified from ENA:

| Field | Value |
| --- | --- |
| Run | `DRR000542` |
| Study | `PRJDA50447` |
| Platform | `ILLUMINA` |
| Layout | `PAIRED` |
| Strategy | `WGS` |
| Base count | `2246358904` |

Download and create an Illumina samplesheet:

```bash
mkdir -p data/public/illumina_DRR000542
cd data/public/illumina_DRR000542
curl -L -o DRR000542_1.fastq.gz   ftp://ftp.sra.ebi.ac.uk/vol1/fastq/DRR000/DRR000542/DRR000542_1.fastq.gz
curl -L -o DRR000542_2.fastq.gz   ftp://ftp.sra.ebi.ac.uk/vol1/fastq/DRR000/DRR000542/DRR000542_2.fastq.gz
printf 'sample,fastq_1,fastq_2,bam,gender,status
DRR000542,%s,%s,,,tumor
'   "$PWD/DRR000542_1.fastq.gz"   "$PWD/DRR000542_2.fastq.gz" > illumina.samplesheet.csv
```

Then edit `params/illumina.example.yml` so `illumina_samplesheet` points to `illumina.samplesheet.csv`.

## ONT Low-Coverage WGS FASTQ

ENA run `DRR165691` is a small human Oxford Nanopore MinION WGS run from a lung cancer genome study.

Archive metadata verified from ENA:

| Field | Value |
| --- | --- |
| Run | `DRR165691` |
| Study | `PRJDB7926` |
| Platform | `OXFORD_NANOPORE` |
| Instrument | `MinION` |
| Layout | `SINGLE` |
| Strategy | `WGS` |
| Base count | `93738336` |

Download into an ONT barcode-style folder:

```bash
mkdir -p data/public/ont_DRR165691/fastq_pass/barcode01
cd data/public/ont_DRR165691/fastq_pass/barcode01
curl -L -o DRR165691_1.fastq.gz   ftp://ftp.sra.ebi.ac.uk/vol1/fastq/DRR165/DRR165691/DRR165691_1.fastq.gz
```

Then edit `params/ont.example.yml`:

```yaml
ont_folder: /absolute/path/to/data/public/ont_DRR165691/fastq_pass
ont_barcodes: barcode01
ont_sample_names: DRR165691
```

## Why Not Bundle FASTQ Files?

Human FASTQ files are too large for the GitHub repository. The repository includes configuration templates and verified public download commands instead.
