# Quick Start

The beginner workflow is: copy a template, replace example paths with real absolute paths, validate the YAML, and run with `-params-file`.

Illumina:

```bash
cp params/illumina.minimal.yml params/my_illumina.yml
nano params/my_illumina.yml
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume
```

ONT:

```bash
cp params/ont.minimal.yml params/my_ont.yml
nano params/my_ont.yml
nextflow run main.nf -stub-run --docker -params-file params/my_ont.yml
nextflow run main.nf --docker -params-file params/my_ont.yml -resume
```

Public FASTQ examples are documented in [Public Example Data](example_data.md). Path concepts are explained in [What Is a Path?](getting_started/paths.md).
