# Install OncoTracer

Install the OncoTracer command, then choose **Conda or Docker** for analysis tools.
Keep software and analysis projects in separate folders.

## Requirements

Both routes need 64-bit Linux, Python 3.10–3.13 and Git for the launcher.
The Conda route also needs [Conda/Miniforge](https://github.com/conda-forge/miniforge#install).
Docker must be running and accessible to your user; host Conda is unnecessary.
Install either backend separately.

Desktop browsing needs Zenity (Ubuntu/GNOME) or KDialog (KDE). Install either,
or enter paths. Remote sessions use server paths.

For a small low-pass genome run with 2–4 threads, plan for:

| Analysis | Available RAM to plan for |
| --- | --- |
| Illumina copy-number analysis | 16 GiB |
| ONT copy-number analysis | 24 GiB |
| Methylation classifiers or report LLMs | Depends on the model; checked separately |

These are planning estimates. Leave at least 60 GiB free for tools, references
and a small run, plus space for FASTQs, BAMs and temporary files. Docker image
downloads and extraction also need free disk. Large datasets need more.

## 1. Install the command

For complete GLIMPSE2 and the resume/FFPE repairs in
[PR #53](https://github.com/cfarkas/oncotracer/pull/53), replace `--branch main`
below with `--branch feat/glimpse2-complete-short-variants` until merged.
Build Docker from that branch using the
[source image guide](containers.md#fastq-to-cna-and-variants); published tags are unchanged.

Install the launcher:

```bash
git clone --branch main https://github.com/cfarkas/oncotracer.git oncotracer-src
python3 -m venv oncotracer-env
oncotracer-env/bin/python -m pip install -e ./oncotracer-src
source oncotracer-env/bin/activate
oncotracer --help
```

Keep both folders: the editable installation links the launcher to its source.
Keep results elsewhere and leave the source unchanged.

## 2. Choose an analysis backend

Replace the project path with the location where you plan to keep your analysis:

```bash
oncotracer system --path /absolute/path/to/my-study
```

### Conda

```bash
oncotracer install --conda
oncotracer doctor --backend conda
oncotracer setup --backend conda
```

Terminal setup alternative to the last command:

```bash
oncotracer setup --terminal --backend conda
```

The install command creates five isolated core environments: alignment, QDNAseq, ichorCNA,
classifier/reporting and GISTIC. Optional `variants`, `ffperase` and `strelka2`
environments are installed separately; follow [variant setup](variants.md#conda-and-docker).

### Docker

For repaired GLIMPSE2/FFPE, [build the current branch image](containers.md#fastq-to-cna-and-variants)
with both provenance arguments, then install it with `--no-pull`.
The published image below predates those repairs:

```bash
oncotracer install --docker --image carlosfarkas/oncotracer:fastq-variants-20260922
oncotracer doctor --backend docker --image carlosfarkas/oncotracer:fastq-variants-20260922
oncotracer setup --backend docker --image carlosfarkas/oncotracer:fastq-variants-20260922
```

Terminal setup alternative with the same image:

```bash
oncotracer setup --terminal --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260922
```

Enable **Add small-variant calling** in the browser or append `--variants`.
See [Docker variants](variants.md#run-cna-and-variants-with-docker) for models,
FFPERASE licensing and ANNOVAR. The older `ghcr.io/cfarkas/oncotracer:2.1.0`
image supports CNA. For methylation, use [Conda](configuration/methylation.md).

Installation flags: `--verbose` logs packages; `--json` supports automation.
See [backends](containers.md) and [advanced installation](installation_details.md).

## 3. Start a project

Setup opens **127.0.0.1:8888**; use the complete printed URL and keep the terminal
open. Select FASTQs, assign samples to Normal or Cancer, review settings and
choose a project folder. Click **Save configuration and check**, then **Run analysis**.
See the [setup guide](setup.md) or [QuickStart 1](quick_start.md).

The terminal alternatives collect settings through prompts; finish with **run**,
or **save** to run later. Add `--variant-specimen-type fresh` or `ffpe` to record
preservation for CNA-only setup. For scripts and SSH, see
[terminal and headless servers](headless.md). Resume a saved project with:

```bash
oncotracer setup --project /absolute/path/to/my-study --run
```

To check and run a Conda project separately:

```bash
oncotracer check --config /absolute/path/to/my-study/config/run.yml
oncotracer run --backend conda --config /absolute/path/to/my-study/config/run.yml
```

For Docker, replace `--backend conda` with `--backend docker` in the run command.
The browser Run button and `setup --project PATH --run` reuse the saved backend
and image. Runs download prebuilt hg38
indexes by default; reuse or build alternatives are covered in [genome indexes](reference_indexes.md).

## In a new terminal

Activate the same environment, using the location you chose during installation:

```bash
source /absolute/path/to/oncotracer-env/bin/activate
oncotracer --help
```

## Uninstall

Preview managed Conda removal with `oncotracer uninstall --conda --dry-run`.
Follow [uninstall](uninstall.md) to remove Conda tools, the Docker image or the
launcher while preserving project data and results.
