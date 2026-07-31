# Choose how to configure a run

Most users need one generated YAML. Use Automatic Setup first; edit a YAML manually only when the standard FASTQ naming rules do not fit the study.

## Choose a route

| Goal | Guide | Result |
| --- | --- | --- |
| Verify OncoTracer with public data | [QuickStart Example 1](quick_start.md) | Generated Illumina and ONT YAML files |
| Run three public paired libraries | [QuickStart Example 2](public_cohort.md) | HCC1143 YAML, samplesheet, and results |
| Configure your own standard FASTQs | [Automatic Setup](auto_params.md) | Generated YAML, manifest, and Illumina samplesheet |
| Configure six tumors and four controls | [Other Example Run](six_tumor_four_control.md) | Template for **user-provided** FASTQs; data are not included |
| Configure Illumina manually | [Illumina Configuration](configuration/illumina.md) | Custom samplesheet and YAML |
| Configure ONT manually | [ONT Configuration](configuration/ont.md) | Custom barcode settings and YAML |
| Add pathology information | [Pathology and classifier](configuration/pathology.md) | Extra fields in the same Illumina YAML |
| Change refinement parameters | [Boundary Refinement](configuration/refinement.md) | Non-default settings in the same YAML |
| Look up a field | [Parameter Reference](configuration/parameter_reference.md) | Name, type, default, and purpose |

## One YAML controls one run

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/runs/sample_a
illumina_samplesheet: /home/student/oncotracer/project/input/illumina.samplesheet.csv
```

The YAML stores paths and settings. It does not contain FASTQ reads.

## Recommended: Automatic Setup

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Generate an Illumina YAML and samplesheet from supported FASTQ names.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/project/input/fastq \
  --sample_table /home/student/oncotracer/project/input/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config \
  --auto_outdir /home/student/oncotracer/project/results

# Run the generated YAML with Docker.
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/illumina.auto.yml \
  -work-dir /home/student/oncotracer/project/work \
  -resume
```

On HPC, replace `--docker` with `--singularity` in the analysis command.

## Manual YAML editing

```bash
# Enter the repository.
cd /home/student/oncotracer

# Copy the Illumina template without changing the versioned original.
cp params/illumina.minimal.yml params/my_illumina.yml

# Edit the copied file.
nano params/my_illumina.yml

# Check the workflow wiring without running the scientific tools.
nextflow run main.nf -stub-run --docker \
  -params-file params/my_illumina.yml

# Run or resume the real analysis.
nextflow run main.nf --docker \
  -params-file params/my_illumina.yml \
  -resume
```

Use `params/ont.minimal.yml` for a manual ONT configuration.

## Runtime options

| Option | Use |
| --- | --- |
| `--docker` | Linux workstation or server; uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer) |
| `--singularity` | HPC configured with Singularity or Apptainer |
| `--conda` | Fallback when containers are unavailable |

Use one runtime option per analysis command.
