# Installation

OncoTracer v2 runs on Linux as one verified global executable. Python 3.10–3.13 is required by the portable zipapp. The selected backend supplies BWA, samtools, Picard, R, qDNAseq, HMMcopy, ichorCNA, the optional classifier, and GISTIC2.

## Requirements

Before installation, provide:

- a 64-bit Linux host;
- Python 3.10–3.13;
- enough storage for FASTQs, hg38, BAMs, environments, temporary files, and results;
- at least 80 GiB of addressable memory for the first uncached Illumina BWA index;
- one backend: Conda, Docker, or Singularity/Apptainer.

The first analysis can take substantially longer because the reference, indexes, packages, and container layers are prepared. Later analyses reuse valid assets and content-matched native stages.

## 1. Install the stable copied executable

Download the complete release asset set with GitHub CLI:

```bash
gh release download v2.0.0 \
  --repo cfarkas/oncotracer \
  --dir oncotracer-v2.0.0
cd oncotracer-v2.0.0
sha256sum -c SHA256SUMS
chmod +x oncotracer
./oncotracer --version
./oncotracer provenance --json
sudo install -m 0755 oncotracer /usr/local/bin/oncotracer
oncotracer --version
oncotracer provenance --json
```

The copied executable is a deterministic Python zipapp containing the versioned OncoTracer source payload. It does not require a Git clone after installation.

Normal commands that need bundled scripts verify and reuse a content-addressed cache at `$XDG_CACHE_HOME/oncotracer/2.0.0/<executable-sha256>/payload` (or `$HOME/.cache` when `XDG_CACHE_HOME` is unset). Each executable digest is isolated, and the exact payload inventory is checked before reuse. `--dry-run` instead uses an automatically removed temporary payload: it does not populate the persistent cache, save installation state, create results or environments, or pull an image/SIF.

Keep these release files together for audit:

```text
oncotracer
SHA256SUMS
release-provenance.json
oncotracer-v2.0.0-parity-audit.tar.gz
```

`release-provenance.json` records the exact release commit, deterministic source archive SHA-256, executable SHA-256, stable container digest, and the successful QuickStart workflow and artifact identities. The values emitted by `oncotracer provenance --json` must agree with that record.

## 2. Prepare exactly one backend

The installation choice is saved under the user's OncoTracer configuration directory. You may still override it per run with `--backend`.

### Conda

Install Miniforge or a compatible Conda distribution, then run:

```bash
oncotracer install --conda
oncotracer doctor --backend conda
```

Five isolated, versioned environments are created:

- core alignment, Picard, refinement, CNA notation, and plotting tools;
- qDNAseq with its pinned R 4.1 stack;
- ichorCNA/HMMcopy with its pinned R 4.4 stack;
- the optional CNA classifier/report stack;
- GISTIC2 for the optional recurrence branch.

Separating the environments avoids incompatible R and compiled-library constraints. The default location is below the user's versioned OncoTracer data directory. Use an explicit shared location when required:

```bash
oncotracer install --conda \
  --prefix /srv/oncotracer/2.0.0/envs

oncotracer doctor --backend conda
```

Use `--force` only when deliberately rebuilding all five prefixes.

### Docker

Install Docker Engine and ensure the current user can access the daemon:

```bash
docker info
oncotracer install --docker
oncotracer doctor --backend docker
```

The installer pulls `ghcr.io/cfarkas/oncotracer:2.0.0`, validates the native tools inside it, and saves the image reference. It does not install Docker, alter daemon settings, or request administrator privileges silently.

Run an analysis with:

```bash
oncotracer run --backend docker \
  --config /absolute/path/project/config/illumina.auto.yml
```

All paths in the YAML should be absolute. OncoTracer derives the required project mounts from the configuration.

### Singularity or Apptainer

Install Apptainer or SingularityCE, then run:

```bash
oncotracer install --singularity
oncotracer doctor --backend singularity
```

Apptainer is preferred when both commands are present. The native v2 image is converted to a reusable SIF below the user's OncoTracer data directory.

Choose a shared SIF path when needed:

```bash
oncotracer install --singularity \
  --sif /srv/oncotracer/images/oncotracer-2.0.0.sif

oncotracer run --backend singularity \
  --config /absolute/path/project/config/illumina.auto.yml
```

### Poetry

Poetry is a source-development route, not the normal binary installation. Clone the repository only for development:

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
./oncotracer install --poetry
poetry run oncotracer --version
poetry run oncotracer doctor --backend poetry
```

`./oncotracer install --poetry` installs the locked Python launcher and the same five Conda scientific environments. Poetry alone does not provide R, BWA, samtools, Picard, HMMcopy, ichorCNA, or GISTIC2.

## 3. Verify provenance and health

Run these checks after installation or after moving environments:

```bash
oncotracer --version
oncotracer provenance --json
oncotracer doctor --backend conda
```

For Docker or Singularity, substitute the selected backend. `doctor` performs semantic probes rather than checking only whether file names exist.

## 4. First analysis checklist

Before a large analysis:

1. Run [QuickStart 1](quick_start.md) through the chosen backend.
2. Confirm that the result summary reports `engine=native` and `nextflow_used=false`.
3. Confirm that `.oncotracer-native/trace.tsv` is present.
4. Place the project and reference cache on storage visible to the backend.
5. Keep the installation and release provenance files with the study record.
