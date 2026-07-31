# Troubleshooting

Keep the terminal output and `.nextflow.log` when a run fails. Common causes are missing host programs, paths outside `lpwgs_root`, incomplete FASTQs, insufficient disk or RAM, and container-runtime permissions.

## Collect the basic information

Run from the cloned repository:

```bash
# Record the repository path and exact revision.
pwd
git status --short
git rev-parse --short HEAD

# Record Java and Nextflow versions.
java -version
nextflow -version

# Check the selected runtime launcher.
command -v docker
command -v singularity
command -v apptainer

# Check project and temporary-disk space.
df -h .
df -h /tmp

# Locate large local caches.
du -sh work .nextflow test 2>/dev/null
```

Copy the first complete `ERROR` block, not only the final `Execution cancelled` line.

## Java or Nextflow does not start

```bash
# Show the Java executable and version.
command -v java
java -version

# Show the Nextflow executable and version.
command -v nextflow
nextflow -version
```

Install a supported Java/Nextflow combination using [Installation](installation.md). Open a new shell after installation when `PATH` has not refreshed.

## Docker permission or daemon errors

```bash
# Confirm that Docker is installed.
command -v docker

# Enter the repository and test Docker through the OncoTracer installation route.
cd /home/student/oncotracer
nextflow run main.nf --install --docker \
  --lpwgs_root /home/student/oncotracer/test
```

- `Cannot connect to the Docker daemon` means the daemon is stopped or unavailable.
- `permission denied` on `/var/run/docker.sock` means the user lacks Docker access.
- Ask the administrator to correct the service or permissions according to institutional policy.

When Docker-created files have the wrong owner, record the host IDs:

```bash
# Print the host user ID.
id -u

# Print the host group ID.
id -g
```

Then add the values to the YAML:

```yaml
docker_user: "1000:1000"
```

## Singularity or Apptainer errors

```bash
# Confirm the HPC runtime launcher.
command -v singularity
command -v apptainer

# Test the configured HPC runtime through Nextflow.
nextflow run main.nf --install --singularity \
  --lpwgs_root /path/to/oncotracer_project
```

Ask the cluster administrator about image-pull, cache, quota, and bind-path restrictions.

## Input path is not visible in the container

Every configured input and output should be below `lpwgs_root`.

```bash
# Resolve the common project directory.
realpath project

# Resolve one input FASTQ.
realpath project/input/Sample_A_R1.fastq.gz
```

Safe YAML pattern:

```yaml
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/results/sample_a
illumina_samplesheet: /home/student/oncotracer/project/config/illumina.samplesheet.csv
```

Do not use `~`, relative paths, or samplesheet paths outside the mounted project root.

## YAML parsing or missing parameters

```bash
# Inspect the YAML exactly as saved.
sed -n '1,180p' params/my_run.yml

# Check workflow wiring without running the scientific tools.
nextflow run main.nf -stub-run --docker \
  -params-file params/my_run.yml
```

YAML uses spaces, not tabs. Confirm `mode: illumina` or `mode: ont` and use keys that belong to that route.

## Illumina samplesheet errors

The required header is:

```csv
sample,fastq_1,fastq_2,status
```

```bash
# Inspect the samplesheet header and first rows.
sed -n '1,12p' project/input/illumina.samplesheet.csv
```

Each sample needs an existing `fastq_1`. Paired-end runs also need `fastq_2`; single-end runs leave every `fastq_2` cell empty. Do not mix layouts. Sample names must be unique and must match pathology identifiers exactly.

## Illumina normal-control errors

Automatic Setup accepts zero normal controls or at least two. This error means one control was supplied:

```text
ERROR: Illumina PoN requires either zero NORMAL samples or at least two; found 1
```

Provide a genuine second control or use a tumor-only table when no local normal reference is intended.

For a manual YAML, these settings must match the samplesheet normal rows exactly:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: Control_1,Control_2
illumina_pon_min_normals: 2
```

```bash
# Inspect the YAML and samplesheet together.
sed -n '1,180p' params/my_run.yml
sed -n '1,40p' project/input/illumina.samplesheet.csv
```

After a normal-reference run, check the final marker:

```bash
# Set the local-PoN result directory.
PON=/path/to/outdir/01_samurai_illumina/qdnaseq_local_pon

