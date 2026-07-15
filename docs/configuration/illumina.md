# Illumina Configuration

Use this route for single-end or paired-end Illumina FASTQ files. OncoTracer aligns the reads, calls broad copy-number changes with SAMURAI/qDNAseq, refines CNA boundaries from the BAM files, creates tables and plots, and writes a run summary.

## Recommended: create the YAML automatically

Choose automatic setup when each sample has one compressed R1/R2 pair in a single folder.

### 1. Arrange the FASTQs

From the cloned repository, create a project tree and place the reads in `illumina_fastq/`:

```bash
cd oncotracer                                                        # enter the repository
mkdir -p project/input/illumina_fastq project/config/illumina project/runs # create project directories
find project/input/illumina_fastq -maxdepth 1 -type f -name '*.fastq.gz' -print | sort # inspect the reads
```

The filenames must share the sample prefix:

```text
oncotracer/
└── project/
    └── input/
        └── illumina_fastq/
            ├── Patient_A_R1.fastq.gz
            ├── Patient_A_R2.fastq.gz
            ├── Patient_B_R1.fastq.gz
            └── Patient_B_R2.fastq.gz
```

`Patient_A_R1.fastq.gz` pairs with `Patient_A_R2.fastq.gz`. Names ending in `_1.fastq.gz` and `_2.fastq.gz`, and the corresponding `.fq.gz` forms, are also accepted. Automatic setup expects exactly one pair per sample at the top level of this folder.

### 2. Create the sample table

```bash
nano project/input/illumina_fastq/samples.csv                         # create the sample-to-status table
```

Enter the header and one row per sample:

```csv
sample_name,status
Patient_A,TUMOR
Patient_B,NORMAL
```

The `sample_name` must exactly match the filename text before `_R1`/`_R2`. `status` must be `TUMOR` or `NORMAL` (case-insensitive). In Nano, save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`.

Inspect the table:

```bash
sed -n '1,20p' project/input/illumina_fastq/samples.csv               # verify the header and rows
```

### 3. Generate the configuration

```bash
ROOT=$(pwd)                                                           # save the absolute repository path
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$ROOT/project/input/illumina_fastq" \
  --sample_table "$ROOT/project/input/illumina_fastq/samples.csv" \
  --auto_config_dir "$ROOT/project/config/illumina" \
  --auto_outdir "$ROOT/project/runs/illumina_auto"                    # generate files and stop
```

This command checks that every row has exactly one R1/R2 pair and that each gzip file is complete. It creates:

```text
project/config/illumina/
├── illumina.auto.yml          # pass this file to -params-file
└── illumina.samplesheet.csv   # detected R1/R2 paths
```

It does **not** start alignment or CNA analysis.

### 4. Inspect the generated files

```bash
sed -n '1,120p' project/config/illumina/illumina.auto.yml              # inspect settings and absolute paths
sed -n '1,20p' project/config/illumina/illumina.samplesheet.csv        # inspect detected pairs and status values
```

The generated YAML will resemble this. It is a **YAML example**, not a terminal command:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/runs/illumina_auto
illumina_samplesheet: /home/student/oncotracer/project/config/illumina/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
```

The generator chooses an absolute `lpwgs_root` that contains the reads, generated configuration, and results. Your path will differ from `/home/student/oncotracer`.

### 5. Check wiring, run, and inspect the summary

```bash
nextflow run main.nf -stub-run --docker -params-file project/config/illumina/illumina.auto.yml # optional workflow-wiring check
nextflow run main.nf --docker -params-file project/config/illumina/illumina.auto.yml -resume   # real analysis
cat project/runs/illumina_auto/06_workflow_summary/workflow_summary.txt                         # show key output paths
```

The stub command uses placeholder outputs; it does not analyze or fully validate the real reads. The second command is the real run. Use `--singularity` instead of `--docker` on a configured HPC system.

## Second option: manual setup

Choose manual setup for single-end reads, when FASTQ naming does not match automatic paired-read detection, or when you need advanced settings.

### 1. Create the samplesheet

This example assumes `pwd` prints `/home/student/oncotracer`. Replace that prefix everywhere if your clone is elsewhere.

```bash
cd oncotracer
mkdir -p project/input/illumina_fastq project/runs                     # create directories if absent
nano project/input/illumina.samplesheet.csv                            # create the paired-read table
```

Enter:

