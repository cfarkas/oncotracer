# Running the native workflow

```bash
oncotracer run --backend conda --config /absolute/path/run.yml
```

The YAML is intentionally flat and human-readable. A native run executes these stages:

1. reference validation and index reuse;
2. input validation and alignment;
3. direct qDNAseq or direct HMMcopy/ichorCNA;
4. BAM-supported boundary refinement;
5. CNA codification and cytogenomic notation;
6. plots, summary, manifest, and checksums.

## Resume and force

The native ledger records stage command, input path/size/mtime, output path/size, small-output SHA-256 values, and completion time. Unchanged valid stages are reused automatically.

```bash
oncotracer run --config run.yml
oncotracer run --config run.yml --force
```

## Audit records

Open these first:

```text
<outdir>/.oncotracer-native/trace.tsv
<outdir>/.oncotracer-native/state.json
<outdir>/06_workflow_summary/workflow_summary.txt
<outdir>/06_workflow_summary/workflow_summary.json
<outdir>/06_workflow_summary/native_run_manifest.json
```

The trace is generated from argument arrays rather than shell strings. The engine checks the final trace and fails if a Nextflow invocation appears.
