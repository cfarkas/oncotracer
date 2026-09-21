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
| Prepare many libraries or ONT barcodes | [Batch examples](docs/auto_params.md) · [Create CSV tables](docs/command_basics.md) |
| Check RAM or reuse genome indexes | [System requirements](docs/installation.md#requirements) · [Prebuilt indexes](docs/reference_indexes.md) |
| Classify ONT methylation | [Methylation guide](docs/configuration/methylation.md) |
| Add small-variant calling or reuse existing BAMs | [Variant guide](docs/variants.md) |
| Prepare manuscript figures from saved results | [Paper report](docs/paper_report.md) |
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

`install` shows live progress and saves full diagnostics to a log. Use `--verbose` for package output or `doctor --json` for automation. `system` explains hardware capacity before installing tools. [Uninstall instructions](docs/uninstall.md) cover preview, recovery, and permanent removal without deleting project data.

## Set up your analysis in the browser

```bash
oncotracer setup
```

This starts the local web page at **127.0.0.1:8888** and opens your browser.
If it does not open, copy the complete URL printed in the terminal, including
its session code after `#`. Keep that terminal open while using the page.
`oncotracer web` also opens the browser interface.

1. **Choose Illumina or ONT.** Click **Browse folders** and select your FASTQ folder.
   OncoTracer discovers the samples automatically: Illumina read pairs become one
   sample; each ONT barcode becomes one sample containing all its FASTQ batches.
   A nonbarcoded ONT ligation folder is one sample.
2. **Assign samples.** Drag cards from **Detected · unassigned** into **Normal** or
   **Cancer**, or use each card's dropdown. Edit the detected sample names as needed.
   Unassigned samples are excluded. Custom tags also let you select an analysis role.
   Normal/Cancer labels accept any capitalization; normal samples are analyzed
   independently, without pooling or subtraction.
3. **Choose settings.** Review detected CPUs, RAM and GPUs, then set your threads.
   QDNAseq defaults to **100 kb** bins; ONT ichorCNA uses **500 kb**.
   Optional CNA reports and local language models work for one sample;
   GISTIC recurrence analysis becomes available with at least two assigned samples.
4. **Choose a project folder**, tools and reference. The default downloads prepared
   hg38 indexes when analysis starts. Choose **Reuse a prepared OncoTracer reference**
   to browse an existing reference, or **Build indexes locally on CPU** for local indexing.
5. Click **Save configuration and check**, review the settings, then **Run analysis**.
   Follow progress or click **Stop analysis**. Stopping offers **Keep project**
   (default) or confirmed folder removal. Click **Open results** when complete.

Prefill a new Illumina project and discover its FASTQs:

```bash
oncotracer setup --project "$PWD/my-study" --mode illumina \
  --input-folder /data/illumina
```

Replace `/data/illumina` with your FASTQ folder. Follow the separate [Illumina and ONT walkthroughs](docs/setup.md) or [QuickStart 1](docs/quick_start.md).
For terminal prompts, use `oncotracer setup --terminal`; press Enter to accept shown defaults such as `[100]` for the QDNAseq bin size.
Scripts can use `setup --non-interactive` with explicit sample flags. `setup --project PATH --run` resumes an existing saved project.

The page saves `my-study/config/run.yml` and sample metadata. To run later:

```bash
oncotracer check --config "$PWD/my-study/config/run.yml"
oncotracer run --backend conda --config "$PWD/my-study/config/run.yml"
```

Saving and checking do not start analysis or genome downloads. Results go to
`my-study/results/`; begin with `06_workflow_summary/workflow_summary.txt`.
Methylation also produces `07_methylation/methylation_status.json`.

## Try the public example

[QuickStart 1](docs/quick_start.md) provides small public Illumina and ONT downloads, then uses the same `setup`, `check`, and `run` commands shown above. [QuickStart 2](docs/public_cohort.md) shows a three-library Illumina analysis. Genome-index reuse is optional; neither analysis needs a special example launcher.

The [complete documentation](https://cfarkas.github.io/oncotracer/) includes [batch setup](docs/auto_params.md), [all settings](docs/configuration/parameter_reference.md), and [release validation](docs/parity_release.md). The optional `run_cna_classifier: true` setting adds interpretation of copy-number changes; it is separate from methylation classification. Runs record their commands and output checksums. Released builds also include `release-provenance.json`.

### Additional variant assessments

Illumina FFPE projects now require FFPERASE unless explicitly disabled. Optional Varlociraptor evaluates BAM evidence and applies local FDR filtering. Original caller genotypes and filters are retained; insufficient or unsupported evidence stays explicitly unevaluated. See [variant configuration and environments](docs/variants.md#additional-assessments-ffperase-and-varlociraptor). The Dockerfile includes separate pinned variant and FFPERASE environments; source/models and optional ANNOVAR databases remain external. For CNA plus variants from FASTQ, select Docker and a local image in browser setup, or use `setup --backend docker --image YOUR_IMAGE`. The project retains the image for its run. Docker uses native image tools and mounts external resources; host/Conda remains available for existing SIF callers. Set `ONCOTRACER_IMAGE` to select a locally built image with Docker Compose.
