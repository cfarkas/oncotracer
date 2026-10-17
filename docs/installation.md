# Install OncoTracer

Install the command, then choose **Conda or Docker**. Keep software and projects separate.

## Requirements

Both routes need 64-bit Linux and Python 3.10–3.13 for the launcher.
The Conda route also needs [Conda/Miniforge](https://github.com/conda-forge/miniforge#install).
Docker must be running and accessible to your user; host Conda is unnecessary.

Desktop browsing needs Zenity (Ubuntu/GNOME) or KDialog (KDE). Install either,
or enter paths. Remote sessions use server paths.

For small runs with 2–4 threads, plan for:

| Analysis | Available RAM to plan for |
| --- | --- |
| Illumina copy-number analysis | 16 GiB |
| ONT copy-number analysis | 24 GiB |
| Methylation classifiers or report LLMs | Depends on the model; checked separately |

Leave at least 60 GiB free for tools, references and a small run, plus FASTQs,
BAMs and temporary files. Docker extraction and larger datasets need more.

## 1. Install the command

Install the application and bundled workflows in your Python environment:

```bash
pip install oncotracer
oncotracer --help
```

No Git checkout is needed. For a separate Python environment, or when system
Python requires one:

```bash
python3 -m venv oncotracer-env
source oncotracer-env/bin/activate
python -m pip install oncotracer
```

Keep that environment. Scientific tools are installed and checked separately below.
Development builds use the [GitHub source route](installation_details.md#install-from-github).

## 2. Choose an analysis backend

Replace the project path with the location where you plan to keep your analysis:

```bash
oncotracer system --path /absolute/path/to/my-study
```

### Conda

```bash
oncotracer install --conda
oncotracer doctor --backend conda
oncotracer --interactive --backend conda
```

Terminal setup alternative to the last command:

```bash
oncotracer setup --terminal --backend conda
```

Installation creates isolated alignment, QDNAseq, ichorCNA, classifier/reporting,
GISTIC and verified Illumina variant environments. Setup selects tools automatically. FFPErase, Strelka2 and ONT resources remain separate; see
[variant setup](variants_reference.md#conda-and-docker).

### Docker

The current image includes GLIMPSE2/FFPE repairs and supports concurrent projects:

```bash
oncotracer install --docker --image carlosfarkas/oncotracer:fastq-variants-20261017
oncotracer doctor --backend docker --image carlosfarkas/oncotracer:fastq-variants-20261017
oncotracer --interactive --backend docker
```

Terminal setup alternative with the same image:

```bash
oncotracer setup --terminal --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20261017
```

Enable **Add small-variant calling** in the browser or append `--variants`.
See [Docker variants](variants.md#run-cna-and-variants-with-docker) for models,
FFPERASE licensing and ANNOVAR. The older `ghcr.io/cfarkas/oncotracer:2.1.0`
image supports CNA. For methylation, use [Conda](configuration/methylation.md).

Backend installation creates `~/.local/bin/oncotracer` when needed and preserves
an existing pip-owned command; `--no-launcher` opts out.
`--verbose` logs packages; `--json` supports automation. See [advanced installation](installation_details.md).

## 3. Start a project

Use setup’s complete printed URL (normally **127.0.0.1:8888**) and keep its terminal
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

Run from any directory, without activating the Python environment:

```bash
oncotracer --interactive --backend conda
```

Choose platform and **Fresh** or **FFPE** in the browser. If `~/.local/bin` is
missing from `PATH`, follow the installer’s printed PATH instruction.

## Uninstall

Preview managed Conda removal with `oncotracer uninstall --conda --dry-run`.
Matching backend removal also removes its additional global command. Use
`oncotracer uninstall --global-launcher --yes` for command-only removal;
pip-owned commands remain until pip uninstalls them.
Then run `python -m pip uninstall oncotracer` in the Python environment used for
installation. See [uninstall](uninstall.md); project data and results remain.