```csv
sample,fastq_1,fastq_2,status
Patient_A,/home/student/oncotracer/project/input/illumina_fastq/Patient_A_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Patient_A_R2.fastq.gz,tumor
Patient_B,/home/student/oncotracer/project/input/illumina_fastq/Patient_B_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Patient_B_R2.fastq.gz,normal
```

Each row is one biological sample. `fastq_1` and `fastq_2` are absolute paths; `status` is `tumor` or `normal`. Save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`.

For a single-end library, retain the four-column header and leave `fastq_2` empty:

```csv
sample,fastq_1,fastq_2,status
Patient_SE,/home/student/oncotracer/project/input/illumina_fastq/Patient_SE.fastq.gz,,tumor
```

One OncoTracer invocation must contain only one layout: all rows single-end or all rows paired-end. Mixed-layout samplesheets stop with an error because qDNAseq applies one paired-read setting to the run.

Check the table and files:

```bash
sed -n '1,20p' project/input/illumina.samplesheet.csv                 # inspect the saved CSV
ls -lh project/input/illumina_fastq/Patient_A_R1.fastq.gz             # confirm R1 exists and is not empty
ls -lh project/input/illumina_fastq/Patient_A_R2.fastq.gz             # confirm R2 exists and is not empty
gzip -t project/input/illumina_fastq/Patient_A_R1.fastq.gz            # no output means gzip is valid
gzip -t project/input/illumina_fastq/Patient_A_R2.fastq.gz            # test the mate too
```

### 2. Copy and edit the YAML

```bash
cp params/illumina.minimal.yml params/my_illumina.yml                  # preserve the versioned template
nano params/my_illumina.yml                                           # replace the example paths
```

A complete minimal file is:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer
outdir: /home/student/oncotracer/project/runs/my_first_illumina_run
illumina_samplesheet: /home/student/oncotracer/project/input/illumina.samplesheet.csv
force: false
```

The remaining settings use tested defaults: `solid_biopsy`, `qdnaseq`, and `100` kb bins. OncoTracer automatically writes the upstream results to `outdir/01_samurai_illumina`; do not add a separate SAMURAI output path.

Save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`. Inspect the result:

```bash
sed -n '1,120p' params/my_illumina.yml                                # verify every saved value
```

### How to edit a YAML file from the terminal

The video below shows the same manual task: copy the example YAML, open it in Nano, replace the project root, samplesheet, and output paths, save with `Ctrl+O` and `Enter`, exit with `Ctrl+X`, inspect the saved YAML, perform a stub wiring check, and start the real run. The pauses are intentional so each edit can be followed.

<video controls preload="metadata" poster="../../assets/tutorial/edit_yaml_with_nano_poster.png" style="width:100%;max-width:960px">
  <source src="../../assets/tutorial/edit_yaml_with_nano.mp4" type="video/mp4">
  Your browser cannot play the embedded video. <a href="../../assets/tutorial/edit_yaml_with_nano.mp4">Open the MP4 video</a>.
</video>

### 3. Check wiring and run

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml # optional workflow-wiring check
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume   # real analysis
cat project/runs/my_first_illumina_run/06_workflow_summary/workflow_summary.txt # inspect final locations
```

## What each setting means

| Setting | Type and accepted value | Default | Purpose |
| --- | --- | --- | --- |
| `mode` | text: `illumina` | required | Selects the Illumina route. |
| `lpwgs_root` | absolute directory | site-specific | Common parent mounted into the container. Every input and output must be below it. |
| `outdir` | absolute directory | required | Results for this run. Use a new directory for a new experiment. |
| `illumina_samplesheet` | absolute CSV path | required | Four columns: `sample,fastq_1,fastq_2,status`; leave `fastq_2` empty for a single-end run. |
| `illumina_analysis_type` | text: `solid_biopsy` | `solid_biopsy` | Standard SAMURAI analysis preset for this route. |
| `illumina_caller` | text: `qdnaseq` | `qdnaseq` | CNA caller used by the current Illumina workflow. |
| `illumina_binsize_kb` | positive integer, kb | `100` | Width of the initial copy-number bins. |
| `run_cna_classifier` | Boolean | `false` | Adds classifier/pathology outputs when `true`. |
| `force` | Boolean | `false` | Requests supported refresh behavior. Keep `false` for real runs. |

For all optional settings, see the [parameter reference](parameter_reference.md).
