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

## SAMURAI Remains at `0 of 1`

The outer OncoTracer task waits for a nested SAMURAI workflow, so `RUN_ILLUMINA_SAMURAI | 0 of 1` or `RUN_ONT_SAMURAI | 0 of 1` can remain visible while alignment and CNA calling are active. This is not automatically a stall. `run_test.sh` prints an activity message every 30 seconds.

In another terminal, confirm that alignment or Nextflow is still running:

```bash
ps -ef | grep -E 'bwa|samtools|nextflow'                         # inspect active analysis processes
tail -f test/runs/illumina/01_samurai_illumina/nextflow_launch/.nextflow.log    # follow the nested Illumina workflow
```

For ONT, replace `illumina/01_samurai_illumina` with `ont/01_samurai_ont`. Stop a run only after checking the log for an error and confirming that no analysis process is active.

## Documentation Website

The online documentation is https://cfarkas.github.io/oncotracer/. You do not need to build docs locally to run the workflow.
