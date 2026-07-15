# Troubleshooting

When a run fails, keep the terminal output and `.nextflow.log`. Most problems are caused by a missing host program, a path outside `lpwgs_root`, a corrupt/incomplete FASTQ, insufficient disk, or an interrupted container download.

## Collect the basics first

Run these commands from the cloned `oncotracer/` directory:

```bash
pwd                                                        # should end in /oncotracer
git status --short                                         # record local changes
git rev-parse --short HEAD                                 # record the exact OncoTracer revision
java -version                                              # Nextflow requires a supported Java installation
nextflow -version                                          # show workflow engine version
docker --version                                           # omit when using Singularity/Conda
docker info >/dev/null && echo 'Docker engine: OK'         # verify daemon access
df -h .                                                    # check free space on the project filesystem
df -h /tmp                                                 # check temporary space
du -sh work .nextflow test 2>/dev/null                     # locate large caches
```

Copy the first `ERROR` block from the log, not only the final `Execution cancelled` line.

## Java or Nextflow does not start

Symptoms include `java: command not found`, `UnsupportedClassVersionError`, or a Nextflow launcher error.

```bash
command -v java
java -version
command -v nextflow
nextflow -version
```

Install a supported Java/Nextflow combination using the official links on [Installation](installation.md). If several Java installations exist, confirm `command -v java` points to the intended one. Opening a new shell after installation often fixes a stale `PATH`.

## Docker permission denied

First distinguish a missing daemon from a permission problem:

```bash
docker --version
docker info
docker run --rm hello-world
```

- `Cannot connect to the Docker daemon` means Docker is stopped or the daemon socket is unavailable.
- `permission denied` on `/var/run/docker.sock` means your account lacks access.
- Ask the administrator to grant Docker access according to your institution's policy, then log out and back in. Docker-group membership is effectively privileged access; do not change it silently on a shared server.

If files created by containers have the wrong owner, pass your numeric user/group in the YAML:

```yaml
docker_user: "1000:1000" # replace with the output of id -u and id -g
```

```bash
id -u
id -g
```

## Docker cannot see an input file

All input, configuration, reference, and output paths should be below `lpwgs_root`.

```bash
realpath project
realpath project/input/Sample_A_R1.fastq.gz
```

A safe YAML pattern is:

```yaml
lpwgs_root: /home/user/oncotracer/project
outdir: /home/user/oncotracer/project/runs/sample_a
illumina_samplesheet: /home/user/oncotracer/project/input/illumina.samplesheet.csv
```

Do not use `~`, relative paths such as `../reads`, or a samplesheet that points outside the mounted root. See [YAML examples and path rules](configuration/yaml_basics.md).

## YAML parsing or missing-parameter errors

YAML uses spaces, not tabs, and every setting needs a colon.

```bash
sed -n '1,160p' params/my_run.yml
nextflow run main.nf -stub-run --docker -params-file params/my_run.yml
```

The stub run validates workflow wiring but cannot prove that real alignment/CNA tools will succeed. Check that `mode` is exactly `illumina` or `ont` and that the input key belongs to that mode.

## Illumina samplesheet errors

The required header is exactly:

```csv
sample,fastq_1,fastq_2,status
```

Check it without Excel:

```bash
sed -n '1,6p' project/input/illumina.samplesheet.csv
```

Each sample needs an existing `fastq_1` path. For paired-end data, `fastq_2` must also exist; for an all-single-end run, leave every `fastq_2` cell empty. Do not mix layouts in one invocation. Sample names should be unique and should not change between the samplesheet, pathology table, and outputs.

## ONT barcode not found or skipped

`ont_folder` must contain barcode directories, usually under `fastq_pass/`:

```bash
find /absolute/path/to/fastq_pass -maxdepth 2 -type f -name '*.fastq*' | sed -n '1,20p'
```

After a run, inspect all selection logs:

```bash
OUT=/absolute/path/to/run
sed -n '1,120p' "$OUT/01_samurai_ont/logs/run_summary.txt"
sed -n '1,120p' "$OUT/01_samurai_ont/logs/used_fastq.tsv"
sed -n '1,120p' "$OUT/01_samurai_ont/logs/skipped_fastq.tsv"
sed -n '1,120p' "$OUT/01_samurai_ont/logs/skipped_samples.tsv"
```

`ont_min_age_minutes` prevents files that may still be written from entering a live run. Use `0` only for a completed dataset.

## Corrupt or partial FASTQ files

