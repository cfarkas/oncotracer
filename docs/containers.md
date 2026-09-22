# Execution backends

All backends use the same native stage graph, flat YAML, result directory layout, and audit records. The backend changes how the scientific programs are supplied; it does not select a different analysis pipeline.

Use the YAML you already created with [setup](setup.md) or [batch setup](auto_params.md).
Choose **one** backend below. The examples assume batch setup's
`project/config/illumina.auto.yml`; substitute `my-study/config/run.yml` if you
used guided setup. Install once, run `check`, then use the matching run command.
Every `run` example works from a terminal without a browser. For unattended setup
and SSH access, see [terminal and headless servers](headless.md).

| Backend | Install command | Primary use |
| --- | --- | --- |
| Conda | `oncotracer install --conda` | Native workstation/server execution |
| Docker | `oncotracer install --docker` | Reproducible container execution |
| Singularity/Apptainer | `oncotracer install --singularity` | HPC execution with a reusable SIF |
| Poetry | `./oncotracer install --poetry` | Launcher development; scientific programs remain in five Conda prefixes |

The five core Conda groups are `core`, `qdnaseq`, `ichorcna`, `classifier`, and `gistic`. Variant-enabled images also include isolated `variants` and `ffperase` environments.

## Conda

```bash
oncotracer install --conda
oncotracer doctor --backend conda
oncotracer check --config "$PWD/project/config/illumina.auto.yml"

oncotracer run \
  --backend conda \
  --config "$PWD/project/config/illumina.auto.yml"
```

The installer creates or updates isolated versioned prefixes. qDNAseq and
ichorCNA are separated because their R stacks are incompatible. The classifier
and GISTIC2 also remain isolated. A prefix parent must be absent, empty, or
carry the exact OncoTracer ownership marker; populated unowned paths and
unmarked fixed children are never adopted. Updates are journaled under an
ownership-checked lock: the verified prior child is backed up and its
replacement is created directly at the final prefix, then semantically probed
and exactly inventoried. Unrelated siblings are preserved, and active managed
prefixes are refused.

`oncotracer doctor --backend conda` uses exact prefix executables and semantic tool/package probes rather than relying on a login shell's `PATH`.

Use an alternate prefix parent when local policy requires it:

```bash
oncotracer install --conda \
  --prefix "/large/storage/oncotracer-v2-envs"
```

## Docker

### FASTQ to CNA and variants

Use the current source checkout and this published integration image for the
combined workflow (Linux x86-64):

```bash
docker pull carlosfarkas/oncotracer:fastq-variants-20260922
oncotracer setup --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260922 --variants
```

Select Illumina or ONT, Fresh or FFPE, and the compatible callers. **Save and
check**, then **Run analysis**, starts alignment, CNA analysis and the selected
variant stages in one project. ANNOVAR uses an existing licensed installation
and databases. With the 20260922 image, Run can prepare the selected Clair3
model and missing FFPERASE source/models after explicit license acceptance.
Checks and dry runs download no variant resources; existing paths remain supported.

Terminal equivalent, using the same image and variant branch:

```bash
oncotracer setup --terminal --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260922 --variants --run
```

Answer the platform, input, preservation, caller and resource questions. `--run`
starts analysis after validation. To save first, omit `--run` and choose **save**;
then use the project path you selected:

```bash
oncotracer check --config /absolute/path/to/my-study/config/run.yml
oncotracer run --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260922 \
  --config /absolute/path/to/my-study/config/run.yml
```

Choose either setup route for a new project. For fully unattended FASTQ setup,
use the [explicit sample commands](headless.md) with `--non-interactive`.

