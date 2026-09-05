<a id="quick-start"></a>

# QuickStart 1: try public data

This complete native analysis uses one Illumina library and one ONT library. It downloads approximately 225 MB of reads and produces copy-number results for each. It is not a methylation example: the supplied FASTQs do not contain methylation calls.

## Before you start

[Install OncoTracer](installation.md) and choose one backend. This page uses Conda. The first uncached Illumina run also prepares the human reference and BWA index: allow tens of minutes and at least 80 GiB of addressable RAM. The small read download is not the total storage requirement; the reference, tools, BAMs, and results need additional space.

Replace `/path/to/my/analyses_dir/` below with an existing directory for this example. `$PWD` means that directory. All example downloads and results go under `oncotracer-quickstart1`.

## Commands to copy

```bash
cd /path/to/my/analyses_dir/

oncotracer install --conda
oncotracer doctor --backend conda

oncotracer quickstart 1 --test-root "$PWD/oncotracer-quickstart1" --download-only

oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/configs/illumina.quickstart.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/configs/ont.quickstart.yml"
```

Run the commands in order. If you already installed and checked the Conda backend, begin at `quickstart`.

| Command or flag | Meaning | What to expect |
| --- | --- | --- |
| `install --conda` | Install the analysis tools once | Conda environments are prepared |
| `doctor --backend conda` | Check those tools | Resolve reported failures before analysis |
| `quickstart 1` | Select this public example | Downloads are checked against known checksums |
| `--test-root` | Folder for the whole example | `public/`, `configs/`, and later `runs/` |
| `--download-only` | Prepare reads and settings without analysis | Two YAML files in `configs/` |
| `run --config` | Analyze the data described by that YAML | Results for one sequencing platform |
| `--backend conda` | Use the installed Conda tools | Same setting for both runs |

You do not need to create or edit a sample table for this example. The Illumina library is `ERR12341627`; the ONT library is `DRR165691`, stored under `fastq_pass/barcode01/`.

## Inspect the saved settings

Open `configs/illumina.quickstart.yml` and `configs/ont.quickstart.yml` in a text editor before running. Check the input paths and `outdir`, which is where results will go.

If you installed current source, you can also run:

```bash
cd /path/to/my/analyses_dir/
oncotracer check --config "$PWD/oncotracer-quickstart1/configs/illumina.quickstart.yml"
oncotracer check --config "$PWD/oncotracer-quickstart1/configs/ont.quickstart.yml"
```

The v2.0.0 executable uses `oncotracer run --dry-run --backend conda --config PATH` instead; it does not have `check`.

## Read and verify the results

Each successful `run` prints `OncoTracer native analysis completed:` and its results path. Start with these files in both `runs/illumina/` and `runs/ont/`:

| File | What it tells you |
| --- | --- |
| `06_workflow_summary/workflow_summary.txt` | Whether the requested analysis completed |
| `03_cna_codification/cna_events.tsv` | Detected copy-number changes |
| `04_cna_custom_plots/cna_per_sample_pages.pdf` | Plots for visual review |
| `.oncotracer-native/trace.tsv` | Commands run, if you need to troubleshoot |

To run the example's output checks, repeat `quickstart` **without** `--download-only`:

```bash
cd /path/to/my/analyses_dir/
oncotracer quickstart 1 --backend conda --test-root "$PWD/oncotracer-quickstart1"
```

It reuses valid completed stages and checks both output trees. This command, unlike the preparation-only command, ends with `QuickStart 1 completed:` after verification.

## Resume or choose another backend

Repeat a failed `run` command after fixing its reported error; completed matching stages are reused. Keep the same configuration and leave `--force` off for a normal resume.

For Docker, replace `--backend conda` with `--backend docker`; for Apptainer, use `--backend singularity`. Install the chosen backend first as described in [execution environments](containers.md). You only need one backend.

Next, [set up your own data](setup.md) or try [QuickStart 2](public_cohort.md).
