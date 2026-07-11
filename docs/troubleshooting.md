# Troubleshooting

## The YAML File Does Not Work

Check that every path is absolute and exists. YAML uses `setting_name: value`; do not remove the colon and do not use tabs.

## Docker Cannot See My Files

Place input data under `lpwgs_root`, or set `lpwgs_root` to the parent folder that contains your FASTQ files. OncoTracer binds this folder into Docker and Singularity by default.

## ONT Barcode Not Found

Make sure `ont_folder` points to the folder containing barcode directories, or to a run folder with `fastq_pass` inside it.

## Illumina Samplesheet Error

The header must be exactly:

```csv
sample,fastq_1,fastq_2,status
```

For FASTQ runs, leave `bam` and `gender` empty.

## Resume A Failed Run

Fix the problem, then rerun the same command with `-resume`.

## Documentation Website

The online documentation is https://cfarkas.github.io/oncotracer/. You do not need to build docs locally to run the workflow.
