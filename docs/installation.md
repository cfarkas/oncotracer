# Install OncoTracer

Use 64-bit Linux and Python 3.10–3.13. Install the application, then choose **Conda or Docker** for analysis tools.

## Quick start: current GitHub preview

```bash
python3 -m pip install --upgrade --force-reinstall "https://github.com/cfarkas/oncotracer/releases/download/v2.3.1-rc.2/oncotracer-2.3.1rc2-py3-none-any.whl"
```

No Git checkout is needed. This preview contains the GPU and experimental haplotype features. It is not a stable scientific-parity release: the historical frozen CNA comparison remains unresolved. `pip install oncotracer` installs the separately published PyPI version, which does not yet contain these preview features. If system Python requires a separate environment, use the [Python environment instructions](#python-environment) below.

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
oncotracer install --docker --image carlosfarkas/oncotracer:2.3.1-rc.2
oncotracer doctor --backend docker
oncotracer --interactive --backend docker
```

Installation saves the selected image; doctor and setup reuse it. Select small variants in the browser when needed. See [Docker variants](variants.md#run-cna-and-variants-with-docker) for models, FFPERASE licensing and ANNOVAR. Use [Conda for ONT methylation](configuration/methylation.md).

### Podman (3.0.0 candidate)

The 3.0.0 launcher offers Podman in CLI, browser and terminal setup, including
project continuation. Podman runs the same image and scientific environments as
Docker. The published 2.3.1 preview launcher does not provide this backend.

After building or loading a 3.0.0 candidate image into **Podman's** local image
store, select its tag explicitly:

```bash
oncotracer install --podman --image localhost/oncotracer:3.0.0-candidate --no-pull
oncotracer doctor --backend podman
oncotracer --interactive --backend podman
oncotracer setup --terminal --backend podman
oncotracer run --backend podman --image localhost/oncotracer:3.0.0-candidate --config /absolute/path/to/my-study/config/run.yml
```

Docker and Podman maintain separate image stores. Remote images should use their
full registry name, such as `docker.io/carlosfarkas/oncotracer:TAG`. The saved
`docker_image` configuration key is also used by Podman for compatibility.

Rootless runs use `--userns=keep-id` so generated files retain your UID/GID;
input mounts stay read-only. OncoTracer does not recursively change ownership or
relabel shared data. The invoking user must have access to the selected paths.
See [Podman's user namespace and volume documentation](https://docs.podman.io/en/v4.9.3/markdown/podman-run.1.html).

For NVIDIA GPUs, configure [NVIDIA CDI](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)
and validate with `oncotracer doctor --backend podman --gpu`. Podman receives
the selected host GPU indexes through CDI; UUID visibility masks and shared
native admission locks retain the selected device identities. CPU runs need no CDI. ONT methylation still requires native licensed tools.

## Optional NVIDIA GPU

Add `--gpu` to backend installation and `doctor` to prepare and test the compatible CUDA runtimes. Choose **GPU usage → Yes** in the browser. See [GPU setup, supported stages and memory limits](gpu.md).

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
python -m pip install --upgrade --force-reinstall "https://github.com/cfarkas/oncotracer/releases/download/v2.3.1-rc.2/oncotracer-2.3.1rc2-py3-none-any.whl"
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
