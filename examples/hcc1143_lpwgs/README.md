# HCC1143 six-FASTQ public example

This public example uses three paired-end LP-WGS libraries—six FASTQ files—from the HCC1143 breast-cancer cell line. The conditions are DMSO, BEZ235, and Trametinib from [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with Ben-David et al., *Nature Communications* (2018).

All three rows use `TUMOR`. DMSO is a treatment control, not a matched normal genome.

Follow [QuickStart Example 2](https://cfarkas.github.io/oncotracer/public_cohort/) for the six download commands, checksum validation, sample table, Automatic Setup, analysis, and output checks.

The sample table is:

```csv
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
```

After the six FASTQs and `samples.csv` are present, run:

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Generate the HCC1143 YAML and samplesheet.
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/test/public/hcc1143_lpwgs \
  --sample_table /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv \
  --auto_config_dir /home/student/oncotracer/test/configs/hcc1143_lpwgs \
  --auto_outdir /home/student/oncotracer/test/runs/hcc1143_lpwgs

# Run or resume the analysis with Docker.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

On a configured HPC system, replace `--docker` with `--singularity`. The Docker option uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).

Requirements: approximately 1.08 GiB for the compressed reads, at least 40 GiB of free working space, 16 CPU cores, and at least 80 GiB RAM because the pinned BWA task requests 72 GB. See [`manifest.tsv`](manifest.tsv) for URLs and provenance and [`checksums.md5`](checksums.md5) for MD5 values.