# Require the exact success marker.
test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = QDNASEQ_LOCAL_PON_SUCCESS
```

Do not interpret partial files when this check fails.

## ONT barcode not found or skipped

```bash
# List FASTQs inside the barcode tree.
find /path/to/fastq_pass -maxdepth 2 -type f \
  \( -name '*.fastq' -o -name '*.fastq.gz' -o -name '*.fq' -o -name '*.fq.gz' \) \
  -print | sed -n '1,40p'

# Set the completed ONT result directory.
OUT=/path/to/outdir

# Inspect the barcode summary and selection logs.
sed -n '1,120p' "$OUT/01_samurai_ont/logs/run_summary.txt"
sed -n '1,120p' "$OUT/01_samurai_ont/logs/used_fastq.tsv"
sed -n '1,120p' "$OUT/01_samurai_ont/logs/skipped_fastq.tsv"
sed -n '1,120p' "$OUT/01_samurai_ont/logs/skipped_samples.tsv"
```

Use `ont_min_age_minutes: 0` only after sequencing files are complete.

## Corrupt or incomplete FASTQs

```bash
# Test every compressed FASTQ below the project input directory.
find project/input -type f -name '*.fastq.gz' -print0 \
  | xargs -0 -r -n1 gzip -t
```

Success produces no output. Replace any file that reports `unexpected end of file` or invalid compressed data.

For a public archive file, verify bytes and MD5 as well:

```bash
# Print the compressed byte count.
wc -c path/to/sample.fastq.gz

# Print the MD5 checksum.
md5sum path/to/sample.fastq.gz

# Confirm gzip integrity.
gzip -t path/to/sample.fastq.gz && echo 'gzip: OK'
```

## Insufficient disk or RAM

```bash
# Check project and temporary-disk space.
df -h . /tmp

# List the largest directories below the project.
du -h -d 2 . 2>/dev/null | sort -h | tail -30
```

Do not delete active `work/`, stage-01 work directories, `.nextflow/`, or image caches. Exit code `137`, `Killed`, or an out-of-memory message usually indicates insufficient RAM or excessive task concurrency.

## SAMURAI remains at `0 of 1`

The top-level process waits for a nested SAMURAI workflow, so its counter can remain unchanged while alignment and CNA calling continue.

```bash
# Show active alignment and workflow processes.
ps -ef | grep -E 'bwa|minimap2|samtools|nextflow' | grep -v grep

# Follow the nested Illumina Nextflow log.
tail -f /path/to/outdir/01_samurai_illumina/nextflow_launch/.nextflow.log
```

For ONT, inspect `01_samurai_ont/nextflow_launch/.nextflow.log`.

## Find the failed task

```bash
# Read the latest top-level Nextflow log lines.
tail -n 160 .nextflow.log

# Inspect the exact failed task command from the hash printed by Nextflow.
sed -n '1,260p' work/ab/cdef123456789/.command.sh

# Inspect task standard error.
sed -n '1,260p' work/ab/cdef123456789/.command.err

# Inspect task standard output.
sed -n '1,260p' work/ab/cdef123456789/.command.out

# Read the task exit code.
cat work/ab/cdef123456789/.exitcode
```

Replace the example hash with the work-directory hash from the error message. Nested SAMURAI tasks have their own work directory and `.nextflow.log` below stage 01.

## Stop and resume

Press `Ctrl+C` once and allow Nextflow to stop active tasks. After fixing the cause, rerun the same command with the same YAML and work directory:

```bash
# Resume the same Docker analysis.
nextflow run main.nf --docker \
  -params-file params/my_run.yml \
  -work-dir /path/to/project/work \
  -resume
```

`-resume` does not repair a corrupt input file; replace the input first.

## Completed run with a missing result

```bash
# Set the result directory from the YAML.
OUT=/path/to/outdir

# Read the workflow summary.
cat "$OUT/06_workflow_summary/workflow_summary.txt"

# List result files near the top of the output tree.
find "$OUT" -maxdepth 3 -type f -print | sort | sed -n '1,200p'
```

`05_cna_classifier/` is absent when `run_cna_classifier: false`. A CNA event table containing only its header can represent a CNA-flat sample.

## Ask for help

Include the exact command, OncoTracer commit, Java/Nextflow/runtime versions, redacted YAML, first complete error block, failed task `.command.sh`, `.command.err`, `.exitcode`, available disk and RAM, and whether `-resume` was used. Do not upload patient identifiers, private FASTQs, credentials, or raw clinical text to a public issue.
