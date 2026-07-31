# HCC1143 six-FASTQ public example

This opt-in example uses three paired-end low-pass whole-genome sequencing libraries—six physical FASTQ files—from the HCC1143 triple-negative breast-cancer cell line. The conditions are DMSO, BEZ235, and Trametinib from public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with [Ben-David et al., *Nature Communications* (2018)](https://doi.org/10.1038/s41467-018-05729-w).

All three rows use `TUMOR`: DMSO is an experimental treatment control, not a normal genome. Tiny unpaired singleton files are excluded because this example requires matched R1/R2 files.

Follow the [beginner QuickStart Example 2](https://cfarkas.github.io/oncotracer/public_cohort/) for the six literal `curl` commands, checksum validation, sample-table copy, automatic configuration, real analysis, and output checks. It does not depend on a shell runner.

After the FASTQs and `samples.csv` are in `/home/student/oncotracer/test/public/hcc1143_lpwgs`, the two OncoTracer commands are:

```bash
cd /home/student/oncotracer
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/test/public/hcc1143_lpwgs \
  --sample_table /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv \
  --auto_config_dir /home/student/oncotracer/test/configs/hcc1143_lpwgs \
  --auto_outdir /home/student/oncotracer/test/runs/hcc1143_lpwgs

nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

`--docker` is a Nextflow option. Nextflow manages the container; do not launch Docker or Apptainer directly. On a configured HPC system, replace only `--docker` with `--singularity` in the Nextflow command.

Requirements: Linux, Java 17+, Nextflow, a supported container runtime, about
1.08 GiB for the compressed reads, at least 40 GiB free working space, 16 CPU
cores, and at least 80 GiB of addressable RAM because the pinned BWA task
requests 72 GB. A first run also prepares the hg38 reference. See
[`manifest.tsv`](manifest.tsv) for URLs and provenance and
[`checksums.md5`](checksums.md5) for the six expected MD5 values.
