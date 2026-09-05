# OncoTracer

[![Release](https://img.shields.io/github/v/release/cfarkas/oncotracer)](https://github.com/cfarkas/oncotracer/releases)
[![Documentation](https://img.shields.io/badge/docs-read%20the%20guide-blue)](https://cfarkas.github.io/oncotracer/)
[![Tests](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml/badge.svg)](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml)

OncoTracer analyzes Illumina and Oxford Nanopore (ONT) sequencing data. It finds gains and losses of DNA, called **copy-number changes**, and produces tables, plots, and a run summary. For ONT data with methylation calls, it can also run MARLIN for leukemia research or Sturgeon for CNS-tumor research.

OncoTracer is for research use, not a standalone diagnostic system.

## Start here

| What you want to do | Guide |
| --- | --- |
| Try public data first | [QuickStart 1](docs/quick_start.md) |
| Analyze your own FASTQs | [Set up a project](docs/setup.md) |
| Check RAM or reuse genome indexes | [System requirements](docs/installation.md#requirements) · [Prebuilt indexes](docs/reference_indexes.md) |
| Classify ONT methylation | [Methylation guide](docs/configuration/methylation.md) |
| Understand a result or an error | [Outputs](docs/outputs.md) · [Troubleshooting](docs/troubleshooting.md) |

## Install

You need Linux, Python 3.10–3.13, Git, and Conda. Run these commands in a directory where you keep software:

```bash
git clone --branch main https://github.com/cfarkas/oncotracer.git oncotracer-src
python3 -m venv oncotracer-env
oncotracer-env/bin/python -m pip install -e ./oncotracer-src
source oncotracer-env/bin/activate
oncotracer system --path /absolute/path/to/my-study
oncotracer install --conda
oncotracer doctor --backend conda
```

The first four commands install and activate OncoTracer. Keep both folders: the source folder is part of this installation. `install --conda` installs the analysis tools; `doctor` checks them. Keep the environment activated when using `oncotracer`; see [installation](docs/installation.md) for details and other backends.

`system` explains hardware capacity before installing tools. [Uninstall instructions](docs/uninstall.md) cover preview, recovery, and permanent removal without deleting project data.

## Set up your analysis

```bash
oncotracer setup --project /absolute/path/to/my-study
```

Replace `/absolute/path/to/my-study` with the project folder you want to create. Setup asks which analysis and reads to use, then saves a commented configuration at `my-study/config/run.yml`. It prints the next commands:

```bash
oncotracer check --config /absolute/path/to/my-study/config/run.yml
oncotracer run --backend conda --config /absolute/path/to/my-study/config/run.yml
```

`--config` selects your saved settings. `--backend conda` selects the installed analysis tools. `check` explains configuration problems without starting an analysis; `run` starts it. You can also [supply setup answers as flags](docs/setup.md).

Results go to `my-study/results/`. Begin with `06_workflow_summary/workflow_summary.txt`; methylation results also have `07_methylation/methylation_status.json`.

## Try the public example

[QuickStart 1](docs/quick_start.md) provides small public Illumina and ONT downloads, then uses the same `setup`, `check`, and `run` commands shown above. [QuickStart 2](docs/public_cohort.md) shows a three-library Illumina analysis. Genome-index reuse is optional; neither analysis needs a special example launcher.

The [complete documentation](https://cfarkas.github.io/oncotracer/) includes [batch setup](docs/auto_params.md), [all settings](docs/configuration/parameter_reference.md), and [release validation](docs/parity_release.md). The optional `run_cna_classifier: true` setting adds interpretation of copy-number changes; it is separate from methylation classification. Runs record their commands and output checksums. Released builds also include `release-provenance.json`.
