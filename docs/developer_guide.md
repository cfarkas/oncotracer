# Developer Guide

The top-level workflow is `main.nf`. Default parameters live in `nextflow.config`. Beginner-editable YAML templates live in `params/`. Documentation lives in `docs/` and is built with MkDocs Material.

Useful checks:

```bash
bash -n bin/scripts/*.sh
nextflow run main.nf -stub-run --docker -params-file test/configs/illumina.quickstart.yml
nextflow run main.nf -stub-run --docker -params-file test/configs/ont.quickstart.yml
mkdocs build --strict
```

Do not change CNA-calling algorithms, qDNAseq or ichorCNA behavior, BAM boundary refinement logic, default scientific thresholds, output file names, numbered output directories, or research-use limitations unless the scientific method change is intentional and reviewed.