Test every gzip before analysis:

```bash
find project/input -type f -name '*.fastq.gz' -print0 | xargs -0 -r -n1 gzip -t
```

No output means all tested files passed. `unexpected end of file`, `invalid compressed data`, or a nonzero exit means the download/copy is incomplete; replace that file rather than resuming from it.

For the six-FASTQ HCC1143 example, do not validate by filename alone. Its run script checks the exact ENA byte count, MD5, and gzip stream and re-downloads invalid files:

```bash
bash examples/hcc1143_lpwgs/run_example.sh --docker --download-only
```

For another public dataset, compare:

```bash
wc -c path/to/sample.fastq.gz                         # exact compressed byte count
md5sum path/to/sample.fastq.gz                        # checksum published by the archive
gzip -t path/to/sample.fastq.gz && echo 'gzip: OK'   # compressed-stream integrity
```

## Not enough disk space

Reference files, container layers, FASTQs, BAMs, nested SAMURAI work, and the top-level `work/` cache coexist during a run. BAMs and work files can be much larger than compressed FASTQs.

```bash
df -h . /tmp
du -h -d 2 . 2>/dev/null | sort -h | tail -30
docker system df                                     # Docker usage; read-only report
```

Do not delete `work/`, nested `01_samurai_*/work/`, `.nextflow/`, or container caches while a run is active. They are needed by `-resume`. After results are verified and archived, use Nextflow's documented cleanup commands deliberately; never run broad deletion commands on a shared project root.

An exit code `137`, `Killed`, or an out-of-memory message usually indicates RAM pressure rather than a bad YAML. Reduce concurrent work, request more memory, or use a larger node.

## SAMURAI remains at `0 of 1`

The top-level process waits for a complete nested SAMURAI workflow. Therefore this display can remain unchanged while alignment, sorting, indexing, or CNA calling is active:

```text
RUN_ILLUMINA_SAMURAI | 0 of 1
```

In another terminal, inspect activity:

```bash
ps -ef | grep -E 'bwa|minimap2|samtools|nextflow' | grep -v grep
tail -f /absolute/path/to/outdir/01_samurai_illumina/nextflow_launch/.nextflow.log
```

For ONT, use `01_samurai_ont/nextflow_launch/.nextflow.log`. Large first runs may also download/index hg38. CPU activity and changing log timestamps indicate progress.

## Find the real task error

The top-level run log is in the repository:

```bash
tail -n 120 .nextflow.log
```

The console prints a work-directory hash for failed processes. Inspect that exact directory:

```bash
sed -n '1,240p' work/ab/cdef123456789/.command.sh   # command Nextflow executed
sed -n '1,240p' work/ab/cdef123456789/.command.err  # standard error
sed -n '1,240p' work/ab/cdef123456789/.command.out  # standard output
cat work/ab/cdef123456789/.exitcode                  # numeric exit status
```

Replace the example hash with the one in your error. Nested SAMURAI tasks have their own `work/` and `.nextflow.log` below stage 01.

## Stop safely with Ctrl+C

Press `Ctrl+C` once in the terminal running Nextflow, then allow it to stop its active tasks. Repeated interrupts can force termination before cleanup. Completed task caches remain available.

After fixing the cause, rerun the **same command**, with the same YAML and output/work locations, adding `-resume`:

```bash
nextflow run main.nf --docker -params-file params/my_run.yml -resume
```

Changing sample names, input paths, parameters, container runtime, or work directory can invalidate cached tasks. `-resume` does not repair a corrupt input file; replace the input first.

## A run completed but an expected result is missing

Start with:

```bash
OUT=/absolute/path/to/outdir
cat "$OUT/06_workflow_summary/workflow_summary.txt"
find "$OUT" -maxdepth 3 -type f | sort | sed -n '1,200p'
```

`05_cna_classifier/` is absent by design when `run_cna_classifier: false`. A CNA event table with only a header can represent a CNA-flat sample. See [Output files](outputs.md) before treating either condition as a failure.

## Ask for help with enough evidence

Include:

- the exact command (remove secrets);
- `git rev-parse HEAD`;
- Java, Nextflow, and runtime versions;
- the YAML with private paths/identifiers redacted consistently;
- the first complete error block from `.nextflow.log`;
- the failed task's `.command.sh`, `.command.err`, and `.exitcode`;
- available disk/RAM and whether `-resume` was used.

Do not upload patient identifiers, raw clinical text, credentials, or private FASTQs to a public issue.
