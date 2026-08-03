# HCC1143 six-FASTQ public example

This example runs three paired-end HCC1143 low-pass whole-genome sequencing libraries from public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331). It starts from a fresh OncoTracer clone and uses paths relative to the clone.

All rows use `TUMOR`. DMSO is a treatment control, not a normal genome.

## 1. Clone OncoTracer

```bash
# Clone OncoTracer and enter the repository.
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer

READS_DIR="$(pwd)/test/public/hcc1143_lpwgs"
mkdir -p "$READS_DIR"
```

## 2. Download the six FASTQs

```bash
# Set the HCC1143 reads path.
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

## 3. Validate the files and create the sample table

```bash
# Set the repository root and HCC1143 reads directory.
ROOT="$(pwd)"
READS_DIR="$ROOT/test/public/hcc1143_lpwgs"

# Verify checksums and gzip integrity without changing the parent shell.
(
  cd "$READS_DIR"
  md5sum -c "$ROOT/examples/hcc1143_lpwgs/checksums.md5"
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

`md5sum` should print `OK` six times. `gzip -t` is silent when every file is valid. The subshell returns to the `oncotracer` directory automatically.

## 4. Generate the configuration

```bash
# Set the HCC1143 reads path.
READS_DIR="$(pwd)/test/public/hcc1143_lpwgs"

# Generate the paired-end samplesheet and YAML without starting analysis.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$READS_DIR" \
  --sample_table "$READS_DIR/samples.csv" \
  --auto_config_dir "test/configs/hcc1143_lpwgs" \
  --auto_outdir "test/runs/hcc1143_lpwgs"
```

## 5. Choose one execution method

### Docker

```bash
# Run or resume HCC1143 with Docker.
nextflow run main.nf --docker \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs-docker" \
  -resume
```

### Singularity or Apptainer

```bash
# Run or resume HCC1143 through Singularity or Apptainer.
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
# Run or resume HCC1143 with native Conda environments.
nextflow run main.nf --conda \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs-conda" \
  -resume
```

## 6. Check the completed outputs

```bash
# Set the result path.
OUT="$(pwd)/test/runs/hcc1143_lpwgs"

# List the aligned BAMs and confirm all three samples in qDNAseq output.
find "$OUT/01_samurai_illumina/alignment" -maxdepth 1 -type f -name '*.bam' -print
grep -F HCC1143_DMSO "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"
grep -F HCC1143_BEZ235 "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"
grep -F HCC1143_TRAMETINIB "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"

# Read the final workflow summary.
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

Plan for about 1.08 GiB of compressed reads, at least 40 GiB of free working space, and at least 80 GiB of addressable RAM. See [`manifest.tsv`](manifest.tsv) for provenance and [`checksums.md5`](checksums.md5) for the expected MD5 values.
