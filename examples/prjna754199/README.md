# PRJNA754199 public archive example

This public example downloads and processes the 12 Illumina HiSeq 2500 single-end plasma cfDNA libraries currently returned for [PRJNA754199](https://www.ncbi.nlm.nih.gov/bioproject/754199). The files contain 266,097,582 reads and occupy approximately 5.75 GiB compressed.

The associated publication is Przybyl et al., *Detection of MDM2 amplification by shallow whole genome sequencing of cell-free DNA of patients with dedifferentiated liposarcoma*, PLOS ONE (2022).

The publication describes more specimens than are available as public read runs. This example processes the complete 12-run public archive, not the full publication cohort. Public aliases such as `DDLPS_1a` are retained for traceability and are not independently verified diagnoses.

See [`manifest.tsv`](manifest.tsv) for accessions, byte counts, MD5 checksums, and FASTQ URLs; [`samples.csv`](samples.csv) for the sample table; and [`PROVENANCE.md`](PROVENANCE.md) for archive details.

## Requirements

Plan for at least 150 GiB free working space, 16 CPU cores, and 80 GiB RAM. The first uncached analysis downloads hg38 and builds its BWA index. The Docker option uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).

## Sample table

```csv
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
```

## Run the archive

```bash
# Clone OncoTracer into the example directory.
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer

# Enter the repository.
cd /home/student/oncotracer

# Download or reuse all 12 public FASTQs and validate them.
nextflow run /home/student/oncotracer/main.nf --make_prjna754199 \
  --test_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/prjna754199_download

# Generate the 12-sample Illumina YAML and samplesheet.
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/test/public/prjna754199 \
  --sample_table /home/student/oncotracer/test/public/prjna754199/samples.csv \
  --auto_config_dir /home/student/oncotracer/test/configs/prjna754199 \
  --auto_outdir /home/student/oncotracer/test/runs/prjna754199 \
  --run_cna_classifier true \
  --cna_classifier_sample_set sarcoma \
  --pathology_use_biomed_models false \
  -work-dir /home/student/oncotracer/test/work/prjna754199_auto_params

# Optionally check workflow wiring with Docker.
nextflow run /home/student/oncotracer/main.nf -stub-run --docker \
  -params-file /home/student/oncotracer/test/configs/prjna754199/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/prjna754199_stub

# Run or resume the complete analysis with Docker.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/prjna754199/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/prjna754199 \
  -resume

# Verify the expected sample aliases and required outputs.
python3 /home/student/oncotracer/examples/prjna754199/verify_outputs.py \
  --outdir /home/student/oncotracer/test/runs/prjna754199
```

On a configured HPC system, replace `--docker` with `--singularity` in the stub and real analysis commands.

## Generated layout

```text
test/
├── public/prjna754199/
├── configs/prjna754199/
├── references/samurai_hg38/
├── work/
└── runs/prjna754199/
```

Downloaded reads, references, BAMs, work directories, and full outputs are not committed to Git.

## Method difference

The publication used a GRCh37/hg19 Plasma-Seq method. This example uses hg38, SAMURAI/qDNAseq at 100 kb, BAM-supported boundary refinement, CNA codification, visualization, and an optional research classifier. Results are not directly interchangeable with the publication's calls.

See the [Full Tutorial](https://cfarkas.github.io/oncotracer/full_tutorial/) for output interpretation and verification checkpoints.
