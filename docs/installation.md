# Installation

OncoTracer runs on Linux. Nextflow runs on the host and starts the selected Docker or Singularity/Apptainer image.

The maintained image is [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer). With Singularity or Apptainer, the same image is referenced as `docker://carlosfarkas/oncotracer:latest`.

## 1. Install the host prerequisites

Use Linux with the following programs:

| Requirement | Purpose | Installation |
| --- | --- | --- |
| Git | Clone and update OncoTracer | [Install Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) |
| Java 17 or newer | Run Nextflow | [Install Eclipse Temurin 17](https://adoptium.net/temurin/releases/?version=17) |
| Nextflow | Run the workflow | [Install Nextflow](https://www.nextflow.io/docs/latest/install.html) |
| Python 3 | Run helper and verification scripts | [Install Python](https://www.python.org/downloads/) |
| samtools | Prepare and inspect sequence alignments | [Install samtools/HTSlib](https://www.htslib.org/download/) |
| BWA | Illumina alignment and reference indexing | [Install BWA](https://github.com/lh3/bwa) |
| minimap2 | ONT alignment | [Install minimap2](https://github.com/lh3/minimap2) |
| pigz | Parallel gzip support | [Install pigz](https://zlib.net/pigz/) |
| curl or wget | Download public reads and references | [Install curl](https://curl.se/download.html) or [install wget](https://www.gnu.org/software/wget/) |
| Docker Engine | Container runtime for a Linux workstation or server | [Install Docker Engine](https://docs.docker.com/engine/install/) |
| SingularityCE or Apptainer | Container runtime commonly provided on HPC | [Install SingularityCE](https://docs.sylabs.io/guides/latest/admin-guide/installation.html) or [install Apptainer](https://apptainer.org/docs/admin/main/installation.html) |

Only one container runtime is required: normally Docker on a workstation/server or Singularity/Apptainer on HPC. Ask the system administrator when installation or permissions require elevated access.

## 2. Verify the installation

```bash
# Confirm Git, Java, Nextflow, Python, and the sequence tools.
git --version
java -version
nextflow -version
python3 --version
samtools --version
bwa 2>&1 | head -2
minimap2 --version
pigz --version

# Confirm curl or wget.
command -v curl
command -v wget

# Confirm Docker on a workstation or server.
command -v docker

# Confirm Singularity or Apptainer on HPC.
command -v singularity
command -v apptainer
```

Either `curl` or `wget` is sufficient. Either Docker or Singularity/Apptainer is sufficient.

## 3. Clone OncoTracer

Use `/path/to/my/directory/oncotracer` as the repository path in the documentation.

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Clone OncoTracer into that directory.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"

# Enter the repository and confirm that main.nf is present.
cd "$REPO_DIR"
pwd
ls main.nf
```

## 4. Prepare one runtime without starting an analysis

Choose Docker or Singularity/Apptainer.

### Docker

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Pull or reuse the Docker image and test the installed software.
nextflow run "$REPO_DIR/main.nf" --install --docker \
  --lpwgs_root "$REPO_DIR/test" \
  -work-dir "$REPO_DIR/test/work/install_docker"

# Record the selected runtime and image identity.
cat "$REPO_DIR/.oncotracer/install/install_manifest.txt"
```

### Singularity or Apptainer

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Pull or reuse the Singularity/Apptainer image and test the software.
nextflow run "$REPO_DIR/main.nf" --install --singularity \
  --lpwgs_root "$REPO_DIR/test" \
  -work-dir "$REPO_DIR/test/work/install_singularity"

# Record the selected runtime and image identity.
cat "$REPO_DIR/.oncotracer/install/install_manifest.txt"
```

The `--install` route checks the host tools, prepares the selected image, caches SAMURAI v1.4.0, writes the manifest, and stops. It does not download sequencing reads or hg38 and does not start an analysis.

## 5. Estimated time and resources

The first uncached Illumina or ONT analysis downloads the hg38 reference, approximately **3.16 GB**, and creates a BWA index. Indexing commonly takes **30–60 minutes**. The pinned BWA task requests 72 GB, so provide at least 80 GiB of addressable RAM.

Also allow space for the container image, compressed and uncompressed reads, the Nextflow `work/` directory, and final results. Later `-resume` runs reuse a valid reference index and unchanged completed tasks.

The outer Nextflow display can remain at `RUN_*_SAMURAI (0 of 1)` while nested SAMURAI tasks are active. This counter alone does not indicate a stalled run.

## 6. Choose the first run

- [QuickStart Example 1](quick_start.md): one public Illumina and one public ONT sample, about **225 MB** of reads.
- [Automatic Setup](auto_params.md): generate a YAML for your own FASTQ folder.
- [QuickStart Example 2](public_cohort.md): three public HCC1143 libraries, about **1.08 GiB** of reads.
- [Full Tutorial](full_tutorial.md): all 12 public PRJNA754199 libraries currently available from the archive.
- [Other Example Run: six tumors and four controls](six_tumor_four_control.md): a command template that requires the user to provide all 20 FASTQs; no data are included or downloaded.

QuickStart Example 1 is the recommended installation check.
