# QuickStart Example 3: Six Illumina Tumors and Four Controls

This example analyzes six paired-end Illumina tumor samples, `ONCO001`
through `ONCO006`, against a run-local qDNAseq panel of normals (PoN) built
from four PBMC controls, `CTRL001` through `CTRL004`. The corrected CNA
outputs contain the six tumors; the four controls remain PoN provenance and
quality-control inputs.

The commands below give Nextflow a 20-logical-CPU view, do not request a GPU,
and run in `screen`. They do not use a wrapper script, `tmux`, or `nohup`.

!!! important "Start containers only through Nextflow"
    The supported user entry point is always `nextflow run .../main.nf`.
    Configuration-only commands do not need a runtime option. For the real
    analysis, `--docker` tells Nextflow to manage Docker. Do not type
    `docker run`, `docker exec`, `apptainer run`, `apptainer exec`,
    `singularity run`, or `singularity exec` yourself.

    A generated value beginning with `docker://`, such as the pinned qDNAseq
    image, is an OCI image address used by the workflow. It is not a command
    to run Docker directly.

!!! note "What the 20-CPU guard means"
    `taskset --cpu-list 0-19` pins host processes and Singularity/Apptainer
    children to logical CPUs 0 through 19. `ActiveProcessorCount=20` gives
    the top-level and nested Nextflow controllers the same 20-CPU scheduling
    view. Individual steps may use fewer CPUs. Docker containers are created
    by the Docker daemon and do not inherit the client process's `taskset`
    affinity, so this is not a hard Docker cpuset. Ask the administrator for
    an executor-level 20-CPU limit when a hard system-wide cap is required.

!!! warning "The 8 GiB value is not a total RAM limit"
    `-Xmx8g` limits each Nextflow controller JVM only. Alignment, qDNAseq,
    the local PoN, and concurrent tasks use additional RAM. The pinned BWA task
    requests 72 GB by itself, so use a machine or scheduler allocation with at
    least 80 GiB of addressable RAM; more may be needed for concurrent tasks.
    Check available memory and disk with your administrator before starting.

## 1. Check the required programs

This guide assumes that OncoTracer is cloned at
`/home/student/oncotracer` and that Nextflow and Docker are already installed.
These commands check the required launchers without invoking Docker:

```bash
cd /home/student/oncotracer
nextflow -version
command -v docker
command -v taskset
command -v screen
taskset -pc $$
```

Do not continue if any command is missing. See [Install
requirements](installation.md) before using this example. The final command
prints the logical CPUs already allowed for the current shell. Continue with
the documented `0-19` list only if all CPU IDs 0 through 19 are allowed. If
they are not, ask the administrator or scheduler for a 20-CPU allocation and
replace every `0-19` below with 20 CPU IDs from that allocation.

GNU Screen provides the `screen` command, and the Linux `util-linux` package
provides `taskset`. Ask your administrator to install them if either launcher
check is empty.

## 2. Arrange the 20 FASTQ files

Create one project directory **outside** the cloned Git repository. This keeps
large or private sequencing files away from accidental Git staging:

```bash
mkdir -p /home/student/oncotracer_projects/onco6_ctrl4/input/fastq
mkdir -p /home/student/oncotracer_projects/onco6_ctrl4/config
mkdir -p /home/student/oncotracer_projects/onco6_ctrl4/results
mkdir -p /home/student/oncotracer_projects/onco6_ctrl4/work
```

Place exactly one R1/R2 pair for each sample directly in `input/fastq`:

