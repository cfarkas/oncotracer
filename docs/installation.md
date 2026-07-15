# Installation

OncoTracer runs on Linux. Most analysis software is inside the maintained container, while the stage-01 launch and reference helpers still use a small set of host commands.

## 1. Install the host prerequisites

| Requirement | Why OncoTracer needs it | Official instructions |
| --- | --- | --- |
| Git | Downloads and updates this repository. | [Install Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) |
| Java 17 or newer | Runs Nextflow. | [Nextflow Java requirements](https://www.nextflow.io/docs/latest/install.html#requirements) |
| Nextflow | Orchestrates every workflow step. | [Install Nextflow](https://www.nextflow.io/docs/latest/install.html) |
| Docker Engine | Runs the reproducible analysis container. | [Install Docker Engine](https://docs.docker.com/engine/install/) |
| Python 3 and curl or wget | Validate metadata and retrieve references. | Use the packages provided by your Linux distribution. |
| samtools, BWA, minimap2, and pigz | Prepare/index references and support the Illumina/ONT stage-01 launchers. | Install through your system package manager or a shared bioinformatics environment. |

These are host-level programs. OncoTracer cannot install Java, Git, Docker, or operating-system packages for you because their installation commonly requires administrator access. Ask your system administrator if any check below fails.

On an HPC cluster, your administrator may provide [Apptainer](https://apptainer.org/docs/admin/main/installation.html) instead. In that case, replace `--docker` with `--singularity` in the tutorials.

## 2. Verify the installation

Run this block in a terminal:

```bash
git --version                  # should print a Git version
java -version                  # should report Java 17 or newer
nextflow -version              # should print a Nextflow version
docker --version               # should print a Docker version
docker run --rm hello-world    # should finish successfully and confirm Docker can run a container
python3 --version              # should print a Python 3 version
samtools --version             # stage-01/reference helper
bwa 2>&1 | head -2            # Illumina alignment/index fallback
minimap2 --version             # ONT alignment helper
pigz --version                 # parallel gzip helper
```

A printed Docker version does not prove that your user can access the Docker service; the `hello-world` command checks that access. If it reports a permission error, follow Docker's [Linux post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/) or contact the administrator.

!!! note "Nextflow fallback in the test helper"
    `run_test.sh` downloads a local Nextflow launcher into the repository's `.tools/` directory if `nextflow` is not found. Java must still be installed. Installing Nextflow yourself first makes the setup easier to diagnose and lets you use `nextflow` outside this repository.

## 3. Clone OncoTracer

Choose a directory with enough free disk space, then run:

```bash
git clone https://github.com/cfarkas/oncotracer.git  # download the repository into a new oncotracer folder
cd oncotracer                                        # enter the repository
pwd                                                  # confirm the directory; main.nf should be here
ls main.nf                                           # confirm that the main workflow file is present
```

Run all tutorial `nextflow run main.nf ...` commands from this `oncotracer` directory.

## 4. Prepare one runtime without starting an analysis

Use `--install` to prepare and smoke-test one runtime before downloading patient reads. Choose exactly one runtime flag. For Docker:

```bash
ROOT="$(pwd)"                                                        # cloned OncoTracer repository
nextflow run main.nf --install --docker --lpwgs_root "$ROOT"        # pull/test software and cache SAMURAI
cat .oncotracer/install/install_manifest.txt                         # record the exact runtime identity
```

On an HPC system, replace `--docker` with `--singularity`. Where containers are unavailable, use `--conda`; Nextflow creates or reuses the environment below `lpwgs_root/.oncotracer/conda`.

The installation route:

- checks Java, Nextflow, the selected runtime, and host-side stage-01 tools;
- pulls or reuses the selected image/environment and runs an analysis-software smoke test;
- caches the pinned SAMURAI v1.4.0 workflow below `lpwgs_root/.oncotracer/nxf-assets`;
- writes `install_manifest.txt` and stops without requiring `mode`, inputs, or `outdir`.

It does **not** download sequencing reads or hg38, build reference indexes, or create analysis stages `01` through `06`. Those data-dependent operations begin only with a real workflow or public tutorial. Repeating the same install command is safe: existing container layers, Conda packages, and valid cached assets are reused. Use `--install_dir /absolute/path` only when the manifest must be published somewhere other than `.oncotracer/install`.

OncoTracer cannot install host-level Java, Docker, Apptainer, or operating-system permissions. The command fails with the missing program or daemon access problem so it can be corrected before a long run.

## 5. Plan time and disk space

!!! warning "Large one-time reference step"
    The first real Illumina or ONT analysis downloads the hg38 reference (about **3.16 GB**) and prepares its BWA index. BWA indexing is single-core and commonly takes **30–60 minutes**. The terminal can display an outer SAMURAI task at `0 of 1` while nested alignment and CNA work is active. Do not assume it is stalled from that counter alone.

Also allow space for the Docker image, uncompressed/intermediate files, the Nextflow `work/` directory, and final results. Completed reference and Nextflow work are reused by later `-resume` runs when inputs and commands have not changed.

## 6. Choose the first run

- [Quick verification](quick_start.md): about **225 MB of public reads**, one Illumina sample and one ONT sample.
- [Three-sample HCC1143 cohort](public_cohort.md): **1.08 GiB** of reads in six FASTQ files; use this after the quick verification.
- [Your own FASTQ folder](auto_params.md): automatically generate a samplesheet and YAML.

The quick verification is the recommended first run because it exercises both workflow branches with the smallest provided datasets.
