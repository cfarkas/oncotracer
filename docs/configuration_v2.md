# Native YAML configuration

OncoTracer v2 reads flat YAML. Automatic Setup is recommended; manual files are useful for unusual layouts.

## Minimal Illumina

```yaml
mode: illumina
lpwgs_root: /data/study
outdir: /data/study/results
illumina_samplesheet: /data/study/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
force: false
```

## Minimal ONT

```yaml
mode: ont
lpwgs_root: /data/study
outdir: /data/study/results
ont_folder: /data/study/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: SAMPLE_01,SAMPLE_02
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
force: false
```

Nested YAML is deliberately rejected by the standalone parser. Paths should be absolute for containers and HPC systems.
