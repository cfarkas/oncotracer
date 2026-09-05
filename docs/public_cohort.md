# QuickStart 2: three HCC1143 libraries

This example analyzes three public Illumina libraries. Preparation downloads all six paired-end FASTQs and validates each exact size and MD5 checksum. You then run the same `oncotracer run` command used for your own data.

| Sample | Public run |
| --- | --- |
| `HCC1143_DMSO` | `SRR7085656` |
| `HCC1143_BEZ235` | `SRR7085655` |
| `HCC1143_TRAMETINIB` | `SRR7085657` |

Start with [QuickStart 1](quick_start.md) if you have not tested your installation. This larger example needs storage for six FASTQs, reference files, BAMs, and outputs. The first uncached Illumina index requires at least 80 GiB of addressable memory.

## Prepare and analyze

Replace the first path with an existing analysis directory. `$PWD` means that directory; `--test-root` is the new example folder underneath it.

```bash
cd /path/to/my/analyses_dir/

oncotracer install --conda
oncotracer doctor --backend conda

oncotracer quickstart 2 --test-root "$PWD/oncotracer-quickstart2" --download-only
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart2/configs/hcc1143_lpwgs/illumina.auto.yml"
```

Skip `install` and `doctor` if this backend is already installed and checked. `--download-only` prepares the reads and settings without analysis. `--config` selects the generated YAML; `--backend conda` selects the analysis tools.

Before running, open `configs/hcc1143_lpwgs/illumina.auto.yml` and `illumina.samplesheet.csv` to inspect the paths and sample names. Current-source users can also run `oncotracer check --config PATH_TO_YAML`.

## Read and verify the outputs

Results are under `oncotracer-quickstart2/runs/hcc1143_lpwgs/`. Open:

- `06_workflow_summary/workflow_summary.txt` for completion status;
- `03_cna_codification/cna_events.tsv` for copy-number changes;
- `04_cna_custom_plots/cna_per_sample_pages.pdf` for plots.

To run the example's required-output checks, use the command without `--download-only`:

```bash
cd /path/to/my/analyses_dir/
oncotracer quickstart 2 --backend conda --test-root "$PWD/oncotracer-quickstart2"
```

It reuses matching completed stages and checks the required summary, event table, and sample PDF. Success ends with `QuickStart 2 completed:` and the example path.

## Resume

After correcting an error, repeat the same `run` command. Keep the YAML and output directory unchanged, and leave `--force` off for a normal resume.

For Docker use `--backend docker`, or for Apptainer use `--backend singularity`, after [installing that backend](containers.md). For your own samples, follow [project setup](setup.md) or [batch setup](auto_params.md).
