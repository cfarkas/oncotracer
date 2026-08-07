# Execution backends

All backends use the same native stage graph, flat YAML, result directory layout, and audit contract. The backend changes how the scientific programs are supplied; it does not select a different analysis pipeline.

| Backend | Install command | Primary use |
| --- | --- | --- |
| Conda | `oncotracer install --conda` | Native workstation/server execution |
| Docker | `oncotracer install --docker` | Reproducible container execution |
| Singularity/Apptainer | `oncotracer install --singularity` | HPC execution with a reusable SIF |
| Poetry | `./oncotracer install --poetry` | Launcher development; scientific programs remain in five Conda prefixes |

The five Conda groups are `core`, `qdnaseq`, `ichorcna`, `classifier`, and `gistic`.

## Conda

```bash
oncotracer install --conda
oncotracer doctor --backend conda

oncotracer run \
  --backend conda \
  --config "$PWD/project/config/illumina.auto.yml"
```

The installer creates or updates isolated versioned prefixes. qDNAseq and ichorCNA are separated because their R stacks are incompatible. The classifier and GISTIC2 also remain isolated.

`oncotracer doctor --backend conda` uses exact prefix executables and semantic tool/package probes rather than relying on a login shell's `PATH`.

Use an alternate prefix parent when local policy requires it:

```bash
oncotracer install --conda \
  --prefix "/large/storage/oncotracer-v2-envs"
```

## Docker

Stable native image:

```text
ghcr.io/cfarkas/oncotracer:2.0.0
```

Install and run:

```bash
oncotracer install --docker
oncotracer doctor --backend docker

oncotracer run \
  --backend docker \
  --config "$PWD/project/config/illumina.auto.yml"
```

The CLI reads the YAML and mounts the minimal distinct parents required for the configuration file, `lpwgs_root`, inputs, outputs, optional pathology table, and reference assets. Absolute paths are mounted at the same path inside the container, so the YAML does not need a container-specific rewrite.

Docker runs as the invoking `UID:GID` so result ownership remains with the user. Docker daemon access is privileged; follow institutional policy instead of adding undocumented administrator workarounds.

Override an image only for a controlled test:

```bash
oncotracer run \
  --backend docker \
  --image ghcr.io/cfarkas/oncotracer:2.0.0 \
  --config "$PWD/project/config/illumina.auto.yml"
```

For direct Compose inspection:

```bash
ONCOTRACER_PROJECT_DIR="$PWD/project" \
  docker compose run --rm oncotracer --version
```

The ordinary CLI route is preferred for analyses because it derives mounts from the YAML.

## Singularity or Apptainer

```bash
oncotracer install --singularity
oncotracer doctor --backend singularity

oncotracer run \
  --backend singularity \
  --config "$PWD/project/config/illumina.auto.yml"
```

Apptainer is preferred when both executables are present. The installer pulls the same native image into a local SIF and records its path in the OncoTracer configuration. The runner uses `--cleanenv` and explicit binds derived from YAML paths.

Choose an explicit SIF location:

```bash
oncotracer install --singularity \
  --sif "/large/storage/containers/oncotracer-2.0.0.sif"
```

Or override it for one run:

```bash
oncotracer run \
  --backend singularity \
  --sif "/large/storage/containers/oncotracer-2.0.0.sif" \
  --config "$PWD/project/config/illumina.auto.yml"
```

## Poetry development route

```bash
./oncotracer install --poetry
poetry run oncotracer doctor --backend poetry
poetry run oncotracer run \
  --backend poetry \
  --config "$PWD/project/config/illumina.auto.yml"
```

Poetry manages the Python launcher environment. It does not replace BWA, SAMtools, Picard, qDNAseq, HMMcopy, ichorCNA, the classifier stack, or GISTIC2; those are still resolved from the exact managed prefixes.

## Paths and mounts

Use absolute paths in YAML, especially for Docker and HPC systems:

```yaml
lpwgs_root: /data/studies/cohort_a
outdir: /data/studies/cohort_a/results/run_01
illumina_samplesheet: /data/studies/cohort_a/config/illumina.samplesheet.csv
pathology_csv: /data/studies/cohort_a/input/pathology.csv
```

Keep all configured files under storage visible to the backend. Avoid symlinks whose targets are outside mounted roots.

## Image and source identity

```bash
oncotracer provenance --json
oncotracer doctor --backend docker
```

Stable release records contain the exact source commit, deterministic source-tree SHA-256, copied-executable SHA-256, container digest, and successful native-CI and parity workflow identities. Prefer the immutable digest from `release-provenance.json` when recording a formal analysis.

## Backend-independent QuickStarts

```bash
oncotracer quickstart 1 \
  --backend docker \
  --test-root "$PWD/oncotracer-quickstart1-docker"

oncotracer quickstart 2 \
  --backend singularity \
  --test-root "$PWD/oncotracer-quickstart2-singularity"
```

Preparation and YAML content are backend-independent. The same generated config can be moved between supported backends when all absolute paths remain available.
