# Programs Used by OncoTracer

OncoTracer connects established alignment, quality-control, CNA-calling, refinement, plotting, and reporting tools. Users normally run the complete Nextflow workflow rather than invoking these programs separately.

## Workflow and runtime

| Program | Role |
| --- | --- |
| [Nextflow](https://www.nextflow.io/docs/latest/) | Executes tasks, records provenance, and enables `-resume` |
| [Docker](https://docs.docker.com/engine/) | Container runtime for Linux workstations and servers |
| [Apptainer](https://apptainer.org/docs/) / [SingularityCE](https://docs.sylabs.io/guides/latest/user-guide/) | Container runtime for HPC |
| [Conda](https://docs.conda.io/) | Fallback environment manager and optional classifier runtime |
| [SAMURAI](https://github.com/dincalcilab/samurai) | Upstream LP-WGS alignment, QC, and CNA workflow |

The maintained OncoTracer Docker image is [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).

## Illumina route

| Program | Role | Main output |
| --- | --- | --- |
| BWA-MEM | Align short reads to hg38 | `01_samurai_illumina/alignment/*.bam` |
| SAMtools | Sort, index, and inspect BAM/reference files | BAM, BAI, and reference indices |
| FastQC | Per-FASTQ quality control | `01_samurai_illumina/fastqc/` |
| MultiQC | Combined quality-control report | `01_samurai_illumina/multiqc/` |
| Picard | Alignment and whole-genome metrics | `01_samurai_illumina/picard/` |
| qDNAseq | Read-depth binning and segmentation | `01_samurai_illumina/qdnaseq/` or `qdnaseq_local_pon/` |

The standard Illumina route uses `solid_biopsy`, qDNAseq, and 100 kb bins.

## ONT route

| Program | Role | Main output |
| --- | --- | --- |
| minimap2 | Align Oxford Nanopore reads to hg38 | `01_samurai_ont/bam/*.bam` |
| SAMtools and pigz | Process BAMs and compressed FASTQs | `bam/`, `merged_fastq/`, and `logs/` |
| ichorCNA | Read-depth CNA analysis | `01_samurai_ont/results/ichorcna/` |
| Picard | Alignment and WGS metrics | `01_samurai_ont/results/picard/` |

The standard ONT route uses `liquid_biopsy`, ichorCNA, and 500 kb bins.

## OncoTracer-specific stages

After SAMURAI, OncoTracer performs:

1. BAM-supported boundary refinement;
2. conversion to CNA event and cytogenomic tables;
3. per-sample and cohort visualizations;
4. optional CNA-pattern, report, literature, and pathology comparison.

## Check host programs

```bash
# Check Java and Nextflow.
java -version
nextflow -version

# Check Python and the host-side sequence tools.
python3 --version
samtools --version | sed -n '1p'
bwa 2>&1 | head -2
minimap2 --version
pigz --version

# Check Docker on a workstation or server.
command -v docker

# Check Singularity or Apptainer on HPC.
command -v singularity
command -v apptainer
```

Prepare the selected runtime through Nextflow:

```bash
# Prepare Docker, the OncoTracer image, and the SAMURAI cache.
nextflow run main.nf --install --docker \
  --lpwgs_root /path/to/oncotracer_project
```

```bash
# Prepare Singularity or Apptainer on HPC.
nextflow run main.nf --install --singularity \
  --lpwgs_root /path/to/oncotracer_project
```

## Record versions from a completed run

```bash
# List Illumina stage-01 provenance files.
find /path/to/outdir/01_samurai_illumina/pipeline_info \
  -maxdepth 1 -type f -print | sort

# List ONT stage-01 provenance files.
find /path/to/outdir/01_samurai_ont/results/pipeline_info \
  -maxdepth 1 -type f -print | sort

# Record the OncoTracer commit.
git rev-parse HEAD

# Record the Nextflow version.
nextflow -version

# Read the image and runtime identity recorded by --install.
cat .oncotracer/install/install_manifest.txt
```

The current installers and Illumina wrappers pin SAMURAI `v1.4.0`. Preserve nested `pipeline_info`, the OncoTracer commit, image identity, generated YAML, and workflow summary with any result.

Containerization improves software reproducibility but does not make an unsuitable method scientifically valid. Predefine parameters, retain QC and provenance, and confirm important findings with an appropriate orthogonal assay.
