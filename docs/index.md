# OncoTracer Documentation

OncoTracer is a Nextflow workflow for reproducible CNA analysis from LP-WGS data. It supports ONT and Illumina inputs, performs BAM-supported CNA boundary refinement, creates standardized CNA event tables, generates custom CNA plots, and optionally produces CNA classification, literature-backed reports, and pathology concordance outputs.

## What You Need To Run It

Most users only need to edit one YAML file in `params/` and run `nextflow run main.nf`.

Choose one of these entry points:

- **ONT from FASTQ/barcodes**: use `params/ont.example.yml`.
- **ONT from existing SAMURAI/ichorCNA output**: use `params/ont.from_existing_samurai.example.yml`.
- **Illumina from existing qDNAseq/SAMURAI output and BAMs**: use `params/illumina.example.yml`.

## Main Outputs

The workflow writes numbered stage folders:

```text
01_samurai_ont/
02_bam_refinement/
03_cna_codification/
04_cna_custom_plots/
05_cna_classifier/
06_workflow_summary/
```

The most commonly used files are:

- `03_cna_codification/cna_events.tsv`
- `03_cna_codification/cna_cytogenomic_notation.tsv`
- `04_cna_custom_plots/cna_per_sample_pages.pdf`
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`
- `05_cna_classifier/03_report/pdf_reports/all_sample_CNA_knowledge_reports.pdf`
- `05_cna_classifier/07_pathology/pathology_concordance.tsv`

## Documentation Sections

- Installation: required software and setup.
- YAML configuration: how to edit every example YAML field.
- Inputs: expected ONT, Illumina, and pathology files.
- Running workflows: commands for common runs.
- Tutorial with our ONT and Illumina runs: exact commands, output counts, and figures.
- Outputs: complete output folder explanation.
- Models and pathology: LLM/transformer layers and pathology inference.
- Troubleshooting: common failures and fixes.
