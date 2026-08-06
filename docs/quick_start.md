# QuickStart 1: Illumina and ONT

QuickStart 1 is a complete native analysis of one public paired-end Illumina library and one public ONT library. OncoTracer downloads approximately 225 MB of reads, validates their exact byte counts and MD5 values, creates two YAML files, runs both branches, and verifies the required outputs.

```bash
oncotracer install --conda
oncotracer quickstart 1 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart1"
```

Equivalent execution commands are available after installing the corresponding backend:

```bash
oncotracer quickstart 1 --backend docker --test-root "$PWD/oncotracer-quickstart1-docker"
oncotracer quickstart 1 --backend singularity --test-root "$PWD/oncotracer-quickstart1-sif"
```

The command runs Illumina first, ONT second, and verifies:

- `06_workflow_summary/workflow_summary.txt`;
- `03_cna_codification/cna_events.tsv`;
- `03_cna_codification/cna_cytogenomic_notation.tsv`;
- the per-sample and cohort PDF plots;
- `.oncotracer-native/trace.tsv` and `native_run_manifest.json`.

## Download without analysis

```bash
oncotracer quickstart 1 \
  --test-root "$PWD/oncotracer-quickstart1" \
  --download-only
```

The generated configs are under `configs/`; the native results are under `runs/illumina/` and `runs/ont/`.
