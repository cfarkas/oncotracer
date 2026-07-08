# Installation

## Required Software

OncoTracer can be run with Docker, Singularity/Apptainer, or native Conda. Docker is the preferred portable route. The bundled subworkflows still use Conda environments for Python tooling, BAM refinement, and GISTIC2.

For Docker mode, install:

- Docker

For native Conda mode, install these first:

- Java, required by Nextflow
- Nextflow
- Conda or Mamba
- Bash shell
- Standard Unix tools such as `find`, `sort`, `gzip`, and `tar`
- `samtools` and `minimap2` when running ONT-from-FASTQ without Docker

Check your installation:

```bash
java -version
nextflow -version
conda --version
```

## Clone The Repository

```bash
git clone <REPOSITORY_URL> OncoTracer
cd OncoTracer
```


## Docker Installation

Docker is the recommended path for new users.

```bash
docker --version
docker pull carlosfarkas/oncotracer:latest
docker run --rm carlosfarkas/oncotracer:latest --help
```

Run with the Nextflow Docker flag:

```bash
nextflow run main.nf --docker \
  -params-file params/illumina.example.yml \
  -resume
```

Maintainers can publish Docker Hub release tags with:

```bash
docker login -u carlosfarkas
docker tag carlosfarkas/oncotracer:latest carlosfarkas/oncotracer:2026-07-06
docker tag carlosfarkas/oncotracer:latest carlosfarkas/oncotracer:v0.1.0
docker push carlosfarkas/oncotracer:latest
docker push carlosfarkas/oncotracer:2026-07-06
docker push carlosfarkas/oncotracer:v0.1.0
```

## Singularity / Apptainer Installation

Use this on HPC systems where Docker is not available.

```bash
apptainer --version
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
```

Run with Nextflow:

```bash
nextflow run main.nf --singularity \
  -params-file params/illumina.example.yml \
  -resume
```

You can also let Nextflow pull `docker://carlosfarkas/oncotracer:latest` through the `singularity` profile.

## Native Conda Fallback

Use Conda only when Docker and Singularity/Apptainer are unavailable.

```bash
conda env create -f environment.yml
conda activate oncotracer
nextflow run main.nf --conda -params-file params/illumina.example.yml -resume
```

## Conda Environment Behavior

The top-level workflow calls bundled scripts. The CNA classifier subworkflow uses Conda environments under:

```text
bin/cna_classifier_nf/envs/cna_classifier.yml
bin/cna_classifier_nf/envs/gistic2.yml
```

`cna_classifier.yml` includes Python packages such as pandas, numpy, scipy, scikit-learn, matplotlib, requests, reportlab, pypdf, transformers, torch, safetensors, and huggingface_hub.

`gistic2.yml` installs GISTIC2 plus download utilities.

The BAM refinement wrapper manages its own environment/check logic. If you only want to check the environment, use the manual script mode or run a small Nextflow test first.

## Recommended First Test

Run a workflow with the classifier disabled first. This validates input paths, BAM refinement, CNA codification, and custom plots without running literature/model/report stages.

```bash
nextflow run main.nf --conda \
  -params-file params/illumina.example.yml \
  --run_cna_classifier false \
  -resume
```

Then enable reports/classifier:

```bash
nextflow run main.nf --conda \
  -params-file params/illumina.example.yml \
  --run_cna_classifier true \
  -resume
```

## Documentation Preview

The documentation is MkDocs-ready.

```bash
pip install -r docs/requirements.txt
mkdocs serve
```

Then open the local URL printed by MkDocs.
