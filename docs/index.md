# OncoTracer v2

OncoTracer v2 converts low-pass whole-genome sequencing FASTQs into copy-number alteration tables, cytogenomic notation, plots, and auditable run records. It supports Illumina paired- or single-end reads and Oxford Nanopore barcode folders.

The v2 engine is native Python, R, and command-line orchestration. **Nextflow is not installed or invoked by the v2 analysis path.** The frozen v1.1 Nextflow release is used only as the independent comparator in release-validation jobs.

```text
Illumina FASTQ -> BWA/Picard -> qDNAseq
ONT FASTQ      -> minimap2   -> HMMcopy/ichorCNA
                                  |
                                  v
BAM-supported boundary refinement -> CNA notation -> PDF reports
```

## Start here

1. [Install the global executable and one backend](installation.md).
2. Run [QuickStart 1](quick_start.md), covering one Illumina and one ONT library.
3. Use [Automatic Setup](auto_params.md) for your own FASTQs.
4. Run [QuickStart 2](public_cohort.md), covering three public HCC1143 libraries.
5. Review [release parity and audit records](parity_release.md).

## Core commands

```bash
oncotracer install --conda
oncotracer doctor
oncotracer auto --mode illumina --reads-folder reads --sample-table samples.csv
oncotracer run --config config/illumina.auto.yml
```

Every completed native run writes `.oncotracer-native/trace.tsv`, a content-aware stage ledger, a workflow summary, and a checksum manifest. The trace records argument arrays without shell interpolation and is rejected if it contains a Nextflow command.

## Research use

OncoTracer is a research workflow. CNA calls require expert review, laboratory validation, and interpretation together with pathology and orthogonal molecular evidence.
