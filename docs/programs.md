# Programs used by OncoTracer

OncoTracer is an orchestrator: it connects established alignment, quality-control, CNA-calling, refinement, plotting, and reporting tools. Most users should run the workflow, not invoke these programs independently.

## Workflow and runtime layer

| Program | Role | Where to learn more |
| --- | --- | --- |
| [Nextflow](https://www.nextflow.io/docs/latest/) | Executes tasks, tracks provenance, and enables `-resume` | Official Nextflow documentation |
| [Docker](https://docs.docker.com/engine/) | Preferred portable software runtime | Docker Engine documentation |
| [Apptainer](https://apptainer.org/docs/) / [SingularityCE](https://docs.sylabs.io/guides/latest/user-guide/) | HPC container runtimes | Site documentation takes precedence |
| [Conda](https://docs.conda.io/) | Fallback environment manager and optional classifier profile | Conda documentation |
| [SAMURAI](https://github.com/dincalcilab/samurai) | Upstream LP-WGS alignment/QC/CNA workflow used in stage 01 | SAMURAI repository |

Java is required to launch Nextflow. It is infrastructure, not a CNA analysis step.

## Illumina route

| Program/component | What it does | Result to inspect |
| --- | --- | --- |
| [BWA-MEM](https://github.com/lh3/bwa) | Aligns paired short reads to hg38 | `01_samurai_illumina/alignment/*.bam` |
| [SAMtools](https://www.htslib.org/) | Sorts, indexes, and inspects BAM/reference files | BAM/BAI and reference indices |
| [FastQC](https://www.bioinformatics.babraham.ac.uk/projects/fastqc/) | Per-FASTQ quality control | `01_samurai_illumina/fastqc/` |
| [MultiQC](https://multiqc.info/) | Aggregates sample QC | `01_samurai_illumina/multiqc/` |
| [Picard](https://broadinstitute.github.io/picard/) | Alignment and whole-genome metrics | `01_samurai_illumina/picard/` |
| [qDNAseq](https://bioconductor.org/packages/QDNAseq/) | Read-depth binning/segmentation for Illumina LP-WGS | `01_samurai_illumina/qdnaseq/` |

The standard Illumina YAML selects `solid_biopsy`, `qdnaseq`, and 100 kb bins. Change these only for a scientifically justified analysis plan.

## ONT route

| Program/component | What it does | Result to inspect |
| --- | --- | --- |
| [minimap2](https://github.com/lh3/minimap2) | Aligns Oxford Nanopore reads to hg38 | `01_samurai_ont/bam/*.bam` |
| SAMtools/pigz | Sorts/indexes BAM and validates/merges compressed FASTQs | `bam/`, `merged_fastq/`, and `logs/` |
| [ichorCNA](https://github.com/broadinstitute/ichorCNA) | Read-depth CNA and tumor-fraction-oriented analysis | `01_samurai_ont/results/ichorcna/` |
| Picard | Alignment and WGS metrics | `01_samurai_ont/results/picard/` |

The standard ONT YAML selects `liquid_biopsy`, `ichorcna`, and 500 kb bins. Check `logs/used_fastq.tsv` and every skipped/warning log before interpreting the caller output.

## OncoTracer-specific stages

After SAMURAI, bundled scripts perform:

1. BAM-supported boundary refinement (`02_bam_refinement`);
2. conversion to event and cytogenomic tables (`03_cna_codification`);
3. per-sample and cohort visualizations (`04_cna_custom_plots`);
4. optional CNA-pattern, literature, report, and pathology-concordance analysis (`05_cna_classifier`).

These stages use Python/R libraries including pandas, NumPy, SciPy, pysam, Matplotlib, scikit-learn, ReportLab, and openpyxl. The optional classifier can also use Transformers/PyTorch models when explicitly enabled.

See [Output files](outputs.md) for which files are primary and which are intermediate/presentation outputs.

## What must be installed on the host?

The exact checks depend on the route. Begin with:

```bash
java -version
nextflow -version
python3 --version
```

For Docker:

```bash
docker --version
docker info >/dev/null && echo 'Docker engine: OK'
```

The current stage-01 launchers also prepare references/alignment outside some nested tasks. Check:

```bash
samtools --version | sed -n '1p'
minimap2 --version                 # required for ONT
pigz --version                     # used for ONT compressed FASTQs
```

These are stage-01 launcher checks in the current workflow. Containers package the analysis environments, but they do not replace every host-side launcher action. Ask an administrator to install missing host tools on managed systems.

## Record versions from a completed run

Do not infer tool versions from a website screenshot. Preserve generated provenance:

```bash
find /absolute/path/to/outdir/01_samurai_illumina/pipeline_info -maxdepth 1 -type f | sort
find /absolute/path/to/outdir/01_samurai_ont/results/pipeline_info -maxdepth 1 -type f | sort
```

Depending on the route, `pipeline_info/` contains parameter JSON, software-version YAML, execution trace, timeline, report, and DAG. Also record:

```bash
git rev-parse HEAD
docker image inspect carlosfarkas/oncotracer:latest --format '{{index .RepoDigests 0}}'
nextflow -version
```

The wrappers currently request the latest SAMURAI revision, so recording the nested `pipeline_info` and cache revision is essential. For a formal study, freeze and validate all workflow/tool revisions before production analysis.

## Scientific responsibility

Each program has assumptions about genome build, bin size, coverage, tumor purity, and sample type. Containerization makes software execution reproducible; it does not make an unsuitable method scientifically valid. Predefine parameters, retain QC/provenance, and validate important findings with an appropriate orthogonal assay.
