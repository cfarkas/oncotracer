# OncoTracer

![OncoTracer sequencing-to-CNA workflow](assets/oncotracer-hero.png)

OncoTracer is a reproducible Nextflow research workflow for **low-pass whole-genome sequencing (LP-WGS)**. It converts Illumina or Oxford Nanopore Technologies (ONT) FASTQ reads into **copy-number alteration (CNA)** tables, plots, and reports.

Choose one of four supported ways to launch an analysis:

1. **Docker:** `nextflow run ... --docker`.
2. **Singularity or Apptainer:** `nextflow run ... --singularity`.
3. **Poetry launcher:** `poetry run oncotracer --backend docker ...`.
4. **Conda:** `nextflow run ... --conda`; Nextflow creates and reuses the native environments automatically.

Every QuickStart, the Full Tutorial, Automatic Setup, and the mock tumor/normal example provide explicit commands for all four methods.

## Clone OncoTracer

```bash
# Clone OncoTracer into a given directory.

git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
```

## Choose where to start

| Your goal | Start here | Data used |
| --- | --- | --- |
| Verify the installation | [QuickStart Example 1](quick_start.md) | One public Illumina and one public ONT sample downloaded by the tutorial |
| Analyze your own FASTQs | [Automatic Setup](auto_params.md) | Your Illumina files or ONT barcode folders |
| Run a larger public example | [QuickStart Example 2](public_cohort.md) | Three public HCC1143 libraries, six FASTQs |
| Process the complete public archive example | [Full Tutorial](full_tutorial.md) | Twelve public PRJNA754199 libraries |
| See how normal controls are used | [Other Example Run: six tumors and four controls](six_tumor_four_control.md) | Mock six-tumor/four-normal example illustrating a local qDNAseq panel of normals |
| Configure unusual paths or options | [Manual YAML editing](configuration/yaml_basics.md) | Your own data |
| Add pathology data | [Pathology and classifier](configuration/pathology.md) | Your own matched sequencing and pathology tables |

For standard Illumina or ONT layouts, start with Automatic Setup. It validates the input names and writes the YAML used by the analysis command.

## Normal controls

For Illumina, zero `NORMAL` rows run without a local panel of normals, one normal is rejected, and two or more normals enable the local qDNAseq reference. Corrected CNA outputs contain tumor samples; controls remain reference and quality-control inputs. See [Automatic Setup](auto_params.md#illumina-step-by-step), [Illumina setup](configuration/illumina.md), and [output files](outputs.md#illumina-local-panel-of-normals).

## Estimated time for the first analysis

The first uncached analysis downloads the hg38 reference (about **3.16 GB**) and creates a BWA index. This commonly takes **30–60 minutes**, and the pinned BWA task requests 72 GB, so provide at least 80 GiB of addressable RAM. Later runs reuse a valid index.

QuickStart Example 1 additionally downloads about **225 MB** of reads. QuickStart Example 2 downloads about **1.08 GiB**. A first Conda run also needs time and disk space to create its software environments; later runs reuse them.

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

| Input | Main CNA route | Setup guide |
| --- | --- | --- |
| Illumina single-end or paired-end FASTQ | SAMURAI + qDNAseq | [Automatic Illumina setup](auto_params.md#illumina-step-by-step) |
| ONT barcode FASTQ folders | SAMURAI + ichorCNA | [Automatic ONT setup](auto_params.md#ont-step-by-step) |
| Illumina plus pathology CSV | Optional classifier and concordance reports | [Pathology tutorial](configuration/pathology.md) |

## Basic run pattern

1. Install Java, Nextflow, and Conda, Docker, or Singularity/Apptainer.
2. Clone the repository.
3. Generate a YAML with `--auto_params`, or edit one manually for an unusual layout.
4. Run the YAML with Docker, Singularity/Apptainer, the Poetry launcher, or Conda and keep `-resume` enabled.
5. Open `06_workflow_summary/workflow_summary.txt` and inspect the plots and tables.

With `--conda`, Nextflow creates the required environments automatically and reuses them on later runs. `-resume` reuses unchanged completed tasks after an interruption or on a repeated command.

## Research use

OncoTracer is not a standalone diagnostic system. Results require expert interpretation, laboratory validation, and integration with pathology and orthogonal molecular tests.
