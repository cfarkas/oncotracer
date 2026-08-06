# OncoTracer v2

OncoTracer v2 converts low-pass whole-genome sequencing FASTQs into copy-number alteration tables, cytogenomic notation, plots, and auditable run records. It supports Illumina paired- or single-end reads and Oxford Nanopore barcode folders.

The v2 engine is native Python, R, and command-line orchestration. **Nextflow is not installed or invoked by the v2 analysis path.** The frozen v1.1 Nextflow release is used only as the independent comparator in release-validation jobs.

> **Documentation version:** this site describes the native OncoTracer v2 command-line application. Historical Nextflow commands belong only to the archived [v1.1 workflow](legacy_v1.md) and must not be used as the normal v2 execution route.

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
oncotracer provenance --json
oncotracer auto --mode illumina --reads-folder reads --sample-table samples.csv
oncotracer run --config config/illumina.auto.yml
```

Every completed native run writes `.oncotracer-native/trace.tsv`, a content-aware stage ledger, a workflow summary, and a checksum manifest. The trace records argument arrays without shell interpolation and is rejected if it contains a Nextflow command.

The stable release also publishes `release-provenance.json`, tying the exact Git commit and deterministic source-tree SHA-256 to the copied executable, container digest, and complete public-data parity audits.

## Research use

OncoTracer is a research workflow. CNA calls require expert review, laboratory validation, and interpretation together with pathology and orthogonal molecular evidence.
