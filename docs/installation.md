# Installation

OncoTracer v2 runs on Linux as one verified global executable. Python 3.10–3.13 is required by the portable zipapp. The selected backend supplies BWA, samtools, Picard, R, qDNAseq, HMMcopy, ichorCNA, the optional classifier, and GISTIC2.

## Requirements

Before installation, provide:

- a 64-bit Linux host;
- Python 3.10–3.13;
- enough storage for FASTQs, hg38, BAMs, environments, temporary files, and results;
- at least 80 GiB of addressable memory for the first uncached Illumina BWA index;
- one backend: Conda, Docker, or Singularity/Apptainer.

The first analysis can take substantially longer because the reference, indexes, packages, and container layers are prepared. If `lpwgs_root/references/samurai_hg38` already exists, OncoTracer treats it as an external, read-only shared reference: every pinned file, index manifest, physical reader lock, and tool identity must validate, and OncoTracer will stop instead of repairing it in place. A plain existing FASTA/index directory is not auto-adopted. When that path is absent, FASTA/index, ichorCNA, and qDNAseq assets go to OncoTracer-owned, content-addressed caches below `lpwgs_root/.oncotracer/reference-cache/`. Later analyses reuse those validated assets even when they use a fresh result directory. Conda channel URLs are retained as informational provenance, but portability validity is based on stable package metadata and the exact indexing-executable SHA-256.

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

The prefix parent must be absent, empty, or already owned by this OncoTracer
installer. OncoTracer never adopts a populated Conda directory or an unmarked
fixed child (`core`, `qdnaseq`, `ichorcna`, `classifier`, `gistic`, or the
optional Poetry runtime). Strict markers bind each managed path to an install
ID, environment definition, exact file inventory, and OncoTracer source
identity. Creation and updates run under an ownership-checked lock. For an
owned replacement, OncoTracer journals the operation, moves only the verified
old child to a same-filesystem backup, and creates the new Conda or Poetry
environment directly at its final canonical prefix. Semantic probes and the
exact inventory must pass there before commit; interruption restores the prior
child. An unsealed tree left by SIGKILL is moved to a reported sibling
`oncotracer-preserved` path rather than deleted. Shared runtime locks keep an
installer from replacing a prefix while an analysis is consuming it. `--force`
applies only to an intact owned installation and refuses prefixes used by
active processes. Unrelated siblings under an owned prefix parent are preserved.

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

The first installation requires an absent SIF destination. OncoTracer writes a
strict `.oncotracer.json` sidecar that binds the canonical path, source image,
SIF SHA-256, install ID, and OncoTracer source identity. Reuse rechecks the
sidecar and bytes, then verifies the native `doctor` and container provenance
inside the image.
`--force` is accepted only for an owned pair: a new image is pulled to a
same-directory transaction, verified before publication, and atomically
swapped with rollback protection. Existing unowned files, symlinks,
hard-linked files, malformed sidecars, and active images fail closed without
being removed.

After any committed Conda, Poetry, or SIF transaction, OncoTracer retains the
authenticated rollback tree and its journal instead of deleting either one.
They are reported on standard error and stored beside the installation target:

```text
.<target>.oncotracer-<kind>-txn-<transaction-id>.oncotracer-retained
.oncotracer-<kind>-retained-journal-<transaction-id>-<target-binding>.json
```

These transaction-ID-bound paths are ignored by later installs, so an
interrupted journal-retention step can be resumed and a new install can proceed
without overwriting earlier evidence. OncoTracer never automatically removes
retained rollback or audit material. An administrator may archive or remove an
exact reported path only after inspecting it and confirming that no OncoTracer
install is running; never use a wildcard cleanup command.

Failed publications use the same rule: after restoring the prior target,
OncoTracer writes a durable `rolled_back` journal, seals the exact rollback
inventory, and retains both records. A later invocation can resume between any
of those steps without overwriting earlier evidence.

### Poetry

Poetry 2.0 or newer is required. Poetry is a source-development route, not the normal binary installation. Clone the repository only for development:

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
./oncotracer install --poetry \
  --prefix /path/to/my/oncotracer-v2-dev-envs

/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer --version
/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer doctor \
  --backend poetry
```

`./oncotracer install --poetry` installs the locked Python launcher into the
explicit, OncoTracer-owned `poetry-runtime` child and creates the same five
Conda scientific environments. It does not select or modify Poetry's global
environment, another checkout's virtual environment, or an existing unowned
prefix. OncoTracer first builds a wheel from an exact clean checkout in an
isolated transaction tree, then invokes only the final virtual environment's
Python and pip to install that wheel with dependencies and indexes disabled.
Poetry's configuration, data, build environment, and caches remain inside the
transaction; the checkout, ambient interpreter, global site-packages, and
checkout-local `.venv` are unchanged.
Poetry alone does not provide R, BWA, samtools, Picard, HMMcopy, ichorCNA, or
GISTIC2.

Installer options are backend-specific. `--prefix` is accepted only for Conda
and Poetry, while `--image` and `--sif` apply only to the relevant container
backend. OncoTracer rejects irrelevant combinations instead of silently
ignoring a path or force request.

## 3. Verify provenance and health

Run these checks after installation or an ownership-verified replacement.
Managed Conda and Poetry prefixes are canonical-path-bound and must not be moved:

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
