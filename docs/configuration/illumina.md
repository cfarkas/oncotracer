# Illumina YAML

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer_project
outdir: /home/student/oncotracer_project/runs/illumina_example
illumina_samplesheet: /home/student/oncotracer_project/input/illumina_samplesheet.csv
illumina_samurai_outdir: /home/student/oncotracer_project/runs/illumina_example/01_samurai_illumina
```

| Field | Required? | Meaning |
| --- | --- | --- |
| `mode` | Yes | Must be `illumina`. |
| `lpwgs_root` | Yes | Absolute folder path bound into Docker/Singularity. |
| `outdir` | Yes | Main output folder. |
| `illumina_samplesheet` | Yes | CSV with `sample,fastq_1,fastq_2,status`. |
| `illumina_samurai_outdir` | Yes | Upstream Illumina SAMURAI/qDNAseq output folder. |
| `illumina_analysis_type` | Default | `solid_biopsy`. |
| `illumina_caller` | Default | `qdnaseq`. |
| `illumina_binsize_kb` | Default | `100`. |
| `run_cna_classifier` | Optional | Runs optional classifier/report steps. |
| `force` | Optional | Allows supported overwrite/recompute behavior. |

```csv
sample,fastq_1,fastq_2,status
Sample_A,/home/student/data/Sample_A_R1.fastq.gz,/home/student/data/Sample_A_R2.fastq.gz,tumor
```
