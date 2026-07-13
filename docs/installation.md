# Installation

OncoTracer itself does not require a manual software stack. Install the four prerequisites below, then clone the pipeline. All analysis programs are supplied by the maintained container.

## Requirements

| Requirement | Check | Official installation |
| --- | --- | --- |
| Git | `git --version` | [Install Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) |
| Java 17 or newer | `java -version` | [Nextflow Java requirements](https://www.nextflow.io/docs/latest/install.html#requirements) |
| Nextflow | `nextflow -version` | [Install Nextflow](https://www.nextflow.io/docs/latest/install.html) |
| Docker | `docker --version` | [Install Docker Engine](https://docs.docker.com/engine/install/) |

On an HPC system, use [Apptainer](https://apptainer.org/docs/admin/main/installation.html) instead of Docker and replace `--docker` with `--singularity`.

## Install OncoTracer

```bash
git clone https://github.com/cfarkas/oncotracer.git  # download the pipeline
cd oncotracer                                        # enter it; run main.nf from here
bash run_test.sh --docker                            # install the local Nextflow launcher if missing, pull/update the container, reuse or download test data, and test both branches
```

`run_test.sh` does not install Java, Git, or Docker because those require operating-system permissions. It checks them and gives a clear error. It downloads Nextflow locally only when `nextflow` is unavailable. Docker reuses unchanged image layers, and the data preparation step reuses every FASTQ that already exists and passes gzip validation.
