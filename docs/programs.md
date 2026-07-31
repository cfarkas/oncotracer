# Programs used by OncoTracer

OncoTracer connects established alignment, quality-control, CNA-calling, refinement, plotting, and reporting programs. Most users should run the workflow rather than invoke these programs separately.

## Workflow and runtime layer

| Program | Role | Source |
| --- | --- | --- |
| [Nextflow](https://www.nextflow.io/docs/latest/) | Executes tasks, records provenance, and supports `-resume` | Official documentation |
| [Docker](https://docs.docker.com/engine/) | Container runtime for Linux workstations and servers | Uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer) |
| [Apptainer](https://apptainer.org/docs/) / [SingularityCE](https://docs.sylabs.io/guides/latest/user-guide/) | HPC container runtime | Uses `docker://carlosfarkas/oncotracer:latest` |
| [Conda](https://docs.conda.io/) | Fallback environment manager | Official documentation |
| [SAMURAI](https://github.com/dincalcilab/samurai) | Upstream LP-WGS alignment, QC, and CNA workflow used in stage 01 | SAMURAI repository |

Java 17 or newer is required to launch Nextflow.

## Illumina route

| Program | Purpose | Main output |
| --- | --- | --- |
| [BWA-MEM](https://github.com/lh3/bwa) | Align single-end or paired short reads to hg38 | `01_samurai_illumina/alignment/*.bam` |
| [SAMtools](https://www.htslib.org/) | Sort, index, and inspect BAM/reference files | BAM/BAI and reference indexes |
| [FastQC](https://www.bioinformatics.babraham.ac.uk/projects/fastqc/) | Per-FASTQ quality control | `01_samurai_illumina/fastqc/` |
| [MultiQC](https://multiqc.info/) | Aggregate sample QC | `01_samurai_illumina/multiqc/` |
| [Picard](https://broadinstitute.github.io/picard/) | Alignment and whole-genome metrics | `01_samurai_illumina/picard/` |
| [qDNAseq](https://bioconductor.org/packages/QDNAseq/) | Read-depth binning and segmentation; optional local normal correction | `01_samurai_illumina/qdnaseq/` or `qdnaseq_local_pon/` |

The standard Illumina configuration uses `solid_biopsy`, qDNAseq, and 100 kb bins.

## ONT route

| Program | Purpose | Main output |
| --- | --- | --- |
| [minimap2](https://github.com/lh3/minimap2) | Align Oxford Nanopore reads to hg38 | `01_samurai_ont/bam/*.bam` |
| SAMtools and pigz | Sort/index BAMs and validate/merge compressed FASTQs | `bam/`, `merged_fastq/`, and `logs/` |
| [ichorCNA](https://github.com/broadinstitute/ichorCNA) | Read-depth CNA and tumor-fraction-oriented analysis | `01_samurai_ont/results/ichorcna/` |
| Picard | Alignment and WGS metrics | `01_samurai_ont/results/picard/` |

The standard ONT configuration uses `liquid_biopsy`, ichorCNA, and 500 kb bins. Review the used, skipped, and warning logs before interpreting results.

## OncoTracer stages after SAMURAI

1. `02_bam_refinement`: evaluates and refines broad CNA boundaries.
2. `03_cna_codification`: creates CNA event tables and cytogenomic notation.
3. `04_cna_custom_plots`: creates per-sample and cohort plots.
4. `05_cna_classifier`: optionally produces CNA-pattern, literature, report, and pathology-comparison outputs.

These stages use Python and R packages including pandas, NumPy, SciPy, pysam, Matplotlib, scikit-learn, ReportLab, openpyxl, and qDNAseq.

## Host program checks

```bash
# Confirm Java, Nextflow, and Python.
java -version
nextflow -version
python3 --version

# Confirm the host-side alignment and compression helpers.
samtools --version | sed -n '1p'
minimap2 --version
pigz --version
```

For Docker:

```bash
# Confirm Docker is installed.
command -v docker

# Let Nextflow prepare and test the maintained Docker image.
nextflow run main.nf --install --docker \
  --lpwgs_root /absolute/path/to/oncotracer-data
```

For Singularity or Apptainer:

```bash
# Confirm the HPC launcher and test docker://carlosfarkas/oncotracer:latest.
command -v singularity
command -v apptainer
nextflow run main.nf --install --singularity \
  --lpwgs_root /absolute/path/to/oncotracer-data
```

Ask the system administrator to install missing host programs on managed systems.

## Record versions from a completed run

```bash
# List the Illumina or ONT pipeline provenance files.
find /absolute/path/to/outdir/01_samurai_illumina/pipeline_info \
  -maxdepth 1 -type f | sort
find /absolute/path/to/outdir/01_samurai_ont/results/pipeline_info \
  -maxdepth 1 -type f | sort

# Record the OncoTracer commit, Nextflow version, and selected image.
git rev-parse HEAD
nextflow -version
cat .oncotracer/install/install_manifest.txt
```

The installer and Illumina wrapper pin SAMURAI `v1.4.0`. Preserve the nested `pipeline_info`, generated YAML and samplesheet, OncoTracer commit, and installation manifest with formal study outputs.

## Scientific responsibility

Each program has assumptions about genome build, bin size, coverage, tumor purity, and sample type. Reproducible software execution does not make an unsuitable method scientifically valid. Predefine parameters, retain QC and provenance, and validate important findings with an appropriate orthogonal assay.
