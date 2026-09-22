# Install OncoTracer

Install the OncoTracer command, then choose **Conda or Docker** for analysis tools.
Keep software and analysis projects in separate folders.

## Requirements

Both routes need 64-bit Linux, Python 3.10–3.13 and Git for the launcher.
The Conda route also needs [Conda/Miniforge](https://github.com/conda-forge/miniforge#install).
The Docker route needs a running Docker engine accessible to your user; host
Conda is unnecessary. OncoTracer does not install either backend itself.

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

Run these commands in a directory where you keep software:

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
classifier/reporting and GISTIC. The optional `variants` and `ffperase`
environments are installed separately; follow [variant setup](variants.md#conda-and-docker).

### Docker

```bash
oncotracer install --docker --image carlosfarkas/oncotracer:fastq-variants-20260921
oncotracer doctor --backend docker --image carlosfarkas/oncotracer:fastq-variants-20260921
oncotracer setup --backend docker --image carlosfarkas/oncotracer:fastq-variants-20260921
```

Terminal setup alternative with the same image:

```bash
oncotracer setup --terminal --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260921
```

This published Linux/amd64 image supports Illumina/ONT FASTQs through CNA and
optional variants. Enable **Add small-variant calling** in the browser, or append
`--variants` to setup. Chemistry-matched Clair3 models, FFPERASE source/models and
licensed ANNOVAR resources remain external; see [Docker variants](variants.md#run-cna-and-variants-with-docker).

Use this explicit tag for variants. The older `ghcr.io/cfarkas/oncotracer:2.1.0`
image supports the earlier CNA workflow. Docker methylation is unavailable;
use the [native methylation setup](configuration/methylation.md).

Add `--verbose` to installation for package output, or `--json` for automation.
See [execution backends](containers.md) and [advanced installation](installation_details.md).

## 3. Start a project

Setup opens **127.0.0.1:8888**; use the complete printed URL and keep the terminal
open. Select FASTQs, assign samples to Normal or Cancer, review settings and
choose a project folder. Click **Save configuration and check**, then **Run analysis**.
See the [setup guide](setup.md) or [QuickStart 1](quick_start.md).

The terminal alternatives ask the same configuration questions; finish with
**run**, or **save** to run later. For scripts and SSH, see
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
