# Troubleshooting

Keep the terminal output and `.nextflow.log` when a run fails. Common causes are a missing host program, an inaccessible path, a corrupt FASTQ, insufficient disk or RAM, or an interrupted image download.

The examples use `/path/to/my/directory/oncotracer` as the repository path.

## Collect the basics

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Record versions, revision, storage, and local changes.
pwd
git status --short
git rev-parse --short HEAD
java -version
nextflow -version
command -v docker
df -h "$REPO_DIR" /tmp
du -sh "$REPO_DIR/work" "$REPO_DIR/.nextflow" "$REPO_DIR/test" 2>/dev/null
```

Copy the first complete `ERROR` block, not only the final cancellation line.

## Java or Nextflow does not start

```bash
# Confirm the active Java and Nextflow launchers.
command -v java
java -version
command -v nextflow
nextflow -version
```

Install supported versions using [Installation](installation.md). Open a new shell after changing `PATH` or Java.

## Docker or Singularity cannot start

Docker:

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Confirm Docker and test it through the OncoTracer installation route.
command -v docker
nextflow run "$REPO_DIR/main.nf" --install --docker \
  --lpwgs_root "$REPO_DIR/project"
```

Singularity or Apptainer:

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Confirm the HPC launcher and test the runtime through Nextflow.
command -v singularity
command -v apptainer
nextflow run "$REPO_DIR/main.nf" --install --singularity \
  --lpwgs_root "$REPO_DIR/project"
```

A Docker daemon error means the service is stopped or unavailable. A socket permission error means the account lacks Docker access. Ask the administrator rather than changing shared-server permissions without approval.

## The container cannot see an input file

Keep every configured input and output below `lpwgs_root`:

```yaml
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results/sample_a
illumina_samplesheet: /path/to/my/directory/oncotracer/project/config/illumina.samplesheet.csv
```

```bash
# Set the standard project path and resolve representative files.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
realpath "$PROJECT_DIR"
realpath "$PROJECT_DIR/input/Sample_A_R1.fastq.gz"
```

Do not use `~`, unresolved relative paths, or samplesheet paths outside `lpwgs_root`.

## YAML parsing or missing parameters

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Inspect and check the YAML without running scientific tools.
sed -n '1,160p' "$REPO_DIR/params/my_run.yml"
nextflow run "$REPO_DIR/main.nf" -stub-run --docker \
  -params-file "$REPO_DIR/params/my_run.yml"
```

YAML uses spaces, one colon per setting, and exact parameter names. `mode` must be `illumina` or `ont`.

## Illumina samplesheet errors

The header must be:

```csv
sample,fastq_1,fastq_2,status
```

```bash
# Set the standard project path and inspect the samplesheet.
REPO_DIR=/path/to/my/directory/oncotracer
sed -n '1,20p' "$REPO_DIR/project/input/illumina.samplesheet.csv"
```

Every row needs an existing `fastq_1`. Paired-end rows also need `fastq_2`; all-single-end rows leave every `fastq_2` cell empty. Do not mix layouts.

## Illumina normal-control errors

Automatic Setup accepts zero normals or at least two. This error is deliberate:

```text
ERROR: Illumina PoN requires either zero NORMAL samples or at least two; found 1
```

Provide a genuine second control or use a tumor-only table. For manual YAMLs, the list must contain every and only normal samplesheet ID:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: Control_A,Control_B
illumina_pon_min_normals: 2
```

After local-panel processing, require the completion marker:

```bash
# Set the standard result path.
REPO_DIR=/path/to/my/directory/oncotracer
PON="$REPO_DIR/project/results/01_samurai_illumina/qdnaseq_local_pon"

# Require the exact successful marker.
test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = QDNASEQ_LOCAL_PON_SUCCESS
```

Do not interpret partial files when the marker is missing or different.

## ONT barcode not found or skipped

```bash
# Set the standard project path and inspect the barcode tree.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"
find "$PROJECT_DIR/input/fastq_pass" \
  -maxdepth 2 -type f -name '*.fastq*' | sed -n '1,20p'
