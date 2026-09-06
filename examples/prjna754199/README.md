# PRJNA754199 public archive

The [full tutorial](../../docs/full_tutorial.md) explains how to download the
12 public single-end Illumina libraries, prepare their samplesheet, and analyze
them with the ordinary `auto`, `check`, and `run` commands. The tutorial creates
the table with `cat`; each row maps to one single-end FASTQ. It explains every
path and shows the checksums before the analysis step.

This folder holds the versioned [manifest](manifest.tsv),
[sample labels](samples.csv), [archive provenance](PROVENANCE.md), output
verifier and gallery exporter. The manifest contains 12 public runs, not every
specimen in the associated article. Sample aliases are not independently
verified diagnoses.

Use the standard installation and run commands in the tutorial. Genome-index
reuse is optional; see [prebuilt indexes](../../docs/reference_indexes.md).
