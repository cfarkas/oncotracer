# HCC1143 six-FASTQ public example

This example runs three paired-end HCC1143 low-pass whole-genome sequencing libraries from public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331). It starts from a fresh OncoTracer clone and requires editing only `/path/to/my/directory/oncotracer`.

All rows use `TUMOR`. DMSO is a treatment control, not a normal genome.

## 1. Clone OncoTracer

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Clone OncoTracer and create the reads directory.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"
cd "$REPO_DIR"
READS_DIR="$REPO_DIR/test/public/hcc1143_lpwgs"
mkdir -p "$READS_DIR"
```

## 2. Download the six FASTQs

```bash
# Set the repository and HCC1143 reads paths.
REPO_DIR=/path/to/my/directory/oncotracer
READS_DIR="$REPO_DIR/test/public/hcc1143_lpwgs"
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
# Set the repository and HCC1143 reads paths.
REPO_DIR=/path/to/my/directory/oncotracer
READS_DIR="$REPO_DIR/test/public/hcc1143_lpwgs"
cd "$READS_DIR"

# Verify all six checksums and compressed FASTQs.
md5sum -c "$REPO_DIR/examples/hcc1143_lpwgs/checksums.md5"
gzip -t HCC1143_DMSO_R1.fastq.gz HCC1143_DMSO_R2.fastq.gz \
  HCC1143_BEZ235_R1.fastq.gz HCC1143_BEZ235_R2.fastq.gz \
  HCC1143_TRAMETINIB_R1.fastq.gz HCC1143_TRAMETINIB_R2.fastq.gz

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

`md5sum` should print `OK` six times. `gzip -t` is silent when every file is valid.

## 4. Generate the configuration

```bash
# Set the repository and reads paths and enter the clone.
REPO_DIR=/path/to/my/directory/oncotracer
READS_DIR="$REPO_DIR/test/public/hcc1143_lpwgs"
cd "$REPO_DIR"

# Generate the paired-end samplesheet and YAML without starting analysis.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$READS_DIR" \
  --sample_table "$READS_DIR/samples.csv" \
  --auto_config_dir "$REPO_DIR/test/configs/hcc1143_lpwgs" \
  --auto_outdir "$REPO_DIR/test/runs/hcc1143_lpwgs"
```

## 5. Choose one execution method

### Docker

```bash
# Run or resume HCC1143 with Docker.
REPO_DIR=/path/to/my/directory/oncotracer
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "$REPO_DIR/test/work/hcc1143_lpwgs-docker" \
  -resume
```

### Singularity or Apptainer

```bash
# Run or resume HCC1143 through Singularity or Apptainer.
REPO_DIR=/path/to/my/directory/oncotracer
nextflow run "$REPO_DIR/main.nf" --singularity \
  -params-file "$REPO_DIR/test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "$REPO_DIR/test/work/hcc1143_lpwgs-singularity" \
  -resume
```

### Poetry launcher

```bash
# Install the launcher and run HCC1143 through Poetry with Docker.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"
poetry install --no-interaction
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file "$REPO_DIR/test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "$REPO_DIR/test/work/hcc1143_lpwgs-poetry" \
  -resume
```

### Conda

```bash
# Run or resume HCC1143 with native Conda environments.
REPO_DIR=/path/to/my/directory/oncotracer
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$REPO_DIR/test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "$REPO_DIR/test/work/hcc1143_lpwgs-conda" \
  -resume
```

## 6. Check the completed outputs

```bash
# Set the repository and result paths.
REPO_DIR=/path/to/my/directory/oncotracer
OUT="$REPO_DIR/test/runs/hcc1143_lpwgs"

# List the aligned BAMs and confirm all three samples in qDNAseq output.
find "$OUT/01_samurai_illumina/alignment" -maxdepth 1 -type f -name '*.bam' -print
grep -F HCC1143_DMSO "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"
grep -F HCC1143_BEZ235 "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"
grep -F HCC1143_TRAMETINIB "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"

# Read the final workflow summary.
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

Plan for about 1.08 GiB of compressed reads, at least 40 GiB of free working space, and at least 80 GiB of addressable RAM. See [`manifest.tsv`](manifest.tsv) for provenance and [`checksums.md5`](checksums.md5) for the expected MD5 values.
