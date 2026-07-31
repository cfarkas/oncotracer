<a id="quick-start"></a>

# QuickStart Example 1: one Illumina and one ONT sample

This tutorial downloads about **225 MB** of public reads, creates one Illumina YAML and one ONT YAML, runs both workflows, and verifies the main outputs.

The commands use Docker. On an HPC system configured with Singularity or Apptainer, replace `--docker` with `--singularity`. See [Installation](installation.md) and the maintained [Docker image](https://hub.docker.com/r/carlosfarkas/oncotracer).

[![Six-step OncoTracer QuickStart flow](assets/tutorial/quickstart_flow.svg)](assets/tutorial/quickstart_flow.svg)

## Estimated time for this analysis

The example reads are small, but an uncached first analysis also downloads hg38 and creates a BWA index. Indexing commonly takes **30–60 minutes**, and the pinned task requests 72 GB, so provide at least 80 GiB of addressable RAM. Later `-resume` runs reuse a valid index.

## 1. Clone OncoTracer

Use `/path/to/my/directory/oncotracer` as the repository path throughout this tutorial.

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Clone OncoTracer into that directory.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"

# Enter the repository and confirm the path.
cd "$REPO_DIR"
pwd
```

Skip the clone command when the repository already exists.

## 2. Prepare the public reads and YAML files

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Download and validate the public Illumina and ONT reads, then create both YAML files.
nextflow run "$REPO_DIR/main.nf" --make_test \
  --test_root "$REPO_DIR/test"
```

This step checks file size, MD5, and gzip integrity. It does not align reads or call CNAs.

The public examples use these sample mappings.

### Illumina

```csv
sample_name,status
ERR12341627,TUMOR
```

### ONT

```csv
barcode,sample_name,status
barcode01,DRR165691,TUMOR
```

The preparation command writes:

```text
/path/to/my/directory/oncotracer/test/
├── configs/
│   ├── illumina.quickstart.yml
│   └── ont.quickstart.yml
├── public/
│   ├── illumina_ERR12341627/
│   └── ont_DRR165691/
└── runs/
```

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# List the two generated run configurations.
ls -1 "$REPO_DIR/test/configs"

# Inspect the Illumina YAML.
sed -n '1,120p' "$REPO_DIR/test/configs/illumina.quickstart.yml"

# Inspect the ONT YAML.
sed -n '1,120p' "$REPO_DIR/test/configs/ont.quickstart.yml"
```

A YAML is a saved run plan containing paths and analysis settings. It does not contain sequencing reads or results.

## 3. Run the Illumina analysis

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Run the generated Illumina YAML with Docker and a reusable work directory.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/illumina" \
  -resume
```

Wait for this command to finish before starting the ONT run. The outer task can remain at `RUN_ILLUMINA_SAMURAI (0 of 1)` while nested SAMURAI tasks are active.

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Display the first lines of the completed Illumina summary.
head -n 3 "$REPO_DIR/test/runs/illumina/06_workflow_summary/workflow_summary.txt"
```

The summary should begin with `mode=illumina` and `dataset=illumina_qdnaseq_100kb`.

## 4. Run the ONT analysis

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Run the generated ONT YAML after the Illumina run finishes.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/ont" \
  -resume
```

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Display the first lines of the completed ONT summary.
head -n 3 "$REPO_DIR/test/runs/ont/06_workflow_summary/workflow_summary.txt"
```

The summary should begin with `mode=ont` and `dataset=ONT_ichorcna_500kb`.

## 5. Verify the outputs

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Verify the required summary, CNA table, and plot files from both runs.
python3 "$REPO_DIR/examples/quickstart/verify_outputs.py" \
  --test-root "$REPO_DIR/test"
```

A successful check ends with:

```text
SUCCESS: both QuickStart workflows completed and required outputs were found.
```

Important result folders are:

```text
/path/to/my/directory/oncotracer/test/runs/illumina/
├── 01_samurai_illumina/
├── 03_cna_codification/
├── 04_cna_custom_plots/
└── 06_workflow_summary/

/path/to/my/directory/oncotracer/test/runs/ont/
├── 01_samurai_ont/
├── 03_cna_codification/
├── 04_cna_custom_plots/
└── 06_workflow_summary/
```

These public outputs are also shown in the [Results Gallery](gallery.md).

## Exact commands to repeat or resume

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Prepare or revalidate the public reads and YAML files.
nextflow run "$REPO_DIR/main.nf" --make_test \
  --test_root "$REPO_DIR/test"

# Run or resume the Illumina example.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/illumina" \
  -resume

# Run or resume the ONT example after Illumina finishes.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/ont" \
  -resume

# Verify both completed runs.
python3 "$REPO_DIR/examples/quickstart/verify_outputs.py" \
  --test-root "$REPO_DIR/test"
```

## Continue from here

- [Automatic Setup](auto_params.md) generates a YAML for your own Illumina or ONT FASTQs.
- [QuickStart Example 2](public_cohort.md) runs three public HCC1143 libraries.
- [Full Tutorial](full_tutorial.md) runs the 12 public PRJNA754199 libraries.
- [Other Example Run: six tumors and four controls](six_tumor_four_control.md) is a command template only. The `ONCO001`–`ONCO006` and `CTRL001`–`CTRL004` FASTQs are not included or downloaded.
