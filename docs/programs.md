# Programs

OncoTracer coordinates external bioinformatics tools through Nextflow, Docker/Singularity/Conda environments, and bundled helper scripts.

Main components include:

- Nextflow for workflow orchestration.
- SAMURAI for upstream LP-WGS CNA processing from FASTQ.
- qDNAseq-style Illumina CNA outputs.
- ichorCNA-style ONT CNA outputs.
- Python/R helper scripts for boundary refinement, cytogenomic notation, plots, reports, and optional pathology concordance.

Use the containerized execution modes whenever possible so tool versions stay reproducible.