```text
/home/student/oncotracer_projects/onco6_ctrl4/
├── input/
│   ├── samples.csv
│   └── fastq/
│       ├── ONCO001_R1.fastq.gz
│       ├── ONCO001_R2.fastq.gz
│       ├── ONCO002_R1.fastq.gz
│       ├── ONCO002_R2.fastq.gz
│       ├── ONCO003_R1.fastq.gz
│       ├── ONCO003_R2.fastq.gz
│       ├── ONCO004_R1.fastq.gz
│       ├── ONCO004_R2.fastq.gz
│       ├── ONCO005_R1.fastq.gz
│       ├── ONCO005_R2.fastq.gz
│       ├── ONCO006_R1.fastq.gz
│       ├── ONCO006_R2.fastq.gz
│       ├── CTRL001_R1.fastq.gz
│       ├── CTRL001_R2.fastq.gz
│       ├── CTRL002_R1.fastq.gz
│       ├── CTRL002_R2.fastq.gz
│       ├── CTRL003_R1.fastq.gz
│       ├── CTRL003_R2.fastq.gz
│       ├── CTRL004_R1.fastq.gz
│       └── CTRL004_R2.fastq.gz
├── config/                         # generated configuration goes here
├── results/                        # final results go here
└── work/                           # Nextflow resume cache goes here
```

The sample name is the filename text before `_R1` or `_R2`. Automatic Setup
does not recursively combine lane files. If a sample was delivered in
multiple lanes, consolidate each read direction first so that the folder has
one R1 and one R2 for that sample. Do not mix single-end and paired-end
libraries in one run.

Confirm the file count without searching outside this one directory:

```bash
find /home/student/oncotracer_projects/onco6_ctrl4/input/fastq \
  -maxdepth 1 -type f -name '*.fastq.gz' | wc -l
```

The command must print `20`.

## 3. Create the tumor/normal table

Open the metadata file:

```bash
nano /home/student/oncotracer_projects/onco6_ctrl4/input/samples.csv
```

Paste exactly this content:

```csv
sample_name,status
ONCO001,TUMOR
ONCO002,TUMOR
ONCO003,TUMOR
ONCO004,TUMOR
ONCO005,TUMOR
ONCO006,TUMOR
CTRL001,NORMAL
CTRL002,NORMAL
CTRL003,NORMAL
CTRL004,NORMAL
```

Save with `Ctrl+O`, press Enter, and exit Nano with `Ctrl+X`.

This two-column table is the input to Automatic Setup. It is not the final
Illumina samplesheet. Automatic Setup creates the required four-column
`sample,fastq_1,fastq_2,status` samplesheet from it. Sample IDs are
case-sensitive and must match the FASTQ prefixes exactly.

## 4. Enter a screen session and set the CPU/GPU environment

Start `screen`:

```bash
screen -S oncotracer_onco6_ctrl4
```

Run the following commands inside that screen:

```bash
cd /home/student/oncotracer

export CUDA_VISIBLE_DEVICES=""
export NVIDIA_VISIBLE_DEVICES=none
export SINGULARITYENV_CUDA_VISIBLE_DEVICES=""
export APPTAINERENV_CUDA_VISIBLE_DEVICES=""
export NXF_OPTS="-XX:ActiveProcessorCount=20 -Xms512m -Xmx8g"
```

The Nextflow commands below do not request GPUs. The Singularity alternative
also does not use `--nv`. The environment variables hide visible GPU devices
from child processes, but a hard GPU exclusion on a shared system requires an
administrator-enforced scheduler or cgroup allocation.

## 5. Generate the YAML and Illumina samplesheet

Still inside `screen`, run Automatic Setup through Nextflow:

```bash
taskset --cpu-list 0-19 nextflow run /home/student/oncotracer/main.nf \
  --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer_projects/onco6_ctrl4/input/fastq \
  --sample_table /home/student/oncotracer_projects/onco6_ctrl4/input/samples.csv \
  --auto_config_dir /home/student/oncotracer_projects/onco6_ctrl4/config \
  --auto_outdir /home/student/oncotracer_projects/onco6_ctrl4/results \
  -work-dir /home/student/oncotracer_projects/onco6_ctrl4/work/auto_params
```

This preparation step validates all 20 gzip files and then stops. It does not
align reads or call CNAs. Wait for `AUTO_PARAMS SUCCESS` before continuing.

