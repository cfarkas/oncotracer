# Installation

OncoTracer v2 runs on Linux as one global executable. Python 3.10–3.13 is required for the portable zipapp release asset. The scientific tools are supplied by one selected backend.

## 1. Install the release executable

Download the `oncotracer` file attached to the stable GitHub release, then:

```bash
chmod +x oncotracer
./oncotracer --version
sudo install -m 0755 oncotracer /usr/local/bin/oncotracer
oncotracer --version
```

The file is a self-extracting Python zipapp. Its versioned scientific payload is extracted once below the user cache. It does not require a Git clone after installation.

## 2. Select one backend

### Conda

Install Miniforge or another compatible Conda distribution, then:

```bash
oncotracer install --conda
oncotracer doctor --backend conda
```

Three isolated, versioned environments are created: core tools, qDNAseq/R 4.1, and ichorCNA/HMMcopy/R 4.4. This separation avoids incompatible R dependency constraints.

### Docker

```bash
oncotracer install --docker
oncotracer doctor --backend docker
```

The command validates access to the Docker daemon and pulls the immutable v2 image from GitHub Container Registry. It does not install Docker or silently request administrator privileges.

### Singularity or Apptainer

```bash
oncotracer install --singularity
oncotracer doctor --backend singularity
```

Apptainer is preferred when both commands are present. The pinned container is stored as a local SIF and reused.

### Poetry

Use this route in a source checkout when developing the launcher:

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
oncotracer install --poetry
poetry run oncotracer --version
```

Poetry does not replace the scientific backend; it is a development installation of the native Python launcher.

## 3. Storage and memory

The first Illumina analysis downloads the UCSC hg38 FASTA and creates a BWA index. Keep at least 80 GiB of addressable memory for the initial index and adequate storage for the reference, BAMs, native stage cache, and outputs. Later runs reuse valid reference indexes and content-matched completed stages.
