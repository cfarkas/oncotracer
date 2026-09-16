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

These are planning estimates, not guaranteed minimums. Leave at least 60 GiB
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
`doctor` checks those tools and prints a short OK/FAIL summary. The installer shows
progress and elapsed time, with colors in supported terminals. Full package output
goes to the printed log path; add `--verbose` to show package output in the terminal.
Use `install --json` or `doctor --json` for automation, and `NO_COLOR=1` to disable colors.

Conda is the recommended starting backend. Docker, Apptainer and development
options are described in [execution backends](containers.md) and
[advanced installation](installation_details.md).

Methylation needs separately installed Dorado, Modkit and classifier resources;
see the [methylation guide](configuration/methylation.md). Installing the standard
tools does not install those models.

## 3. Start a project

Start the local browser setup:

```bash
oncotracer setup
```

The page opens at **127.0.0.1:8888**; use the complete printed URL if needed.
Keep the terminal open. Choose the platform, browse to FASTQs, assign detected
samples to Normal or Cancer, and review threads and bins (**100 kb** for QDNAseq).
Choose a project folder, click **Save configuration and check**, then **Run analysis**.
See the [setup guide](setup.md) or try [QuickStart 1](quick_start.md).

For terminal prompts, use `oncotracer setup --terminal`. To resume a saved project:

```bash
oncotracer setup --project /absolute/path/to/my-study --run
```

Matching alignment and CNA calling results are reused; refinement and reports
are regenerated. To check and run separately:

```bash
oncotracer check --config /absolute/path/to/my-study/config/run.yml
oncotracer run --backend conda --config /absolute/path/to/my-study/config/run.yml
```

By default, run downloads prebuilt hg38 indexes.
Use `setup --hg38_build /path/to/reference` to reuse a build, or
`setup --build_reference` to build indexes locally using more RAM, disk and time.
See [genome indexes](reference_indexes.md).

## In a new terminal

Activate the same environment, using the location you chose during installation:

```bash
source /absolute/path/to/oncotracer-env/bin/activate
oncotracer --help
```

To remove OncoTracer later, follow [uninstall](uninstall.md).
