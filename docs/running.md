# Running OncoTracer

Run from the repository root:

```bash
nextflow run main.nf --docker -params-file params/my_config.yml -resume
```

Use exactly one runtime flag:

| Flag | Use when |
| --- | --- |
| `--docker` | Docker is available. Recommended for workstations. |
| `--singularity` | Singularity/Apptainer is available. Recommended for HPC. |
| `--conda` | Containers are unavailable. |

## Illumina FASTQ

```bash
cp params/illumina.example.yml params/my_illumina.yml
nano params/my_illumina.yml
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume
```

## ONT FASTQ

```bash
cp params/ont.example.yml params/my_ont.yml
nano params/my_ont.yml
nextflow run main.nf --docker -params-file params/my_ont.yml -resume
```

## Illumina FASTQ + Pathology

```bash
cp params/illumina.pathology.example.yml params/my_illumina_pathology.yml
nano params/my_illumina_pathology.yml
nextflow run main.nf --docker -params-file params/my_illumina_pathology.yml -resume
```

## Resume Behavior

Use `-resume` after an interrupted run. Nextflow reuses completed tasks when the command and inputs are unchanged.

## Runtime Notes

The upstream SAMURAI steps launch their own Nextflow/SAMURAI run from FASTQ. The downstream OncoTracer steps use the selected Docker, Singularity, or Conda runtime.