```

After a run, inspect:

```bash
# Set the standard ONT result path.
REPO_DIR=/path/to/my/directory/oncotracer
OUT="$REPO_DIR/project/results/ont"

# Review used, skipped, and warning logs.
sed -n '1,120p' "$OUT/01_samurai_ont/logs/run_summary.txt"
sed -n '1,120p' "$OUT/01_samurai_ont/logs/used_fastq.tsv"
sed -n '1,120p' "$OUT/01_samurai_ont/logs/skipped_fastq.tsv"
sed -n '1,120p' "$OUT/01_samurai_ont/logs/skipped_samples.tsv"
```

## Corrupt or partial FASTQs

```bash
# Set the standard project path and test every compressed FASTQ.
REPO_DIR=/path/to/my/directory/oncotracer
find "$REPO_DIR/project/input" -type f -name '*.fastq.gz' -print0 \
  | xargs -0 -r -n1 gzip -t
```

No output means the tested gzip files are valid. Replace any incomplete file before resuming.

For public data, also compare the compressed byte count and archive checksum:

```bash
# Check one public FASTQ against its recorded provenance.
wc -c /path/to/sample.fastq.gz
md5sum /path/to/sample.fastq.gz
gzip -t /path/to/sample.fastq.gz && echo 'gzip: OK'
```

## Not enough disk or memory

```bash
# Set the standard repository path and inspect storage use.
REPO_DIR=/path/to/my/directory/oncotracer
df -h "$REPO_DIR" /tmp
du -h -d 2 "$REPO_DIR" 2>/dev/null | sort -h | tail -30
```

Do not delete active `work/`, stage-01 work, `.nextflow/`, or image caches. Exit code `137`, `Killed`, or an out-of-memory message usually indicates RAM pressure.

## SAMURAI remains at `0 of 1`

The top-level process waits for the nested SAMURAI workflow, so `0 of 1` can remain visible while alignment or CNA calling is active.

```bash
# Set the standard result path and inspect active tools and the nested log.
REPO_DIR=/path/to/my/directory/oncotracer
OUT="$REPO_DIR/project/results/illumina"
ps -ef | grep -E 'bwa|minimap2|samtools|nextflow' | grep -v grep
tail -f "$OUT/01_samurai_illumina/nextflow_launch/.nextflow.log"
```

Changing log timestamps and CPU activity indicate progress.

## Find the real task error

```bash
# Set the standard repository path and inspect the top-level log.
REPO_DIR=/path/to/my/directory/oncotracer
tail -n 120 "$REPO_DIR/.nextflow.log"

# Replace the example task hash with the failed work directory shown by Nextflow.
sed -n '1,240p' "$REPO_DIR/work/ab/cdef123456789/.command.sh"
sed -n '1,240p' "$REPO_DIR/work/ab/cdef123456789/.command.err"
sed -n '1,240p' "$REPO_DIR/work/ab/cdef123456789/.command.out"
cat "$REPO_DIR/work/ab/cdef123456789/.exitcode"
```

## Resume after fixing the cause

Use the same YAML and work directory:

```bash
# Set the standard repository and project paths.
REPO_DIR=/path/to/my/directory/oncotracer
PROJECT_DIR="$REPO_DIR/project"

# Resume the existing analysis.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$PROJECT_DIR/config/illumina.auto.yml" \
  -work-dir "$PROJECT_DIR/work/analysis" \
  -resume
```

`-resume` cannot repair a corrupt input file. Replace the file first.

## A run completed but a result is missing

```bash
# Set the standard output path and inventory the run.
REPO_DIR=/path/to/my/directory/oncotracer
OUT="$REPO_DIR/project/results/illumina"
cat "$OUT/06_workflow_summary/workflow_summary.txt"
find "$OUT" -maxdepth 3 -type f | sort | sed -n '1,200p'
```

`05_cna_classifier/` is absent when `run_cna_classifier: false`. A CNA event table containing only a header can represent a CNA-flat sample.

## Ask for help with enough evidence

Include the exact command, commit, software versions, redacted YAML, first complete error block, failed task `.command.*` files, disk/RAM availability, and whether `-resume` was used. Do not post patient identifiers, private clinical text, credentials, or private FASTQs in a public issue.
