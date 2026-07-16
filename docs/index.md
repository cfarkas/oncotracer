# OncoTracer

![OncoTracer sequencing-to-CNA workflow](assets/oncotracer-hero.png)

OncoTracer is a reproducible Nextflow research workflow for **low-pass whole-genome sequencing (LP-WGS)**. It turns Illumina single-end or paired-end and Oxford Nanopore Technologies (ONT) FASTQ reads into **copy-number alteration (CNA)** tables, plots, and reports. A CNA is a genomic region with a gain or loss of DNA.

## Choose where to start

| Your goal | Start here | What you will do |
| --- | --- | --- |
| Check that OncoTracer works | [QuickStart Example 1](quick_start.md) | Run one public Illumina sample and one public ONT sample. |
| Analyze your own reads (recommended default) | [Automatic setup](auto_params.md) | Point to a FASTQ folder and let OncoTracer write the configuration. |
| Run a realistic public cohort | [QuickStart Example 2](public_cohort.md) | Download and analyze three paired HCC1143 samples (six FASTQs). |
| Reproduce the complete patient-cohort workflow | [Full Tutorial](full_tutorial.md) | Process all 12 public PRJNA754199 libraries and review CNA reports. |
| Configure unusual inputs manually (second option) | [Manual YAML editing](configuration/yaml_basics.md) | Understand paths and edit a YAML example safely. |
| Add pathology data | [Pathology and classifier](configuration/pathology.md) | Match a pathology CSV to Illumina sample names. |

For your own standard Illumina or ONT layout, start with automatic setup. Edit a YAML manually only when automatic detection does not fit the study or you need advanced settings.

!!! warning "First real analysis"
    The first analysis downloads the hg38 reference (about **3.16 GB**). BWA indexing is single-core and commonly takes **30–60 minutes**. Later runs reuse the prepared reference. The small public verification also downloads about **225 MB of reads**; the optional three-sample cohort downloads **1.08 GiB**.

## What the workflow does

```text
FASTQ reads
    |
    +-- Illumina -> SAMURAI + qDNAseq
    |
    +-- ONT ------> SAMURAI + ichorCNA
                         |
                         v
             CNA boundary refinement
                         |
                         v
               tables, plots, reports
```

| Input | Main CNA route | Beginner guide |
| --- | --- | --- |
| Illumina single-end or paired-end FASTQ | SAMURAI + qDNAseq | [Illumina setup](configuration/illumina.md) |
| ONT barcode FASTQ folders | SAMURAI + ichorCNA | [Automatic ONT setup](auto_params.md#ont-step-by-step) |
| Illumina plus pathology CSV | Optional classifier and concordance reports | [Pathology tutorial](configuration/pathology.md) |

## The basic run pattern

Every analysis follows the same sequence:

1. Install and check Java, Git, Nextflow, and a container runtime.
2. Clone the repository and enter the `oncotracer` directory.
3. Generate a YAML automatically (recommended), or edit one manually as the second option.
4. Perform an optional `-stub-run` wiring check.
5. Run the real workflow with `-resume`.
6. Open `06_workflow_summary/workflow_summary.txt` and inspect the plots.

`-stub-run` creates placeholder task outputs to check workflow connections. It does **not** fully validate real FASTQ contents or prove that the analysis will finish. `-resume` tells Nextflow to reuse unchanged completed tasks, which is useful after interruption and on repeat commands.

!!! warning "Research use"
    OncoTracer is not a standalone diagnostic system. Results require expert interpretation, laboratory validation, and integration with pathology and orthogonal molecular tests.
