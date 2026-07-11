# ONT YAML

```yaml
mode: ont
lpwgs_root: /home/student/oncotracer_project
outdir: /home/student/oncotracer_project/runs/ont_example
ont_folder: /home/student/data/ont_run/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Sample_ONT_1,Sample_ONT_2
ont_samurai_outdir: /home/student/oncotracer_project/runs/ont_example/01_samurai_ont
```

| Field | Required? | Meaning |
| --- | --- | --- |
| `mode` | Yes | Must be `ont`. |
| `lpwgs_root` | Yes | Absolute folder path bound into Docker/Singularity. |
| `outdir` | Yes | Main output folder. |
| `ont_folder` | Yes | Folder containing FASTQ files or barcode folders. |
| `ont_barcodes` | Yes | Comma-separated barcode IDs. |
| `ont_sample_names` | Recommended | Comma-separated sample names in barcode order. |
| `ont_samurai_outdir` | Yes | Upstream ONT SAMURAI/ichorCNA output folder. |
| `ont_analysis_type` | Default | `liquid_biopsy`. |
| `ont_caller` | Default | `ichorcna`. |
| `ont_binsize_kb` | Default | `500`. |
| `ont_min_age_minutes` | Optional | Wait time for active sequencing folders; use `0` for completed FASTQ files. |
| `ont_ref` | Optional | Reference FASTA path. |
| `ont_normal_folder` | Optional | Normal/control FASTQ folder. |
| `ont_normal_barcodes` | Optional | Normal/control barcode IDs. |
| `ont_normal_sample_names` | Optional | Normal/control sample names. |
| `ont_build_pon` | Optional | Build panel of normals when supported. |
| `ont_force_realign` | Optional | Force ONT realignment. |
| `run_cna_classifier` | Optional | Runs optional classifier/report steps. |
| `force` | Optional | Allows supported overwrite/recompute behavior. |
