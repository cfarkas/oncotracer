# Running the native workflow

A normal v2 analysis starts from one flat YAML:

```bash
oncotracer run \
  --backend conda \
  --threads 16 \
  --config /absolute/path/project/config/illumina.auto.yml
```

The standard CNA YAML can be executed through Conda, Docker, Singularity/Apptainer, or Poetry. All backends use the same native stage graph.
The caller stage is direct qDNAseq or direct HMMcopy/ichorCNA, followed by the same downstream refinement and reporting contract.

The optional ONT POD5 methylation branch uses explicit user-installed/licensed resources and therefore supports host, Conda, or Poetry in v2.0.0, not the stable Docker or Singularity/Apptainer image.

## Native stage graph

For Illumina:

```text
FASTQ validation
  -> BWA alignment
  -> samtools/Picard processing
  -> direct independent qDNAseq for every selected sample
  -> BAM-supported boundary refinement
  -> CNA event and cytogenomic notation tables
  -> cohort and per-sample plots
  -> workflow summary, manifest, and checksums
  -> optional native classifier/GISTIC2/reports
```

For ONT:

```text
optional explicit POD5 + barcode read IDs
  -> Dorado 5mCG/5hmCG -> Modkit CpG pileup -> Sturgeon or MARLIN
  -> methylation status (independent of CNA)

barcode FASTQ discovery and merge
  -> minimap2 alignment
  -> HMMcopy readCounter
  -> direct HMMcopy/ichorCNA
  -> BAM-supported boundary refinement
  -> CNA event and cytogenomic notation tables
  -> cohort and per-sample plots
  -> workflow summary, manifest, and checksums
  -> optional native classifier/GISTIC2/reports
```

When `run_cna_classifier: true`, stage `05_cna_classifier` creates prepared matrices, cancer-context classifications, optional GISTIC2 results, knowledge/pathology concordance, HTML/PDF reports, and clinician summaries.

## Choose a backend

### Conda

```bash
oncotracer install --conda
oncotracer doctor --backend conda

oncotracer run --backend conda \
  --config "$PWD/project/config/illumina.auto.yml"
```

### Docker

```bash
oncotracer install --docker
oncotracer doctor --backend docker

oncotracer run --backend docker \
  --config "$PWD/project/config/illumina.auto.yml"
```

### Singularity or Apptainer

```bash
oncotracer install --singularity
oncotracer doctor --backend singularity

oncotracer run --backend singularity \
  --config "$PWD/project/config/illumina.auto.yml"
```

### Poetry

```bash
./oncotracer install --poetry \
  --prefix /path/to/my/oncotracer-v2-dev-envs
/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer doctor \
  --backend poetry

/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer run \
  --backend poetry \
  --config "$PWD/project/config/illumina.auto.yml"
```

## Automatic backend selection

When `--backend` is omitted, OncoTracer uses the backend saved by the most recent successful `oncotracer install` command:

```bash
oncotracer run --config "$PWD/project/config/illumina.auto.yml"
```

For auditable production commands, specifying `--backend` explicitly is recommended.

## Threads

```bash
oncotracer run \
  --backend conda \
  --threads 8 \
  --config "$PWD/project/config/illumina.auto.yml"
```

The selected thread count is passed to supported native stages. External tool behavior and memory requirements still depend on the specific stage.

For optional methylation, `--gpu` accelerates Dorado modified-base basecalling and exposes the GPU to MARLIN. Modkit and Sturgeon remain CPU-threaded. Read [Optional ONT methylation](configuration/methylation.md) before enabling it.

## Dry-run

```bash
oncotracer run \
  --backend conda \
  --config "$PWD/project/config/illumina.auto.yml" \
  --dry-run
```

The dry-run validates paths and prints native argument arrays without launching the scientific tools.

## Resume behavior

Repeat the same command:

```bash
oncotracer run --backend conda \
  --config "$PWD/project/config/illumina.auto.yml"
```

The native ledger records:

- the exact stage argument array;
- relevant input paths, sizes, and modification times;
- expected output paths and sizes;
- SHA-256 values for small outputs;
- completion time and stage status.

A stage is reused only when its recorded contract still matches. There is no separate `-resume` option and no external workflow work directory.

## Force

```bash
oncotracer run \
  --backend conda \
  --config "$PWD/project/config/illumina.auto.yml" \
  --force
```

Use `--force` only when deliberately invalidating reusable stages. For a scientifically different analysis, prefer a new YAML and a new `outdir`.

## Native audit records

Open these first:

```text
<outdir>/.oncotracer-native/trace.tsv
<outdir>/.oncotracer-native/state.json
<outdir>/06_workflow_summary/workflow_summary.txt
<outdir>/06_workflow_summary/workflow_summary.json
<outdir>/06_workflow_summary/native_run_manifest.json
<outdir>/05_cna_classifier/native_classifier_summary.json  # when enabled
<outdir>/07_methylation/methylation_status.json             # when enabled
<outdir>/07_methylation/methylation_provenance.json         # when enabled
```

The trace is generated from argument arrays rather than shell strings. The engine checks the final trace and fails if a Nextflow invocation appears.

Inspect the run identity:

```bash
OUTDIR="$PWD/project/results"

grep -E '^(mode|dataset|engine|nextflow_used)=' \
  "$OUTDIR/06_workflow_summary/workflow_summary.txt"

sed -n '1,40p' "$OUTDIR/.oncotracer-native/trace.tsv"
```

Expected native identity:

```text
engine=native
nextflow_used=false
```

## Output ownership and container mounts

Docker runs as the invoking numeric user/group. The CLI derives mounts from the standard CNA YAML paths, including `lpwgs_root`, `outdir`, samplesheet, FASTQ roots, and pathology table. Use absolute paths and keep related data below a small number of project roots. Optional methylation is rejected for the v2.0.0 container backends.

## Stopping and restarting

Interrupting a run does not mark an incomplete stage as valid. Correct the cause and repeat the same command. OncoTracer reuses earlier valid stages and reruns the incomplete stage.

Do not manually edit `.oncotracer-native/state.json`. Preserve it with the result tree for audit and resume.
