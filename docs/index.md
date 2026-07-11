# OncoTracer

**Reproducible LP-WGS CNA analysis for ONT and Illumina FASTQ data.**

OncoTracer is a Nextflow research workflow for copy-number alteration analysis. It starts from FASTQ files, runs the appropriate upstream CNA route, refines CNA boundaries, and writes tables, plots, reports, and optional pathology concordance outputs.

!!! warning "Research workflow"
    OncoTracer is not a standalone diagnostic system. Use outputs as research evidence that requires expert review and validation.

## Start Here

1. Read [Before You Begin](getting_started/before_you_begin.md).
2. Learn path basics in [What Is a Path?](getting_started/paths.md).
3. Install Nextflow plus Docker, Singularity/Apptainer, or Conda in [Install OncoTracer](installation.md).
4. Copy a versioned YAML template from `params/` and edit your real absolute paths.
5. Run with `nextflow run main.nf --docker -params-file params/my_config.yml -resume`.

## Supported Entry Points

| I have... | Copy this template | Read this page |
| --- | --- | --- |
| Illumina paired-end LP-WGS FASTQ files | `params/illumina.minimal.yml` or `params/illumina.example.yml` | [First Illumina Run](getting_started/first_illumina_run.md) |
| ONT `fastq_pass` or barcode FASTQ files | `params/ont.minimal.yml` or `params/ont.example.yml` | [First ONT Run](getting_started/first_ont_run.md) |
| Illumina FASTQ files plus pathology CSV | `params/illumina.pathology.example.yml` | [Pathology and Classifier](configuration/pathology.md) |

## Documentation Map

- [YAML Basics](configuration/yaml_basics.md)
- [Illumina YAML](configuration/illumina.md)
- [ONT YAML](configuration/ont.md)
- [Complete Parameter Reference](configuration/parameter_reference.md)
- [Input Files](inputs.md)
- [Output Files](outputs.md)
- [Troubleshooting](troubleshooting.md)
