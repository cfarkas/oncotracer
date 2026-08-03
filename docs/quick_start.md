<a id="quick-start"></a>

# QuickStart Example 1: one Illumina and one ONT sample

This tutorial downloads about **225 MB** of public reads, creates one Illumina YAML and one ONT YAML, runs both workflows, and verifies the main outputs.

Preparation is independent of the analysis backend. The walkthrough first demonstrates Conda, and [Choose one of four execution methods](#choose-one-of-four-execution-methods) provides complete Docker, Singularity/Apptainer, Poetry, and Conda command sets. Use only one method for a normal run.

[![Six-step OncoTracer QuickStart flow](assets/tutorial/quickstart_flow.svg)](assets/tutorial/quickstart_flow.svg)

## Estimated time for this analysis

The first Conda run also creates the software environments. The example reads are small, but an uncached analysis downloads hg38 and creates the alignment indexes. Indexing can take tens of minutes and requires substantial memory, so provide at least 80 GiB of addressable RAM. Later `-resume` runs reuse the environments, reference, indexes, and unchanged completed tasks.

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

# Download the reads, validate them, and create both YAML files.
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

# Run the generated Illumina YAML with Conda and a reusable work directory.
nextflow run "$REPO_DIR/main.nf" --conda \
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

# Run the generated ONT YAML with Conda after the Illumina run finishes.
nextflow run "$REPO_DIR/main.nf" --conda \
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

## Choose one of four execution methods

Run the common preparation command once, then choose exactly one analysis method below. Illumina must finish before ONT.

```bash
# Set the repository path and prepare or revalidate the public reads and YAML files.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"
nextflow run "$REPO_DIR/main.nf" --make_test \
  --test_root "$REPO_DIR/test"
```

### Docker

```bash
# Run or resume the Illumina example with Docker.
REPO_DIR=/path/to/my/directory/oncotracer
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/docker-illumina" \
  -resume

# Run or resume the ONT example with Docker after Illumina finishes.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/docker-ont" \
  -resume
```

### Singularity or Apptainer

```bash
# Run or resume the Illumina example through Singularity or Apptainer.
REPO_DIR=/path/to/my/directory/oncotracer
nextflow run "$REPO_DIR/main.nf" --singularity \
  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/singularity-illumina" \
  -resume

# Run or resume the ONT example through Singularity or Apptainer.
nextflow run "$REPO_DIR/main.nf" --singularity \
  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/singularity-ont" \
  -resume
```

### Poetry launcher

```bash
# Install the locked Poetry launcher once.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"
poetry install --no-interaction

# Run or resume the Illumina example through Poetry with Docker.
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/poetry-illumina" \
  -resume

# Run or resume the ONT example through Poetry with Docker.
poetry run oncotracer --repo-dir "$REPO_DIR" --backend docker \
  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/poetry-ont" \
  -resume
```

### Conda

```bash
# Run or resume the Illumina example with native Conda environments.
REPO_DIR=/path/to/my/directory/oncotracer
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/conda-illumina" \
  -resume

# Run or resume the ONT example with native Conda environments.
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/conda-ont" \
  -resume
```

After both analyses finish, run the same verifier regardless of the selected method:

```bash
# Verify both completed QuickStart runs.
REPO_DIR=/path/to/my/directory/oncotracer
python3 "$REPO_DIR/examples/quickstart/verify_outputs.py" \
  --test-root "$REPO_DIR/test"
```

The Poetry example uses Docker as its scientific backend. The launcher also accepts `--backend singularity` and `--backend conda`.

## Continue from here

- [Automatic Setup](auto_params.md) generates a YAML for your own Illumina or ONT FASTQs and shows the same four execution methods.
- [QuickStart Example 2](public_cohort.md) runs three public HCC1143 libraries.
- [Full Tutorial](full_tutorial.md) runs the 12 public PRJNA754199 libraries.
- [Other Example Run: six tumors and four controls](six_tumor_four_control.md) is a mock example illustrating how four normal controls are used to correct six tumor profiles.
