# Install OncoTracer

Use 64-bit Linux and Python 3.10–3.13. Install the application, then choose **Conda or Docker** for analysis tools.

## Quick start

```bash
pip install oncotracer
```

No Git checkout is needed. If system Python requires a separate environment, use the [Python environment instructions](#python-environment) below.

### Conda

Install [Conda/Miniforge](https://github.com/conda-forge/miniforge#install) first, then:

```bash
oncotracer install --conda
oncotracer doctor --backend conda
oncotracer --interactive --backend conda
```

Installation prepares alignment, QDNAseq, ichorCNA, classifier/reporting, GISTIC and verified Illumina variant environments. Setup selects their tools automatically. FFPErase, Strelka2 and ONT resources need [additional setup](variants_reference.md#conda-and-docker).

### Docker

Docker must be running and accessible to your user. Host Conda is unnecessary:

```bash
oncotracer install --docker --image carlosfarkas/oncotracer:manual-samples-20261022
oncotracer doctor --backend docker
oncotracer --interactive --backend docker
```

Installation saves the selected image; doctor and setup reuse it. Select small variants in the browser when needed. See [Docker variants](variants.md#run-cna-and-variants-with-docker) for models, FFPERASE licensing and ANNOVAR. Use [Conda for ONT methylation](configuration/methylation.md).

## Start a project

Use the complete printed browser URL and keep its terminal open. Choose **Illumina or ONT**, then **Fresh or FFPE**. Select inputs, assign samples to Normal or Cancer, choose a project folder and review settings. Click **Save configuration and check**, then **Run analysis**.

For a named project:

```bash
oncotracer --interactive --backend conda --project /absolute/path/to/my-study
```

Use `--backend docker` for Docker. Separate terminals can run separate projects at the same time. Each session gets its own URL and available port; give each run a distinct project/output folder and a share of CPU and memory.

For terminal prompts, use `oncotracer setup --terminal --backend conda`. To reopen a saved project, use `oncotracer setup --project /absolute/path/to/my-study --run`. To check and run its saved configuration separately:

```bash
oncotracer check --config /absolute/path/to/my-study/config/run.yml
oncotracer run --backend conda --config /absolute/path/to/my-study/config/run.yml
```

Runs use prebuilt hg38 indexes by default. See [genome indexes](reference_indexes.md), the [setup guide](setup.md), or [terminal and SSH instructions](headless.md).

## Nextflow entry

If you have Nextflow and an [OncoTracer source checkout](installation_details.md#install-from-github), supply its full path from any directory:

```bash
nextflow run /absolute/path/to/oncotracer-src/main.nf --interactive --backend conda
```

Use `--backend docker` for Docker. See [Nextflow setup](nextflow_entry.md).

## Python environment

For a dedicated application environment:

```bash
python3 -m venv oncotracer-env
source oncotracer-env/bin/activate
python -m pip install oncotracer
```

Keep this environment. Backend installation creates an extra `~/.local/bin/oncotracer` command when needed and preserves a pip-owned command already there. Follow the installer’s PATH instruction if necessary; `--no-launcher` opts out. See [advanced installation](installation_details.md) for source, standalone, Poetry and Apptainer routes.

## Requirements

For small runs with 2–4 threads, plan for 16 GiB available RAM for Illumina copy-number analysis or 24 GiB for ONT. Models and larger runs need more. Leave at least 60 GiB free for tools, references and a small run, plus input reads, BAMs and temporary files; Docker extraction needs additional space. Check your intended storage:

```bash
oncotracer system --path /absolute/path/to/my-study
```

Desktop file browsing uses Zenity or KDialog; paths can also be entered manually. Remote sessions use server paths.

## Uninstall

Permanently remove managed Conda tools:

```bash
oncotracer uninstall --conda --yes --purge
```

Then remove the application with `python -m pip uninstall oncotracer` in its Python environment. Docker images and any remaining extra global launcher have separate removal steps. See [uninstall](uninstall.md) for the component commands and retained settings; projects and results remain.
