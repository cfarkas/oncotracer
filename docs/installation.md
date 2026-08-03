# Installation

OncoTracer runs on Linux. Nextflow can create the required Conda environments automatically or run the workflow with Docker or Singularity/Apptainer.

Use one launch method:

- **Docker:** `nextflow run ... --docker` uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).
- **Singularity or Apptainer:** `nextflow run ... --singularity` uses `docker://carlosfarkas/oncotracer:latest`.
- **Poetry launcher:** `poetry run oncotracer --backend docker ...` manages the Python launcher and delegates the scientific execution to the selected backend.
- **Conda:** `nextflow run ... --conda` creates and reuses the required environments from the versioned definitions.

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
| Miniforge or Conda | Native environment manager used by `--conda` | [Install Miniforge](https://github.com/conda-forge/miniforge) |
| Docker Engine | Container runtime used by `--docker` | [Install Docker Engine](https://docs.docker.com/engine/install/) |
| SingularityCE or Apptainer | HPC runtime used by `--singularity` | [Install SingularityCE](https://docs.sylabs.io/guides/latest/admin-guide/installation.html) or [install Apptainer](https://apptainer.org/docs/admin/main/installation.html) |

Choose Miniforge/Conda, Docker, or Singularity/Apptainer. Only one of these execution environments is required. Ask the system administrator when installation or permissions require elevated access.

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

# Confirm Conda for --conda.
command -v conda
conda --version

# Confirm Docker for --docker.
command -v docker

# Confirm Singularity or Apptainer for --singularity.
command -v singularity
command -v apptainer
```

Either `curl` or `wget` is sufficient. Confirm only the execution environment that you plan to use.

## 3. Clone OncoTracer

```bash
# Clone OncoTracer into a given directory.

git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
```

## 4. Prepare one execution environment without starting an analysis

Choose one option.

### Conda

```bash
# Create or reuse the versioned Conda environment and test the software.
nextflow run main.nf --install --conda \
  --lpwgs_root "test" \
  -work-dir "test/work/install_conda"

# Record the selected environment and its explicit package specification hash.
cat ".oncotracer/install/install_manifest.txt"
```

On the first `--conda` run, Nextflow solves and creates the required environment automatically. It stores reusable environments below `lpwgs_root/.oncotracer/conda`.

### Docker

```bash
# Pull or reuse the Docker image and test the installed software.
nextflow run main.nf --install --docker \
  --lpwgs_root "test" \
  -work-dir "test/work/install_docker"

# Record the selected runtime and image identity.
cat ".oncotracer/install/install_manifest.txt"
```

### Singularity or Apptainer

```bash
# Pull or reuse the Singularity/Apptainer image and test the software.
nextflow run main.nf --install --singularity \
  --lpwgs_root "test" \
  -work-dir "test/work/install_singularity"

# Record the selected runtime and image identity.
cat ".oncotracer/install/install_manifest.txt"
```

The `--install` route checks the host tools, prepares the selected environment, caches SAMURAI v1.4.0, writes the manifest, and stops. It does not start an analysis.

## 5. Estimated time and resources

A first Conda run needs additional time and storage to solve and download the software environments. Later runs reuse the environments in the Conda cache.

The first uncached Illumina or ONT analysis also downloads the hg38 reference, approximately **3.16 GB**, and creates the required alignment index. Indexing commonly takes **30–60 minutes**. The pinned BWA task requests 72 GB, so provide at least **80 GiB of addressable RAM**.

Also allow space for Conda environments or container images, compressed and uncompressed reads, the Nextflow `work/` directory, and final results. Later `-resume` runs reuse a valid reference index and unchanged completed tasks.

The outer Nextflow display can remain at `RUN_*_SAMURAI (0 of 1)` while nested SAMURAI tasks are active. This counter alone does not indicate a stalled run.

## 6. Choose the first run

- [QuickStart Example 1](quick_start.md): one public Illumina and one public ONT sample, about **225 MB** of reads.
- [Automatic Setup](auto_params.md): generate a YAML for your own FASTQ folder.
- [QuickStart Example 2](public_cohort.md): three public HCC1143 libraries, about **1.08 GiB** of reads.
- [Full Tutorial](full_tutorial.md): all 12 public PRJNA754199 libraries currently available from the archive.
- [Other Example Run: six tumors and four controls](six_tumor_four_control.md): a mock example illustrating how four normal controls are used to correct six tumor profiles.

QuickStart Example 1 is the recommended installation check.

## Poetry launcher installation

```bash
# Install Poetry, enter the standard repository clone, and create its locked launcher environment.
poetry install --no-interaction
poetry run oncotracer --help
```

Poetry manages the Python launcher. Select `--backend docker`, `--backend singularity`, or `--backend conda` for the scientific runtime.
