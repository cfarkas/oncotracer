# OncoTracer

![OncoTracer sequencing-to-CNA workflow](assets/oncotracer-hero.png)

**Reproducible LP-WGS copy-number analysis from Illumina and Oxford Nanopore FASTQ data.**

OncoTracer runs SAMURAI/qDNAseq or SAMURAI/ichorCNA, refines CNA boundaries, and creates event tables, cytogenomic notation, plots, reports, and optional pathology concordance outputs.

Start with the [Quick Start](quick_start.md). It includes complete public Illumina and ONT runs, the generated YAML files, and a comment explaining every command and YAML setting.

| Input | CNA route | Tutorial |
| --- | --- | --- |
| Illumina paired-end FASTQ | SAMURAI + qDNAseq | [Complete Illumina test](quick_start.md#complete-illumina-public-test) |
| ONT barcode FASTQ | SAMURAI + ichorCNA | [Complete ONT test](quick_start.md#complete-ont-public-test) |
| Illumina plus pathology CSV | CNA classifier and concordance reports | [Models & Pathology](models_pathology.md) |

!!! warning "Research use"
    OncoTracer is not a standalone diagnostic system. Results require expert interpretation, laboratory validation, and integration with pathology and orthogonal molecular tests.
