# HCC1143 six-FASTQ public example

This example uses three paired-end HCC1143 low-pass whole-genome sequencing libraries—six FASTQ files—from public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with [Ben-David et al., *Nature Communications* (2018)](https://doi.org/10.1038/s41467-018-05729-w).

All rows use `TUMOR`. DMSO is a treatment control, not a normal genome. Tiny unpaired singleton files are excluded because this example requires one matched R1/R2 pair per sample.

Follow [QuickStart Example 2](https://cfarkas.github.io/oncotracer/public_cohort/) for the six download commands, MD5 checks, `gzip -t`, output checks, and resume instructions.

After downloading the FASTQs, create the exact sample table and run:

```bash
# Set the standard repository and reads paths.
REPO_DIR=/path/to/my/directory/oncotracer
READS_DIR="$REPO_DIR/test/public/hcc1143_lpwgs"
cd "$REPO_DIR"

# Create or replace the exact HCC1143 sample table.
cat > "$READS_DIR/samples.csv" <<'CSV'
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
CSV

# Generate the Illumina YAML and R1/R2 samplesheet without starting analysis.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$READS_DIR" \
  --sample_table "$READS_DIR/samples.csv" \
  --auto_config_dir "$REPO_DIR/test/configs/hcc1143_lpwgs" \
  --auto_outdir "$REPO_DIR/test/runs/hcc1143_lpwgs"

Choose one analysis method after Automatic Setup.

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

Plan for about 1.08 GiB of compressed reads, at least 40 GiB of free working space, 16 CPU cores, and at least 80 GiB of addressable RAM. The first uncached run also downloads hg38 and creates a BWA index.

See [`manifest.tsv`](manifest.tsv) for URLs and provenance and [`checksums.md5`](checksums.md5) for the six expected MD5 values.
