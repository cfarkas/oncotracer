# Installation

OncoTracer runs on Linux. Nextflow runs on the host and starts the selected Docker or Singularity/Apptainer image.

## 1. Install the host prerequisites

| Requirement | Purpose | Official instructions |
| --- | --- | --- |
| Git | Clone and update OncoTracer | [Install Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) |
| Java 17 or newer | Run Nextflow | [Nextflow Java requirements](https://www.nextflow.io/docs/latest/install.html#requirements) |
| Nextflow | Run the workflow | [Install Nextflow](https://www.nextflow.io/docs/latest/install.html) |
| Docker Engine | Container runtime for a Linux workstation/server | [Install Docker Engine](https://docs.docker.com/engine/install/) |
| Singularity or Apptainer | Container runtime commonly provided on HPC | [Install Apptainer](https://apptainer.org/docs/admin/main/installation.html) |
| Python 3, samtools, BWA, minimap2, pigz, curl or wget | Host-side input and reference helpers | Install from the operating system or a shared bioinformatics environment |

The maintained Docker image is [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer). Singularity/Apptainer uses the same image as `docker://carlosfarkas/oncotracer:latest`.

## 2. Verify the installation

For a Docker workstation:

```bash
# Confirm the host programs required by OncoTracer.
git --version
java -version
nextflow -version
command -v docker
python3 --version
samtools --version
bwa 2>&1 | head -2
minimap2 --version
pigz --version
```

For an HPC system, check the runtime supplied by the cluster:

```bash
# Confirm that either Singularity or Apptainer is available.
command -v singularity
command -v apptainer
```

One of the two HPC checks should print a path. Ask the system administrator when a required program is missing.

## 3. Clone OncoTracer

```bash
# Clone the repository into a new oncotracer directory.
git clone https://github.com/cfarkas/oncotracer.git

# Enter the repository and confirm that main.nf is present.
cd oncotracer
pwd
ls main.nf
```

Run the tutorial commands from the directory containing `main.nf`.

## 4. Prepare one runtime without starting an analysis

Choose one option.

### Docker

```bash
# Use the repository as the initial cache and data root.
ROOT="$(pwd)"

# Pull or reuse the Docker image and test the installed software.
nextflow run main.nf --install --docker \
  --lpwgs_root "$ROOT"

# Record the selected runtime and image identity.
cat .oncotracer/install/install_manifest.txt
```

### Singularity or Apptainer

```bash
# Use the repository as the initial cache and data root.
ROOT="$(pwd)"

# Pull or reuse the Singularity/Apptainer image and test the software.
nextflow run main.nf --install --singularity \
  --lpwgs_root "$ROOT"

# Record the selected runtime and image identity.
cat .oncotracer/install/install_manifest.txt
```

The `--install` route checks the host tools, prepares the selected image, caches SAMURAI v1.4.0, writes the manifest, and stops. It does not download sequencing reads or hg38 and does not start an analysis.

Where containers are unavailable, use `--conda` instead of `--docker` or `--singularity`.

## 5. Estimated time and resources

The first uncached Illumina or ONT analysis downloads the hg38 reference (about **3.16 GB**) and creates a BWA index. Indexing commonly takes **30–60 minutes**. The pinned BWA task requests 72 GB, so provide at least 80 GiB of addressable RAM.

Also allow space for the container image, compressed and uncompressed reads, the Nextflow `work/` directory, and final results. Later `-resume` runs reuse a valid reference index and unchanged completed tasks.

The outer Nextflow display can remain at `RUN_*_SAMURAI (0 of 1)` while the nested SAMURAI tasks are active. This counter alone does not indicate a stalled run.

## 6. Choose the first run

- [QuickStart Example 1](quick_start.md): one public Illumina and one public ONT sample, about **225 MB** of reads.
- [Automatic Setup](auto_params.md): generate a YAML for your own FASTQ folder.
- [QuickStart Example 2](public_cohort.md): three public HCC1143 libraries, about **1.08 GiB** of reads.
- [Full Tutorial](full_tutorial.md): all 12 public PRJNA754199 libraries currently available from the archive.
- [Other Example Run: six tumors and four controls](six_tumor_four_control.md): a command template that requires the user to provide all 20 FASTQs; no data are included or downloaded.

QuickStart Example 1 is the recommended installation check.