Inspect the generated files:

```bash
sed -n '1,120p' /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.auto.yml
sed -n '1,20p' /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.samplesheet.csv
cat /home/student/oncotracer_projects/onco6_ctrl4/config/auto_params_manifest.tsv
```

The YAML must include this exact PoN block:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: "CTRL001,CTRL002,CTRL003,CTRL004"
illumina_pon_min_normals: 4
illumina_pon_name: CTRL001_CTRL002_CTRL003_CTRL004_PoN
illumina_pon_min_mapq: 37
illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1
```

Check the generated role counts:

```bash
grep -c ',tumor$' /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.samplesheet.csv
grep -c ',normal$' /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.samplesheet.csv
```

The two commands must print `6` and `4`, respectively. Do not start the
analysis if the counts or the four control IDs differ.

## 6. Run the analysis with 20 CPUs and no GPU

Still inside the same `screen`, run the generated YAML through Nextflow:

```bash
taskset --cpu-list 0-19 nextflow run /home/student/oncotracer/main.nf \
  --docker \
  -params-file /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.auto.yml \
  -work-dir /home/student/oncotracer_projects/onco6_ctrl4/work/analysis \
  -resume
```

This is the analysis command. Nextflow launches and manages the selected
container runtime; do not open another terminal and start a second copy.
`-resume` is harmless on the first run and reuses unchanged completed tasks
after an interruption.

### HPC with Singularity or Apptainer

On an HPC system configured with Singularity or Apptainer, replace `--docker`
with `--singularity` in the analysis command above. Automatic Setup remains
unchanged because it does not launch an analysis container. The HPC analysis
command is:

```bash
taskset --cpu-list 0-19 nextflow run /home/student/oncotracer/main.nf \
  --singularity \
  -params-file /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.auto.yml \
  -work-dir /home/student/oncotracer_projects/onco6_ctrl4/work/analysis \
  -resume
```

Do not type an `apptainer`, `singularity`, or Docker execution command, and do
not add `--nv`; Nextflow remains the only user-facing launcher.

## 7. Detach, reconnect, or resume

To leave the analysis running, press `Ctrl+A`, release both keys, and then
press `D`. Reconnect later with:

```bash
screen -r oncotracer_onco6_ctrl4
```

Detaching does not stop Nextflow. Do not launch the command again while the
existing screen is running.

If the analysis itself was interrupted, reconnect or create the screen again,
repeat the environment exports from step 4, and rerun the exact analysis
command from step 6. Keep the same YAML, inputs, and `work/analysis` directory
so that `-resume` can reuse completed tasks.

## 8. Verify success and the PoN sample roles

After Nextflow completes successfully, set the output paths:

```bash
OUT=/home/student/oncotracer_projects/onco6_ctrl4/results
PON=$OUT/01_samurai_illumina/qdnaseq_local_pon
```

Require the atomic PoN completion marker:

```bash
test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = QDNASEQ_LOCAL_PON_SUCCESS \
  && echo "PoN completed successfully"
```

Inspect the four-control manifest, leave-one-out normal QC, corrected tumor
profiles, and workflow summary:

```bash
sed -n '1,10p' "$PON/pon/normal_panel_manifest.tsv"
sed -n '1,10p' "$PON/qc/normal_panel_sample_qc.tsv"
find "$PON/bins" -maxdepth 1 -type f -name '*_markdup_bins.bed' -printf '%f\n' | sort
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

The manifest and normal QC must contain exactly `CTRL001` through `CTRL004`.
The corrected bin files must contain exactly `ONCO001` through `ONCO006` and
no controls. A missing or different `.done` value means that the PoN is not
complete, even if partial files are present.

Four controls form a small, run-specific reference. Review the leave-one-out
QC before interpreting CNA calls. OncoTracer is for research use and is not a
standalone diagnostic system.
