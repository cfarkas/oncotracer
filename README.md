# OncoTracer

[![Release](https://img.shields.io/github/v/release/cfarkas/oncotracer)](https://github.com/cfarkas/oncotracer/releases) [![Documentation](https://img.shields.io/badge/docs-read%20the%20guide-blue)](https://cfarkas.github.io/oncotracer/) [![Tests](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml/badge.svg)](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml)

Analyze Illumina and Oxford Nanopore (ONT) reads for DNA copy-number changes and small variants. Set up samples in your browser, run the analysis, and review tables, plots and reports. ONT methylation supports MARLIN leukemia and Sturgeon CNS-tumor research classifiers. For research use.

## Quick start

Use Linux and Python 3.10–3.13. Install the application in your Python environment:

```bash
pip install oncotracer
```

No Git checkout is required. Choose **one** backend for the analysis tools:

### Conda

With [Conda](https://github.com/conda-forge/miniforge#install) available:

```bash
oncotracer install --conda
oncotracer doctor --backend conda
oncotracer --interactive --backend conda
```

### Docker

With Docker running and accessible:

```bash
oncotracer install --docker --image carlosfarkas/oncotracer:fastq-variants-20261017
oncotracer doctor --backend docker
oncotracer --interactive --backend docker
```

The Docker image is saved for later commands. See [installation](docs/installation.md) for Python environments, requirements and other options. FFPE/ONT callers, model resources and ANNOVAR have [additional setup requirements](docs/variants.md); use Conda for ONT methylation.

## Set up your analysis

1. Choose **Illumina or ONT**, then **Fresh or FFPE**.
2. Select input files, assign samples to Normal or Cancer, and choose the analysis and project folder.
3. Review settings, select **Save configuration and check**, then **Run analysis**.

Keep the terminal open and use its complete printed URL, including the session code after `#`. Open another terminal for another project; each session chooses an available port. Use distinct project/output folders and share the server’s CPU and memory between runs.

To prefill a project:

```bash
oncotracer setup --project "$PWD/my-study" --mode illumina --input-folder /data/illumina
```

### Nextflow entry

With Nextflow and an [OncoTracer source checkout](docs/installation_details.md#install-from-github), open the browser from any directory using the full path:

```bash
nextflow run /absolute/path/to/oncotracer-src/main.nf --interactive --backend conda
```

Use `--backend docker` for Docker. See [Nextflow setup and concurrent projects](docs/nextflow_entry.md).

### Interactive demo

[![OncoTracer browser setup: assign samples, choose settings, review and run. Click to explore the interactive demo.](docs/assets/setup-demo-preview.png)](https://cfarkas.github.io/oncotracer/assets/setup-demo/index.html)

[Try the demo](https://cfarkas.github.io/oncotracer/assets/setup-demo/index.html) with synthetic samples and simulated execution. See the [browser walkthrough](docs/browser_demo.md) for the complete sequence.

### Run saved settings

```bash
oncotracer check --config "$PWD/my-study/config/run.yml"
oncotracer run --backend conda --config "$PWD/my-study/config/run.yml"
```

For Docker, use `--backend docker`. Terminal setup and SSH are covered in the [headless guide](docs/headless.md).

## Uninstall

Remove the backend you installed, then the Python application.

**Conda tools:** permanently remove the managed environments and their matching extra global command:

```bash
oncotracer uninstall --conda --yes --purge
```

**Docker image:** remove the selected image/tag:

```bash
docker image rm carlosfarkas/oncotracer:fastq-variants-20261017
```

This Docker command removes only the image/tag. It does not remove the pip application, an extra global launcher, saved settings, projects or results. Shared image layers may remain.

**Python application:** in the Python environment used for installation:

```bash
python -m pip uninstall oncotracer
```

If an extra OncoTracer-owned global launcher remains, [remove it before the Python application](docs/uninstall.md#global-command). Pip removes its own console entry. See [component-by-component removal](docs/uninstall.md) for custom prefixes, separately installed tools and retained settings. User data and results remain.

## Guides and examples

| Task | Guide |
| --- | --- |
| Try public Illumina and ONT data | [QuickStart 1](docs/quick_start.md) · [QuickStart 2](docs/public_cohort.md) |
| Check hardware or reuse genome indexes | [Requirements](docs/installation.md#requirements) · [Prebuilt indexes](docs/reference_indexes.md) |
| Prepare multiple samples | [Batch setup](docs/auto_params.md) · [Command basics](docs/command_basics.md) |
| Add analysis options | [Methylation](docs/configuration/methylation.md) · [Variants](docs/variants.md) · [Paper report](docs/paper_report.md) |
| Understand results | [Outputs](docs/outputs.md) · [Troubleshooting](docs/troubleshooting.md) |

The [complete documentation](https://cfarkas.github.io/oncotracer/) covers settings and validation. Optional `run_cna_classifier: true` adds CNA interpretation. Runs record commands and checksums; released builds include `release-provenance.json`.
