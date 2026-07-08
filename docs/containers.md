# Docker, Singularity, and Conda Execution

OncoTracer supports Docker, Singularity/Apptainer, and native Conda. For new users, choose one mode and use the matching commands below.

## Which Mode Should I Use?

| Situation | Use this mode | Why |
|---|---|---|
| You have Docker on a laptop, workstation, or server | Docker | Simplest and most reproducible option. |
| You already run Nextflow on the host and want each process isolated | Nextflow Docker flag | Host Nextflow controls the workflow, Docker runs each task. |
| You are on an HPC cluster where Docker is not allowed | Singularity/Apptainer | HPC-friendly way to run the same Docker Hub image. |
| You cannot use containers | Conda | Works, but requires more local dependency management. |

Docker is the preferred portable route. Docker Hub is the primary image source:

```text
carlosfarkas/oncotracer:latest
carlosfarkas/oncotracer:2026-07-06
carlosfarkas/oncotracer:v0.1.0
```

## Before You Run

Edit one YAML file in `params/` first. Every absolute input, output, BAM, FASTQ, segment, or pathology path in that YAML must be visible inside the execution environment.

For most users, the Docker or Singularity flag is enough. Advanced bind options are only needed when your cluster or workstation hides input paths from containers.

## Docker: Recommended For Most Users

### 1. Check Docker

```bash
docker --version
```

### 2. Pull and test the image

```bash
docker pull carlosfarkas/oncotracer:latest
docker run --rm carlosfarkas/oncotracer:latest --help
```

### 3. Run with host Nextflow and Docker

Use this when you run `nextflow` on the host and want workflow tasks to run inside Docker:

```bash
nextflow run main.nf --docker \
  -params-file params/illumina.example.yml \
  -resume
```

For most users this is all that is needed: use `--docker` and provide your YAML file. OncoTracer sets the needed Docker user, cache, and project-root mount options internally. Advanced users can override `docker_user` or `docker_run_options` if their site requires different settings.

### 4. Optional: run Nextflow from inside the container

This is useful if you do not want to install Nextflow on the host:

```bash
docker run --rm -it \
  -v /your/data:/your/data \
  carlosfarkas/oncotracer:latest \
  -profile local \
  -params-file /your/data/params/illumina.example.yml \
  -resume
```

The `-params-file` path must be the path as seen inside the container.

### 5. Optional: open a shell inside the image

```bash
docker run --rm -it \
  -v /your/data:/your/data \
  --entrypoint oncoTracer-shell \
  carlosfarkas/oncotracer:latest
```

## Singularity / Apptainer: Recommended For HPC

Use this mode when your cluster does not allow Docker.

### 1. Check the runtime

```bash
apptainer --version
```

If your system uses Singularity instead:

```bash
singularity --version
```

### 2. Pull the Docker Hub image as a SIF file

```bash
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
```

### 3. Run with the Docker Hub image directly

```bash
nextflow run main.nf \
  --singularity \
  -params-file params/illumina.example.yml \
  -resume
```

### 4. Or run with the pre-pulled SIF file

```bash
nextflow run main.nf \
  --singularity \
  --singularity_image /path/to/oncotracer_latest.sif \
  -params-file params/illumina.example.yml \
  -resume
```

For most users this is all that is needed: use `--singularity` and provide your YAML file. OncoTracer sets a default bind for `lpwgs_root` internally. Advanced users can override `singularity_run_options` if their HPC site requires different settings.

## Native Conda: Fallback Mode

Use this only when Docker and Singularity/Apptainer are unavailable.

### 1. Install base tools

You need Java, Nextflow, Conda or Mamba, Bash, and standard Unix tools. For ONT-from-FASTQ runs without containers, also install `samtools` and `minimap2`.

```bash
java -version
nextflow -version
conda --version
```

### 2. Create and activate the broad environment

```bash
conda env create -f environment.yml
conda activate oncotracer
```

### 3. Run with the Conda profile

```bash
nextflow run main.nf \
  --conda \
  -params-file params/illumina.example.yml \
  -resume
```

The BAM refinement wrapper can create or repair its own environment named `bam_cnv_boundary_refine_env`. The classifier subworkflow uses environment YAML files under `bin/cna_classifier_nf/envs/`.

## Maintainer: Build, Tag, and Push Docker Images

Most users do not need this section. Use it only when publishing the Docker image.

Create the Docker Hub repository first at `https://hub.docker.com/repositories/carlosfarkas`. The repository name should be `oncotracer`.

```bash
docker login -u carlosfarkas
docker build -t carlosfarkas/oncotracer:latest .
docker tag carlosfarkas/oncotracer:latest carlosfarkas/oncotracer:2026-07-06
docker tag carlosfarkas/oncotracer:latest carlosfarkas/oncotracer:v0.1.0
docker push carlosfarkas/oncotracer:latest
docker push carlosfarkas/oncotracer:2026-07-06
docker push carlosfarkas/oncotracer:v0.1.0
```

Verify the published image:

```bash
docker pull carlosfarkas/oncotracer:latest
docker run --rm carlosfarkas/oncotracer:latest --help
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
```

## Docker Image Contents

The Docker image includes:

- OncoTracer repository under `/opt/OncoTracer`
- Nextflow
- Java 17
- Conda/Miniforge
- Python 3.11
- samtools
- minimap2
- pandas, numpy, scipy, pysam, openpyxl
- matplotlib, scikit-learn, jinja2, requests
- reportlab, pypdf
- bundled OncoTracer scripts and classifier environments
- optional transformer/torch dependencies through `bin/cna_classifier_nf/envs/cna_classifier.yml` when classifier stages run
