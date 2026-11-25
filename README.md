# OncoTracer

[![Release](https://img.shields.io/github/v/release/cfarkas/oncotracer)](https://github.com/cfarkas/oncotracer/releases) [![Documentation](https://img.shields.io/badge/docs-read%20the%20guide-blue)](https://cfarkas.github.io/oncotracer/) [![Tests](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml/badge.svg)](https://github.com/cfarkas/oncotracer/actions/workflows/native-v2-ci.yml)

Analyze Illumina and Oxford Nanopore (ONT) copy-number changes and small variants. ONT methylation supports MARLIN leukemia and Sturgeon CNS-tumor classifiers. For research use.

## Quick start

Install with Linux and Python 3.10–3.13. No Git checkout is required:

```bash
python3 -m pip install --upgrade --force-reinstall "https://github.com/cfarkas/oncotracer/releases/download/v5.0.0/oncotracer-5.0.0-py3-none-any.whl"
```

Version 5 defaults to Rockfish, retains Modkit and preserves the 4.1 ichorCNA fallback, Broad profiles and coarse rescues. See [caller setup](docs/releases/5.0.0.md). `pip install oncotracer` follows the separate PyPI release. Choose **one** backend:

### Conda

With [Conda](https://github.com/conda-forge/miniforge#install) available:

```bash
oncotracer install --conda
oncotracer doctor --backend conda
oncotracer --interactive --backend conda
```

### Docker

With Docker running:

```bash
oncotracer install --docker --image carlosfarkas/oncotracer:5.0.0
oncotracer doctor --backend docker
oncotracer --interactive --backend docker
```

OncoTracer remembers the Docker image selected during installation. Pass `--image` to use a different one. ONT methylation also needs Dorado and a MARLIN or Sturgeon model; follow the [caller setup guide](docs/releases/5.0.0.md).

Run `oncotracer --detect-resources` to size parallel jobs. Choose the allocation in **Review and run**; see [CPU and memory settings](docs/MULTISAMPLE_QUICKSTART.md).

New projects use the [sensitive CNA profile](docs/cna_evidence_assessment.md), which checks read support for low-coverage calls. Existing projects keep their saved profile.

For GPU support, follow the [GPU installation steps](docs/gpu.md#install-and-check), then select **GPU usage → Yes** in setup.

### Interactive setup with Nextflow

From the [source checkout](docs/installation_details.md#editable-development-installation), Nextflow opens the same interface:

```bash
nextflow run main.nf --interactive --backend conda
```

Use the full path to `main.nf` from another directory. Select **Run analysis** to start. See [Nextflow setup](docs/nextflow_entry.md).

## Run a saved project

Setup saves `config/run.yml`. Run it with:

```bash
oncotracer run --config /path/to/project/config/run.yml --backend conda
```

Use `--backend docker` for Docker. See the [version 5 run guide](docs/releases/5.0.0.md) for caller setup and the [real benchmark commands](#real-benchmark-runs) below.

## Set up your analysis

1. Choose **Illumina or ONT**, then **Fresh or FFPE**.
2. Select input files, assign samples to Normal or Cancer, and choose the analysis and project folder.
3. Review settings, select **Save configuration and check**, then **Run analysis**.

Keep the terminal open and use its complete URL, including the code after `#`. Concurrent projects need separate terminals, ports and output folders; divide resources between them.

## Interactive demo

[![OncoTracer browser setup: assign samples, choose settings, review and run. Click to explore the interactive demo.](docs/assets/setup-demo-preview.png)](https://cfarkas.github.io/oncotracer/assets/setup-demo/index.html)

[Try the demo](https://cfarkas.github.io/oncotracer/assets/setup-demo/index.html) with synthetic samples and simulated execution. See the [browser walkthrough](docs/browser_demo.md) for the sequence.

### Continue an existing analysis

Use `oncotracer --interactive --project /path/to/existing-project`. Load saved settings, then choose parallel sample jobs in **Review and run**. **Run analysis** applies and checks your selection. See [intermediate checks and relocation](docs/running.md#resume-behavior).

[Sturgeon setup](docs/configuration/methylation_reference.md#sturgeon-pretrained-model-setup) offers the pretrained general model and hg38 probes after explicit license acknowledgement.

### ONT input layout

Select `ONT/` with **one folder per library**. Folder names become sample names; keep libraries separate even when they share a patient.

```text
ONT/
├── ONCO-001/
│   ├── fastq_pass/
│   ├── fastq_fail/
│   ├── pod5_pass/
│   ├── pod5_fail/
│   └── bam_pass/       # or modified_bam/
└── ONCO-002/           # same layout
```

CNA/variants use `fastq_pass`; Rockfish methylation uses matching POD5 and Dorado models. Route MARLIN/Sturgeon using clinical records; select Modkit to reuse modified-base BAMs. See [ONT settings](docs/configuration/ont.md), [CpG calling](docs/post_ont_cpg.md) and [SSH setup](docs/headless.md).

Validate before execution: `oncotracer check --config /path/to/project/config/run.yml`.

## Guides and examples

| Task | Guide |
| --- | --- |
| Try public Illumina and ONT data | [QuickStart 1](https://cfarkas.github.io/oncotracer/quick_start/) · [QuickStart 2](https://cfarkas.github.io/oncotracer/public_cohort/) |
| Check hardware or reuse genome indexes | [Requirements](docs/installation.md#requirements) · [Prebuilt indexes](docs/reference_indexes.md) |
| Prepare multiple samples | [Batch setup](docs/auto_params.md) · [Command basics](docs/command_basics.md) |
| Add analysis options | [Methylation](docs/configuration/methylation.md) · [Variants](docs/variants.md) · [Paper report](docs/paper_report.md) |
| Understand results | [Outputs](docs/outputs.md) · [Troubleshooting](docs/troubleshooting.md) |

[Complete documentation](https://cfarkas.github.io/oncotracer/). Optional `run_cna_classifier: true` adds CNA interpretation. Runs record commands and checksums; released builds include `release-provenance.json`.

## Real benchmark runs

Commands from the **58-sample version 5 benchmark**. The linked YAMLs retain recorded settings with placeholder paths and identifiers. See [configuration instructions and input tables](examples/benchmark_v5/README.md) and [measured timings, resources and QC](docs/benchmarks/2026-11-23.md).

Budgets are cohort-wide; reduce them for smaller machines. Memory settings are in each YAML. The benchmark additionally enforced Linux memory limits separately.

### ONT — 21 samples

[ONT/config/run.yml](examples/benchmark_v5/ONT/config/run.yml) · **66 threads**, **4 parallel samples**, **360 GiB RAM**. Methylation: **RTX A5000, 24 GiB VRAM**. Alignment/variants: CPU.

```bash
oncotracer run --config /path/to/ONT/config/run.yml --backend docker --image carlosfarkas/oncotracer:5.0.0 --methylation --methylation-caller rockfish --threads 66 --parallel-samples 4 --no-merge-vcfs --resource-mode dynamic --storage-mode compact --cna-profile sensitive
```

### FFPE Illumina — 26 samples

[FFPE/config/run.yml](examples/benchmark_v5/FFPE/config/run.yml) · **42 threads**, **7 parallel samples**, **400 GiB RAM**, **CPU only**.

```bash
oncotracer run --config /path/to/FFPE/config/run.yml --backend conda --threads 42 --parallel-samples 7 --no-merge-vcfs --cpu --resource-mode fixed --storage-mode compact --cna-profile standard
```

### Fresh Illumina — 11 samples

[Fresh/config/run.yml](examples/benchmark_v5/Fresh/config/run.yml) · **18 threads**, **3 parallel samples**, **176 GiB RAM**, **CPU only**.

```bash
oncotracer run --config /path/to/Fresh/config/run.yml --backend conda --threads 18 --parallel-samples 3 --no-merge-vcfs --cpu --resource-mode fixed --storage-mode compact --cna-profile standard
```