This dated image includes the synthetic FASTQ integration checks described in
[small-variant calling](variants.md#run-cna-and-variants-with-docker). The stable
release below predates this optional variant branch.

### Stable CNA release

Stable native image:

```text
ghcr.io/cfarkas/oncotracer:2.1.0
```

Install and run:

```bash
oncotracer install --docker
oncotracer doctor --backend docker
oncotracer check --config "$PWD/project/config/illumina.auto.yml"

oncotracer run \
  --backend docker \
  --config "$PWD/project/config/illumina.auto.yml"
```

For variant-enabled runs, the CLI reads the YAML and binds the configuration, FASTQs, reference and external resources at their existing absolute paths. Inputs and supplied resources are read-only; output and reference-cache locations are writable. Host tool environments are not mounted over the image environments.

Docker runs as the invoking `UID:GID` so result ownership remains with the user. Docker daemon access is privileged; follow institutional policy instead of adding undocumented administrator workarounds.

Select a local image tag or immutable registry digest:

```bash
oncotracer run \
  --backend docker \
  --image ghcr.io/cfarkas/oncotracer:2.1.0 \
  --config "$PWD/project/config/illumina.auto.yml"
```

The browser's **Docker image tag or digest** field and `setup --image` save the
selection as `docker_image` in the project YAML. `setup --project PROJECT --run`
also reuses the saved `execution_backend`. For `run --backend docker`, an explicit
`--image` takes precedence over `docker_image`, then the installed/default image.
A local tag can therefore be tested without replacing the configured release.

Docker supports CNA with optional [small-variant calling](variants.md#run-cna-and-variants-with-docker)
from Illumina or ONT FASTQs. The updated Dockerfile bundles native Clair3 v1.2.0 and ClairS-TO v0.4.4 alongside
the Illumina callers. Requested callers must be native tools in the chosen image;
a container preflight checks them before alignment. Docker does not nest
ClairS-TO or FFPERASE SIF execution. External source/model folders and existing
ANNOVAR databases remain host resources and are mounted read-only. Browser setup
hides host prefix and SIF fields when Docker is selected. Docker methylation and
Singularity with small variants remain unsupported.

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
oncotracer check --config "$PWD/project/config/illumina.auto.yml"

oncotracer run \
  --backend singularity \
  --config "$PWD/project/config/illumina.auto.yml"
```

Apptainer is preferred when both executables are present. The installer pulls the same native image into a local SIF and records its path in the OncoTracer configuration. The runner uses `--cleanenv` and explicit binds derived from YAML paths.

The SIF and its strict `.oncotracer.json` ownership sidecar form one managed
pair. Reuse verifies the canonical path, image reference, source identity, file
SHA-256, container provenance, and native host doctor. `--force` never unlinks
an existing file first: it stages and verifies a sibling candidate, then
atomically publishes it with a rollback journal. Unowned, malformed, symlinked,
hard-linked, or active destinations are preserved and rejected.

Choose an explicit SIF location:

```bash
oncotracer install --singularity \
  --sif "/large/storage/containers/oncotracer-2.1.0.sif"
```

Or override it for one run:

```bash
oncotracer run \
  --backend singularity \
  --sif "/large/storage/containers/oncotracer-2.1.0.sif" \
  --config "$PWD/project/config/illumina.auto.yml"
```

## Poetry development route

```bash
./oncotracer install --poetry \
  --prefix /path/to/my/oncotracer-v2-dev-envs

ONCOTRACER_DEV=/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer
"$ONCOTRACER_DEV" doctor --backend poetry
"$ONCOTRACER_DEV" check --config "$PWD/project/config/illumina.auto.yml"
"$ONCOTRACER_DEV" run \
  --backend poetry \
  --config "$PWD/project/config/illumina.auto.yml"
```

Poetry 2.0 or newer is required. OncoTracer builds a wheel in isolated
transaction state from the exact clean checkout, then installs it using only
the explicit owned `poetry-runtime` child's Python and pip. It does not mutate
a global Poetry environment, ambient site-packages, or a checkout-local
`.venv`. It does not replace
BWA, SAMtools, Picard, qDNAseq, HMMcopy, ichorCNA, the classifier stack, or
GISTIC2; those are still resolved from the exact managed prefixes.

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

Download the reads and create the configurations using the ordinary `setup`
commands in [QuickStart 1](quick_start.md) or [QuickStart 2](public_cohort.md).
After `check` succeeds, select your installed backend with `--backend`:

```bash
cd /path/to/my/analyses_dir/

oncotracer run --backend docker \
  --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
oncotracer run --backend docker \
  --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"

oncotracer run --backend singularity \
  --config "$PWD/oncotracer-quickstart2/analysis/config/run.yml"
```

The scientific settings remain the same across supported backends. Setup also records the selected execution backend and optional Docker image. All absolute input paths must remain available; use the tool and container options supported by the selected backend.
