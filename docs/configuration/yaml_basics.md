# YAML and Paths

A YAML file is a small plain-text run configuration. It tells OncoTracer which sequencing route to use, where the inputs are, and where results belong. FASTQ reads are **not** stored in YAML.

## Choose how to create the YAML

| Situation | Use this route |
| --- | --- |
| You have a folder of paired Illumina FASTQs or ONT barcode folders | **Automatic setup** with `--auto_params` (recommended) |
| You need a custom samplesheet, custom reference, or advanced settings | Copy and edit a YAML template manually |
| You want to learn with public data | Use `--make_test`; see [Quick Start](../quick_start.md) |

## Recommended: generate it automatically

Automatic setup checks the supported FASTQ layout, writes the YAML, and—for Illumina—writes the four-column samplesheet. It stops after creating these files; it does not run the analysis.

From the repository root, first create a sample table. For Illumina:

```csv
sample_name,status
Sample_A,TUMOR
Sample_B,NORMAL
```

Then generate configuration files. Replace the two input paths with real absolute paths:

```bash
cd oncotracer                                                        # enter the cloned repository
ROOT=$(pwd)                                                           # save its absolute path
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$ROOT/project/input/illumina_fastq" \
  --sample_table "$ROOT/project/input/illumina_fastq/samples.csv" \
  --auto_config_dir "$ROOT/project/config/illumina" \
  --auto_outdir "$ROOT/project/runs/illumina_auto"                    # create YAML and samplesheet, then stop
sed -n '1,120p' project/config/illumina/illumina.auto.yml              # inspect the generated YAML
sed -n '1,20p' project/config/illumina/illumina.samplesheet.csv        # inspect detected read pairs
```

See [Automatic Setup](../auto_params.md) for the required Illumina filenames and ONT barcode table.

## YAML vocabulary

A setting has a **key**, a colon, and a **value**:

```yaml
mode: illumina                         # key: value; text after # is a comment
illumina_binsize_kb: 100               # integer; unit is kilobases
run_cna_classifier: false              # Boolean: true or false
pathology_csv: null                    # null means “not supplied”
ont_barcodes: barcode01,barcode02      # this OncoTracer field uses a comma-separated list
```

Follow these rules:

- Use spaces, not tabs.
- Keep one setting per line and keep the colon after the key.
- Use lowercase `true`, `false`, and `null`.
- A line beginning with `#` is a comment and is ignored.
- Do not repeat a key. A repeated setting is easy to miss and can override an earlier value.
- Avoid spaces and `#` in filenames. If they cannot be avoided, enclose the complete value in quotes.
- The YAML contains literal text. It does not expand `~`, `$HOME`, `$ROOT`, or `$(pwd)`.

## Understand the three important paths

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer
outdir: /home/student/oncotracer/project/runs/sample_a
illumina_samplesheet: /home/student/oncotracer/project/input/illumina.samplesheet.csv
```

- `mode` chooses the `illumina` or `ont` route.
- `lpwgs_root` is the absolute common parent that Docker or Singularity can mount. Put **every configured input, reference, and output below it**.
- `outdir` is the result directory for one run. Give each experiment a new `outdir`.
- `illumina_samplesheet` is a CSV that points to each paired FASTQ.

In this example, both `project/runs/sample_a` and `project/input/illumina.samplesheet.csv` begin with `/home/student/oncotracer`, so they are below `lpwgs_root`.

!!! warning "A path visible on the host may still be invisible in the container"
    Docker receives only the `lpwgs_root` tree. If a FASTQ is outside that tree, move or link it below the root, or choose a common parent that contains both the input and output paths.

## Find and check absolute paths

Run these commands in the cloned repository:

```bash
pwd                                                                    # print the absolute repository path
realpath project/input/illumina_fastq/Sample_A_R1.fastq.gz             # print one absolute FASTQ path
ls -lh project/input/illumina_fastq/Sample_A_R1.fastq.gz               # confirm that it exists and is not empty
gzip -t project/input/illumina_fastq/Sample_A_R1.fastq.gz              # verify gzip integrity; no output means success
```

On Linux, an absolute path starts with `/`. In WSL use a Linux path such as `/mnt/c/Users/Name/oncotracer`, not `C:\Users\Name\oncotracer`. Paths are case-sensitive.

## Manual setup

Use manual setup only when automatic detection does not fit the study.

```bash
cd oncotracer                                                        # enter the repository
mkdir -p project/input project/runs                                 # create input and result directories
cp params/illumina.minimal.yml params/my_illumina.yml               # preserve the versioned template
pwd                                                                  # copy this absolute path for the YAML
nano params/my_illumina.yml                                         # edit the copied file
```

A complete manual Illumina file looks like this. This is a **YAML example**, not a terminal command:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer
outdir: /home/student/oncotracer/project/runs/sample_a
illumina_samplesheet: /home/student/oncotracer/project/input/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
```

In Nano, move with the arrow keys and replace `/home/student/oncotracer` with the path printed by `pwd`. Save with `Ctrl+O`, press `Enter` to confirm the filename, then exit with `Ctrl+X`.

Inspect the saved file before running:

```bash
sed -n '1,120p' params/my_illumina.yml                               # print the file without editing it
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml # optional workflow-wiring check
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume   # real analysis
```

`-stub-run` creates placeholder task outputs and checks workflow wiring. It does **not** validate the real FASTQs, tools, reference downloads, or scientific results. Perform the file checks above, then run the real command.

Continue with [Illumina configuration](illumina.md), [ONT configuration](ont.md), or the [complete parameter reference](parameter_reference.md).
