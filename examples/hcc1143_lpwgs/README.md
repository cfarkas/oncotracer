# HCC1143 six-FASTQ public example

This example uses three paired-end HCC1143 low-pass whole-genome sequencing libraries—six FASTQ files—from public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with [Ben-David et al., *Nature Communications* (2018)](https://doi.org/10.1038/s41467-018-05729-w).

All rows use `TUMOR`. DMSO is a treatment control, not a normal genome. Tiny unpaired singleton files are excluded because this example requires one matched R1/R2 pair per sample.

The exact sample table is:

```csv
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
```

Follow [QuickStart Example 2](https://cfarkas.github.io/oncotracer/public_cohort/) for the six download commands, MD5 checks, `gzip -t`, output checks, and resume instructions.

After the FASTQs and `samples.csv` are present, run:

```bash
# Enter the cloned OncoTracer repository.
cd /home/student/oncotracer

# Generate the Illumina YAML and R1/R2 samplesheet automatically.
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/test/public/hcc1143_lpwgs \
  --sample_table /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv \
  --auto_config_dir /home/student/oncotracer/test/configs/hcc1143_lpwgs \
  --auto_outdir /home/student/oncotracer/test/runs/hcc1143_lpwgs

# Run or resume the generated configuration with Docker.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

Docker uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer). On a configured HPC system, replace `--docker` with `--singularity`; the equivalent image is `docker://carlosfarkas/oncotracer:latest`.

Plan for about 1.08 GiB of compressed reads, at least 40 GiB of free working space, 16 CPU cores, and at least 80 GiB of addressable RAM. The first uncached run also downloads hg38 and creates a BWA index.

See [`manifest.tsv`](manifest.tsv) for URLs and provenance and [`checksums.md5`](checksums.md5) for the six expected MD5 values.
