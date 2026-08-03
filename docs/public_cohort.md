<a id="three-sample-hcc1143-public-cohort"></a>

# QuickStart Example 2: three-sample HCC1143 public cohort

This example downloads and analyzes three paired-end HCC1143 low-pass whole-genome sequencing libraries: six public FASTQ files totaling about **1.08 GiB**. Complete [QuickStart Example 1](quick_start.md) first.

Preparation and Automatic Setup are backend-independent. The analysis section provides explicit Docker, Singularity/Apptainer, Poetry, and Conda commands; choose one method.

This cohort is a software example. It is not a matched tumor/normal study and should not be used to infer treatment effects.

## Requirements

Use Linux with [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git), [Java 17](https://adoptium.net/temurin/releases/?version=17) or newer, [Nextflow](https://www.nextflow.io/docs/latest/install.html), [Miniforge or Conda](https://github.com/conda-forge/miniforge), [Python 3](https://www.python.org/downloads/), [samtools](https://www.htslib.org/download/), [BWA](https://github.com/lh3/bwa), [minimap2](https://github.com/lh3/minimap2), [pigz](https://zlib.net/pigz/), and [curl](https://curl.se/download.html) or [wget](https://www.gnu.org/software/wget/).

Plan for at least 40 GiB of free working space, 16 CPU cores, and 80 GiB of addressable RAM. The first Conda run creates the environments. The first uncached analysis also downloads hg38 and creates a BWA index, which can take tens of minutes before alignment begins.

## Public data

The libraries come from public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with [Ben-David et al., *Nature Communications* (2018)](https://doi.org/10.1038/s41467-018-05729-w).

| OncoTracer sample | Treatment | Run accession | Files used |
| --- | --- | --- | --- |
| `HCC1143_DMSO` | 0.05% DMSO | `SRR7085656` | paired R1/R2 FASTQs |
| `HCC1143_BEZ235` | 1 µM BEZ235 | `SRR7085655` | paired R1/R2 FASTQs |
| `HCC1143_TRAMETINIB` | 1 µM Trametinib | `SRR7085657` | paired R1/R2 FASTQs |

All rows are labeled `TUMOR`. DMSO is a treatment control, not a normal genome. Exact URLs, byte counts, and checksums are stored in [`examples/hcc1143_lpwgs/manifest.tsv`](https://github.com/cfarkas/oncotracer/blob/main/examples/hcc1143_lpwgs/manifest.tsv).

## 1. Clone OncoTracer

```bash
# Clone OncoTracer into a given directory.

git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
```

```bash
# Prepare the input files.
mkdir -p "test/public/hcc1143_lpwgs"
```

<a id="2-download-the-six-fastq-files"></a>

## 2. Download the six FASTQ files

Each command can continue a partial download because it uses `--continue-at -`.

```bash
# Set the standard repository and data paths.
READS_DIR="$(pwd)/test/public/hcc1143_lpwgs"
mkdir -p "$READS_DIR"

# Download HCC1143_DMSO read 1.
curl --fail --location --continue-at - \
  --output "$READS_DIR/HCC1143_DMSO_R1.fastq.gz" \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_1.fastq.gz

# Download HCC1143_DMSO read 2.
curl --fail --location --continue-at - \
  --output "$READS_DIR/HCC1143_DMSO_R2.fastq.gz" \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_2.fastq.gz

# Download HCC1143_BEZ235 read 1.
curl --fail --location --continue-at - \
  --output "$READS_DIR/HCC1143_BEZ235_R1.fastq.gz" \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_1.fastq.gz

# Download HCC1143_BEZ235 read 2.
curl --fail --location --continue-at - \
  --output "$READS_DIR/HCC1143_BEZ235_R2.fastq.gz" \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_2.fastq.gz

# Download HCC1143_TRAMETINIB read 1.
curl --fail --location --continue-at - \
  --output "$READS_DIR/HCC1143_TRAMETINIB_R1.fastq.gz" \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_1.fastq.gz

# Download HCC1143_TRAMETINIB read 2.
curl --fail --location --continue-at - \
  --output "$READS_DIR/HCC1143_TRAMETINIB_R2.fastq.gz" \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_2.fastq.gz
```

## 3. Verify the FASTQs and create `samples.csv`

Use the copy/paste-ready block below. The validation runs in a subshell, so the terminal remains inside the cloned `oncotracer` directory afterward.

```bash
# Validate the six FASTQs and create the sample table.
READS_DIR="$(pwd)/test/public/hcc1143_lpwgs"
CHECKSUMS="$(pwd)/examples/hcc1143_lpwgs/checksums.md5"

# Check the MD5 values and compressed files without changing the current shell directory.
(
  cd "$READS_DIR"
  md5sum -c "$CHECKSUMS"
  gzip -t HCC1143_DMSO_R1.fastq.gz HCC1143_DMSO_R2.fastq.gz \
    HCC1143_BEZ235_R1.fastq.gz HCC1143_BEZ235_R2.fastq.gz \
    HCC1143_TRAMETINIB_R1.fastq.gz HCC1143_TRAMETINIB_R2.fastq.gz
)

# Create or replace the exact HCC1143 sample table.
cat > "$READS_DIR/samples.csv" <<'CSV'
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
CSV

# Display the saved table.
cat "$READS_DIR/samples.csv"
```

`md5sum` should print `OK` six times. `gzip -t` is silent when the FASTQs are valid.

To edit manually instead, run:

```bash
# Open the sample table in Nano.
nano test/public/hcc1143_lpwgs/samples.csv
```

Paste the same four lines, save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

## 4. Generate the YAML automatically

`--auto_params` matches the three sample names to their R1/R2 files, validates the FASTQs, writes `illumina.samplesheet.csv`, and writes `illumina.auto.yml`. It does not start alignment or CNA calling.

```bash
# Generate the Illumina YAML and R1/R2 samplesheet without starting analysis.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "test/public/hcc1143_lpwgs" \
  --sample_table "test/public/hcc1143_lpwgs/samples.csv" \
  --auto_config_dir "test/configs/hcc1143_lpwgs" \
  --auto_outdir "test/runs/hcc1143_lpwgs"
```

```bash
# Display the generated YAML.
sed -n '1,120p' "test/configs/hcc1143_lpwgs/illumina.auto.yml"

# Display the generated R1/R2 samplesheet.
sed -n '1,10p' "test/configs/hcc1143_lpwgs/illumina.samplesheet.csv"

# Display sample counts and file hashes.
cat "test/configs/hcc1143_lpwgs/auto_params_manifest.tsv"
```

The generated samplesheet must contain three data rows, each with one R1 and one R2 path.

## 5. Optional wiring check

```bash
# Check the generated workflow connections without running the analysis tools.
nextflow run main.nf -stub-run --conda \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs_stub"
```

## 6. Run the analysis

Choose exactly one method. Each method reads the same generated YAML and writes the same result directory; route-specific work directories prevent cross-runtime cache mixing.

### Docker

```bash
# Run the generated HCC1143 configuration with Docker.
nextflow run main.nf --docker \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs-docker" \
  -resume
```

### Singularity or Apptainer

```bash
# Run the generated HCC1143 configuration through Singularity or Apptainer.
nextflow run main.nf --singularity \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs-singularity" \
  -resume
```

### Poetry launcher

```bash
# Install the launcher and run HCC1143 through Poetry with Docker.
poetry install --no-interaction
poetry run oncotracer --repo-dir . --backend docker \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs-poetry" \
  -resume
```

### Conda

```bash
# Run the generated HCC1143 configuration with native Conda environments.
nextflow run main.nf --conda \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs-conda" \
  -resume
```

Keep the terminal open until Nextflow returns to the prompt. To resume, repeat the command for the selected method with the same YAML, work directory, and `-resume`.

## 7. Check the outputs

```bash
# Set the standard repository and result paths.
OUT="$(pwd)/test/runs/hcc1143_lpwgs"

# List the three aligned BAM files.
find "$OUT/01_samurai_illumina/alignment" \
  -maxdepth 1 -type f -name '*.bam' -print

# Confirm that each sample appears in the qDNAseq segment table.
grep -Fq HCC1143_DMSO "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg" \
  && echo 'HCC1143_DMSO: found'
grep -Fq HCC1143_BEZ235 "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg" \
  && echo 'HCC1143_BEZ235: found'
grep -Fq HCC1143_TRAMETINIB "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg" \
  && echo 'HCC1143_TRAMETINIB: found'

# Display the workflow summary.
sed -n '1,40p' "$OUT/06_workflow_summary/workflow_summary.txt"
```

Important outputs include:

- `01_samurai_illumina/alignment/*.bam`;
- `01_samurai_illumina/qdnaseq/all_segments.seg`;
- `03_cna_codification/cna_events.tsv`;
- `04_cna_custom_plots/cna_per_sample_pages.pdf`;
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`;
- `06_workflow_summary/workflow_summary.txt`.

## Resume an interrupted run

Repeat the command for the method you selected in [Run the analysis](#6-run-the-analysis), using the same YAML, route-specific work directory, and `-resume`.

## Limitations

This example verifies multi-sample execution. It is not a matched tumor/normal design, does not establish treatment causality, and is not a clinical validation study.

The [Other Example Run: six tumors and four controls](six_tumor_four_control.md) is a mock example illustrating how four normal controls are used to build a local qDNAseq reference for six tumors.
