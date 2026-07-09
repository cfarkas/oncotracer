# Containers

Most users only need one flag.

## Docker

```bash
docker pull carlosfarkas/oncotracer:latest
nextflow run main.nf --docker -params-file params/my_config.yml -resume
```

OncoTracer sets the routine Docker user, cache, and project-root mount options internally from `lpwgs_root`.

## Singularity / Apptainer

```bash
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
nextflow run main.nf --singularity -params-file params/my_config.yml -resume
```

OncoTracer binds `lpwgs_root` internally for Singularity/Apptainer.

## Conda Fallback

```bash
conda env create -f environment.yml
conda activate oncotracer
nextflow run main.nf --conda -params-file params/my_config.yml -resume
```

Use Conda only when containers are unavailable.
