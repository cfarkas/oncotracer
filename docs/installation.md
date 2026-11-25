# Install OncoTracer

The recommended v6 route uses Docker for scientific tools and a small Python
application for project setup. Use Linux x86-64 and Python 3.10–3.13.

## Quick start: OncoTracer 6.0.0

Install [Docker Engine](https://docs.docker.com/engine/install/) and check that
`docker info` works for your user. Then install the application:

```bash
python3 -m venv oncotracer-env
source oncotracer-env/bin/activate
python -m pip install "https://github.com/cfarkas/oncotracer/releases/download/v6.0.0/oncotracer-6.0.0-py3-none-any.whl"
```

No Git checkout is needed. `pip install oncotracer` follows the separate PyPI
release; the URL above selects v6 explicitly. Keep this environment and activate
it in subsequent terminals.

Create [a sample table](launch.md), then run:

```bash
oncotracer launch --samples samples.csv --project my-run --specimen fresh
```

Launch pulls the image and prepares references automatically. The first run
needs internet access and takes longer; verified assets are reused. No host
Conda, R or Nextflow installation is needed. Use `--dry-run` to validate the
table and inspect CPU/RAM allocation without downloading or writing files.

## Browser setup

```bash
oncotracer install --docker --image carlosfarkas/oncotracer:6.0.0
oncotracer doctor --backend docker
oncotracer --interactive --backend docker
```

Open the complete URL printed in the terminal. Select inputs, sample roles,
analysis and output folder. Docker methylation prepares tools/models when you
save; CNS classification requires the Sturgeon license acknowledgement.
Select **Run analysis** after reviewing the saved settings.

## Conda and existing workflows

Conda remains supported for existing projects and advanced configurations:

```bash
oncotracer install --conda
oncotracer doctor --backend conda
oncotracer --interactive --backend conda
```

Install [Miniforge](https://github.com/conda-forge/miniforge#install) first.
See [installation details](installation_details.md) for Conda, Poetry, source,
standalone and Apptainer options. Keep v5 projects on their recorded version
unless you are explicitly validating a new output with v6.

## Requirements

Plan for at least **120 GiB free** for the full Docker runtime, its download/
extraction, initial references and methylation tools. Input files, BAMs, imputation
panels and results need additional space. A full human imputation reference can
be much larger than the genome index. Choose a spacious shared `--cache`.

CNA-only jobs typically need at least 16–24 GiB available RAM; full Illumina
launch plans at least 32 GiB per simultaneous sample. ONT raw-signal processing
and large cohorts may need substantially more. These are planning estimates,
not guarantees. Check your storage and available hardware:

```bash
oncotracer system --path /absolute/path/to/my-study
```

CPU/RAM options are total cohort budgets, not per-sample allocations. Concurrent
projects need separate output directories and explicitly divided budgets.
[Prepared genome indexes](reference_indexes.md) avoid rebuilding hg38 locally.

For GPU methylation, install the [NVIDIA container support](gpu.md) on the host.
Docker already supplies the supported application runtimes. GPU visibility is
checked before analysis. Use `--cpu` when GPU execution is not intended.

## Uninstall

Remove the application from its Python environment with
`python -m pip uninstall oncotracer`. Docker images, shared caches, projects and
results are separate. See [uninstall](uninstall.md) before removing any of them.
