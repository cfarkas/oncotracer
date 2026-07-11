# Before You Begin

OncoTracer is run from a terminal: a text window where you type commands and press Enter.

You need:

- Linux, macOS, WSL, or an HPC shell where `nextflow` works.
- Docker, Singularity/Apptainer, or Conda.
- Illumina or ONT FASTQ input files.
- Enough disk space for references, BAM files, intermediate results, and final reports.
- A project folder visible to the selected runtime.

OncoTracer is a research workflow and not a standalone diagnostic system.

Recommended layout:

```text
/home/student/oncotracer_project/
  oncotracer/
  input/
  data/
  runs/
```

Useful commands:

```bash
pwd
ls -lh
realpath .
```
