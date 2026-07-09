# Installation

OncoTracer needs Nextflow and one execution runtime.

## 1. Install Nextflow

```bash
curl -s https://get.nextflow.io | bash
mkdir -p "$HOME/bin"
mv nextflow "$HOME/bin/"
export PATH="$HOME/bin:$PATH"
nextflow -version
```

## 2. Choose Runtime

### Docker, Recommended

```bash
docker pull carlosfarkas/oncotracer:latest
docker run --rm carlosfarkas/oncotracer:latest --help
```

Run OncoTracer with:

```bash
nextflow run main.nf --docker -params-file params/illumina.example.yml -resume
```

### Singularity / Apptainer, HPC

```bash
apptainer pull oncotracer_latest.sif docker://carlosfarkas/oncotracer:latest
nextflow run main.nf --singularity -params-file params/illumina.example.yml -resume
```

`singularity` can be used instead of `apptainer` on systems that still use the old command name.

### Conda, Fallback

```bash
conda env create -f environment.yml
conda activate oncotracer
nextflow run main.nf --conda -params-file params/illumina.example.yml -resume
```

## Documentation

The public documentation is online: https://cfarkas.github.io/oncotracer/

You do not need to build the documentation to run OncoTracer. The MkDocs files are included only for maintainers who update the website.
