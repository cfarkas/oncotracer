# Installation

OncoTracer runs on Linux. Nextflow runs on the host and starts the analysis software with Docker or Singularity/Apptainer.

The maintained Docker image is [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).

## 1. Install the host programs

| Program | Purpose |
| --- | --- |
| Git | Clone and update the repository |
| Java 17 or newer | Run Nextflow |
| Nextflow | Orchestrate the workflow |
| Docker Engine | Recommended runtime on a Linux workstation or server |
| Singularity or Apptainer | Recommended runtime on HPC where Docker is unavailable |
| Python 3, samtools, BWA, minimap2, pigz, curl or wget | Host-side input and reference preparation |

Use the official installation instructions for [Nextflow](https://www.nextflow.io/docs/latest/install.html), [Docker Engine](https://docs.docker.com/engine/install/), or [Apptainer](https://apptainer.org/docs/admin/main/installation.html). Ask the system administrator when installation or permissions require elevated access.

## 2. Check the installation

```bash
# Confirm Git, Java, and Nextflow.
git --version
java -version
nextflow -version

# Confirm the Docker launcher on a workstation or server.
command -v docker

# Confirm either Singularity or Apptainer on HPC.
command -v singularity
command -v apptainer

# Confirm the host-side sequence tools.
python3 --version
samtools --version
bwa 2>&1 | head -2
minimap2 --version
pigz --version
```

Only one container runtime is required. A workstation normally uses Docker; an HPC system normally provides Singularity or Apptainer.

## 3. Clone OncoTracer

```bash
# Clone the repository into an example directory.
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer

# Enter the repository and confirm that main.nf is present.
cd /home/student/oncotracer
pwd
ls main.nf
```

Replace `/home/student/oncotracer` with another absolute path when needed.

## 4. Prepare Docker or Singularity

Choose one of the following commands.

```bash
# Prepare and test the Docker runtime.
nextflow run /home/student/oncotracer/main.nf --install --docker \
  --lpwgs_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/install_docker

# Read the recorded Docker image and runtime information.
cat /home/student/oncotracer/.oncotracer/install/install_manifest.txt
```

```bash
# Prepare and test Singularity or Apptainer on HPC.
nextflow run /home/student/oncotracer/main.nf --install --singularity \
  --lpwgs_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/install_singularity

# Read the recorded image and runtime information.
cat /home/student/oncotracer/.oncotracer/install/install_manifest.txt
```

`--docker` and `--singularity` tell Nextflow which runtime to use. The installation command checks the selected runtime, prepares the image and SAMURAI cache, writes `install_manifest.txt`, and stops without analyzing reads.

## 5. Estimated time for the first analysis

The first uncached analysis downloads the hg38 reference, approximately **3.16 GB**, and builds its BWA index. Indexing commonly takes **30–60 minutes**. The pinned BWA task requests 72 GB, so use at least **80 GiB of addressable RAM**. Later runs reuse a valid reference and index.

Also reserve space for the container image, FASTQs, BAMs, the Nextflow `work/` directory, and final results.

## 6. Choose the first run

- [QuickStart Example 1](quick_start.md): one public Illumina and one public ONT sample.
- [Automatic Setup](auto_params.md): generate a YAML for your own FASTQs.
- [QuickStart Example 2](public_cohort.md): three public HCC1143 libraries.
- [Full Tutorial](full_tutorial.md): all 12 public PRJNA754199 libraries.
- [Other Example Run](six_tumor_four_control.md): six tumors and four controls using **user-provided** FASTQs that are not included in the repository.
