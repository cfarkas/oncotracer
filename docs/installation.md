# Installation

OncoTracer runs on Linux. The analysis software is inside the maintained container, but four programs must work on the host before you start: Git, Java, Nextflow, and Docker.

## 1. Install the host prerequisites

| Requirement | Why OncoTracer needs it | Official instructions |
| --- | --- | --- |
| Git | Downloads and updates this repository. | [Install Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) |
| Java 17 or newer | Runs Nextflow. | [Nextflow Java requirements](https://www.nextflow.io/docs/latest/install.html#requirements) |
| Nextflow | Orchestrates every workflow step. | [Install Nextflow](https://www.nextflow.io/docs/latest/install.html) |
| Docker Engine | Runs the reproducible analysis container. | [Install Docker Engine](https://docs.docker.com/engine/install/) |

These are host-level programs. OncoTracer cannot install Java, Git, or Docker for you because their installation commonly requires administrator access. Ask your system administrator if any check below fails.

On an HPC cluster, your administrator may provide [Apptainer](https://apptainer.org/docs/admin/main/installation.html) instead. In that case, replace `--docker` with `--singularity` in the tutorials.

## 2. Verify the installation

Run this block in a terminal:

```bash
git --version                  # should print a Git version
java -version                  # should report Java 17 or newer
nextflow -version              # should print a Nextflow version
docker --version               # should print a Docker version
docker run --rm hello-world    # should finish successfully and confirm Docker can run a container
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

## 4. Plan time and disk space

!!! warning "Large one-time reference step"
    The first real Illumina or ONT analysis downloads the hg38 reference (about **3.16 GB**) and prepares its BWA index. BWA indexing is single-core and commonly takes **30–60 minutes**. The terminal can display an outer SAMURAI task at `0 of 1` while nested alignment and CNA work is active. Do not assume it is stalled from that counter alone.

Also allow space for the Docker image, uncompressed/intermediate files, the Nextflow `work/` directory, and final results. Completed reference and Nextflow work are reused by later `-resume` runs when inputs and commands have not changed.

## 5. Choose the first run

- [Quick verification](quick_start.md): about **225 MB of public reads**, one Illumina sample and one ONT sample.
- [Three-sample HCC1143 cohort](public_cohort.md): **1.08 GiB** of reads in six FASTQ files; use this after the quick verification.
- [Your own FASTQ folder](auto_params.md): automatically generate a samplesheet and YAML.

The quick verification is the recommended first run because it exercises both workflow branches with the smallest provided datasets.
