# PRJNA754199 complete public-archive example

> **Legacy v1.1 command record.** This command sequence is retained for historical reproduction of the immutable Nextflow release. New analyses should use the native v2 executable and [Automatic Setup](https://cfarkas.github.io/oncotracer/auto_params/).

This example downloads and processes the 12 Illumina HiSeq 2500 single-end plasma cfDNA libraries currently available from [NCBI BioProject PRJNA754199](https://www.ncbi.nlm.nih.gov/bioproject/754199). The archive contains 266,097,582 reads and about **5.75 GiB** of compressed FASTQs.

The associated publication is Przybyl et al., *PLOS ONE* (2022), [doi:10.1371/journal.pone.0262272](https://doi.org/10.1371/journal.pone.0262272).

## Archive scope

The publication describes 41 plasma specimens, but the ENA read-run report returned 12 public runs on 15 July 2026. This example processes those 12 runs, not every specimen described in the article.

`DDLPS_*` and `WDLPS_*` are archive aliases and are not independently verified diagnoses. See [`manifest.tsv`](manifest.tsv) for accessions, byte counts, checksums, and FASTQ URLs, and [`PROVENANCE.md`](PROVENANCE.md) for archive notes.

## Requirements

Use Linux with Git, Java 17 or newer, Nextflow, Python 3, samtools, BWA, minimap2, pigz, curl or wget, and one launch method: Docker, Singularity/Apptainer, Poetry, or Conda. See the linked installation table in the [Installation guide](https://cfarkas.github.io/oncotracer/installation/). Plan for at least 150 GiB of free working space, 16 CPU cores, and 80 GiB of addressable RAM.

Docker uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer). Singularity/Apptainer uses `docker://carlosfarkas/oncotracer:latest`.

## Run the public archive

The [Full Tutorial](https://cfarkas.github.io/oncotracer/full_tutorial/) explains each step and output.

```bash
# Clone OncoTracer and enter the repository.
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer

# Download or reuse all 12 FASTQs and verify size, MD5, and gzip integrity.
nextflow run main.nf --make_prjna754199 \
  --test_root "test" \
  -work-dir "test/work/prjna754199_download"

# Create or replace the exact 12-sample table.
cat > "test/public/prjna754199/samples.csv" <<'CSV'
sample_name,status
DDLPS_1a,TUMOR
DDLPS_1b,TUMOR
DDLPS_1c,TUMOR
DDLPS_2,TUMOR
DDLPS_3a,TUMOR
DDLPS_3b,TUMOR
WDLPS_1a,TUMOR
WDLPS_1b,TUMOR
WDLPS_1c,TUMOR
WDLPS_1d,TUMOR
WDLPS_2,TUMOR
WDLPS_3,TUMOR
CSV

# Generate the 12-sample YAML, samplesheet, and manifest automatically.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "test/public/prjna754199" \
  --sample_table "test/public/prjna754199/samples.csv" \
  --auto_config_dir "test/configs/prjna754199" \
  --auto_outdir "test/runs/prjna754199" \
  --run_cna_classifier true \
  --cna_classifier_sample_set sarcoma \
  --pathology_use_biomed_models false \
  -work-dir "test/work/prjna754199_auto_params"

# Optionally check the generated workflow connections with Docker.
nextflow run main.nf -stub-run --docker \
  -params-file "test/configs/prjna754199/illumina.auto.yml" \
  -work-dir "test/work/prjna754199_stub"
```

Choose one analysis method.

### Docker

```bash
# Run or resume the complete archive with Docker.
nextflow run main.nf --docker \
  -params-file "test/configs/prjna754199/illumina.auto.yml" \
  -work-dir "test/work/prjna754199-docker" \
  -resume
```

### Singularity or Apptainer

```bash
# Run or resume the complete archive through Singularity or Apptainer.
nextflow run main.nf --singularity \
  -params-file "test/configs/prjna754199/illumina.auto.yml" \
  -work-dir "test/work/prjna754199-singularity" \
  -resume
```

### Poetry launcher

```bash
# Install the launcher and run the archive through Poetry with Docker.
poetry install --no-interaction
poetry run oncotracer --repo-dir . --backend docker \
  -params-file "test/configs/prjna754199/illumina.auto.yml" \
  -work-dir "test/work/prjna754199-poetry" \
  -resume
```

### Conda

```bash
# Run or resume the complete archive with native Conda environments.
nextflow run main.nf --conda \
  -params-file "test/configs/prjna754199/illumina.auto.yml" \
  -work-dir "test/work/prjna754199-conda" \
  -resume
```

```bash
# Verify the exact 12 samples and required output groups.
python3 "examples/prjna754199/verify_outputs.py" \
  --outdir "test/runs/prjna754199"
```

A successful verifier ends with:

```text
SUCCESS: complete PRJNA754199 tutorial outputs are verified.
```

## Generated layout

```text
/path/to/my/directory/oncotracer/test/
├── public/prjna754199/
│   ├── DDLPS_1a.fastq.gz
│   ├── ...
│   ├── WDLPS_3.fastq.gz
│   ├── manifest.tsv
│   └── samples.csv
├── configs/prjna754199/
│   ├── auto_params_manifest.tsv
│   ├── illumina.samplesheet.csv
│   └── illumina.auto.yml
├── references/samurai_hg38/
├── work/
└── runs/prjna754199/
```

Downloaded reads, BAMs, references, work directories, and complete outputs are ignored by Git.

## Method limitation

The publication used a GRCh37/hg19 Plasma-Seq method with a healthy-donor reference. This example uses hg38, SAMURAI/qDNAseq at 100 kb, BAM-supported boundary refinement, CNA tables, plots, and optional CNA-only research reports. Results are not directly interchangeable with the published calls.

No pathology table is supplied. Classifier labels and gene-region flags are research hypotheses and require orthogonal validation.
