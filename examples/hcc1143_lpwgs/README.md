# HCC1143 six-FASTQ public example

This opt-in example runs three paired-end low-pass whole-genome sequencing libraries—six physical FASTQ files—from the HCC1143 triple-negative breast-cancer cell line. The conditions are DMSO, BEZ235, and Trametinib from public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with [Ben-David et al., Nature Communications (2018)](https://doi.org/10.1038/s41467-018-05729-w).

All three rows use `TUMOR`: DMSO is the experimental treatment control, but its DNA still comes from a cancer cell line. Tiny unpaired singleton files exposed by ENA are intentionally excluded because the Illumina workflow requires matched R1/R2 files.

Requirements: Linux, Java 17+, Nextflow, Docker, approximately 1.08 GiB download for reads, 40 GiB free working space, 16 CPU cores, and 64 GiB RAM recommended. A first run also prepares the hg38 reference; allow roughly 1–2 hours depending on network and CPU. Later `-resume` runs reuse completed work.

```bash
git clone https://github.com/cfarkas/oncotracer.git  # clone the pipeline
cd oncotracer                                        # enter the repository
bash examples/hcc1143_lpwgs/run_example.sh --docker # download, validate, configure, run, and check all outputs
```

Use `--download-only` to prepare the FASTQs without analysis, or `--prepare-only` to also generate and display the YAML and samplesheet. Every download is checked against the exact byte count and MD5 published by ENA, then tested with `gzip -t`. See `manifest.tsv` for provenance and checksums.
