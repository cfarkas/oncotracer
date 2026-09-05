# Install OncoTracer

Install the OncoTracer command first, then its analysis tools. Keep software and
analysis projects in separate folders.

## Requirements

You need 64-bit Linux, Python 3.10–3.13, Git and Conda. If Conda is missing, follow
the [Miniforge installation instructions](https://github.com/conda-forge/miniforge#install).
OncoTracer does not install Conda itself.

For a small low-pass genome run with 2–4 threads, plan for:

| Analysis | Available RAM to plan for |
| --- | --- |
| Illumina copy-number analysis | 16 GiB |
| ONT copy-number analysis | 24 GiB |
| Methylation classifiers or report LLMs | Depends on the model; checked separately |

These are planning estimates, not guaranteed minimums. Leave at least 40 GiB
free for tools, reference files and a small run, plus space for your FASTQs,
BAMs and temporary files. Large datasets need more. Prebuilt indexes avoid
index construction; they still need RAM during alignment.

## 1. Install the command

Run these commands in a directory where you keep software:

```bash
git clone --branch main https://github.com/cfarkas/oncotracer.git oncotracer-src
python3 -m venv oncotracer-env
oncotracer-env/bin/python -m pip install -e ./oncotracer-src
source oncotracer-env/bin/activate
oncotracer --help
```

`oncotracer-src` holds the code; `oncotracer-env` holds the command. Keep both
folders. The `-e` option links them and preserves the source identity used by
the tool installer. Do not edit the source or put results inside it.

## 2. Check this computer and install the tools

Replace the project path with the location where you plan to keep your analysis:

```bash
oncotracer system --path /absolute/path/to/my-study
oncotracer install --conda
oncotracer doctor --backend conda
```

`system` reports CPU, available RAM, free disk and limits before any download.
`install --conda` creates separate environments for the analysis tools.
`doctor` checks those tools. Resolve reported errors before starting an analysis.

Conda is the recommended starting backend. Docker, Apptainer and development
options are described in [execution backends](containers.md) and
[advanced installation](installation_details.md).

Methylation needs separately installed Dorado, Modkit and classifier resources;
see the [methylation guide](configuration/methylation.md). Installing the standard
tools does not install those models.

## 3. Start a project

```bash
oncotracer setup --project /absolute/path/to/my-study
oncotracer check --config /absolute/path/to/my-study/config/run.yml
oncotracer run --backend conda --config /absolute/path/to/my-study/config/run.yml
```

Setup asks for the inputs and saves a configuration. Check validates it; run
starts analysis. The [setup guide](setup.md) shows the paths and flags for single
and multiple samples. To try public data, follow [QuickStart 1](quick_start.md).

Reference preparation is automatic when needed. If indexes are already
available, follow [reuse or download genome indexes](reference_indexes.md).

## In a new terminal

Activate the same environment, using the location you chose during installation:

```bash
source /absolute/path/to/oncotracer-env/bin/activate
oncotracer --help
```

To remove OncoTracer later, follow [uninstall](uninstall.md).
