<a id="quick-start"></a>

# QuickStart Example 1: one Illumina and one ONT sample

This tutorial downloads about **225 MB** of public reads, creates one Illumina YAML and one ONT YAML, runs both workflows, and verifies the main outputs.

Preparation is independent of the analysis backend. The walkthrough first demonstrates Conda, and [Choose one of four execution methods](#choose-one-of-four-execution-methods) provides complete Docker, Singularity/Apptainer, Poetry, and Conda command sets. Use only one method for a normal run.

[![Six-step OncoTracer QuickStart flow](assets/tutorial/quickstart_flow.svg)](assets/tutorial/quickstart_flow.svg)

## Estimated time for this analysis

The first Conda run also creates the software environments. The example reads are small, but an uncached analysis downloads hg38 and creates the alignment indexes. Indexing can take tens of minutes and requires substantial memory, so provide at least 80 GiB of addressable RAM. Later `-resume` runs reuse the environments, reference, indexes, and unchanged completed tasks.

## 1. Clone OncoTracer

```bash
# Clone OncoTracer into a given directory.

git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
```

## 2. Prepare the public reads and YAML files

```bash
# Download the reads, validate them, and create both YAML files.
nextflow run main.nf --make_test \
  --test_root "test"
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
test/
├── configs/
│   ├── illumina.quickstart.yml
│   └── ont.quickstart.yml
├── public/
│   ├── illumina_ERR12341627/
│   └── ont_DRR165691/
└── runs/
```

```bash
# Inspect the generated run configurations.
ls -1 "test/configs"
sed -n '1,120p' "test/configs/illumina.quickstart.yml"
sed -n '1,120p' "test/configs/ont.quickstart.yml"
```

A YAML is a saved run plan containing paths and analysis settings. It does not contain sequencing reads or results.

## 3. Run the Illumina analysis

```bash
# Run the generated Illumina YAML with Conda.
nextflow run main.nf --conda \
  -params-file "test/configs/illumina.quickstart.yml" \
  -work-dir "test/work/illumina" \
  -resume
```

Wait for this command to finish before starting the ONT run. The outer task can remain at `RUN_ILLUMINA_SAMURAI (0 of 1)` while nested SAMURAI tasks are active.

```bash
# Display the completed Illumina summary.
head -n 3 "test/runs/illumina/06_workflow_summary/workflow_summary.txt"
```

The summary should begin with `mode=illumina` and `dataset=illumina_qdnaseq_100kb`.

## 4. Run the ONT analysis

```bash
# Run the generated ONT YAML with Conda.
nextflow run main.nf --conda \
  -params-file "test/configs/ont.quickstart.yml" \
  -work-dir "test/work/ont" \
  -resume
```

```bash
# Display the completed ONT summary.
head -n 3 "test/runs/ont/06_workflow_summary/workflow_summary.txt"
```

The summary should begin with `mode=ont` and `dataset=ONT_ichorcna_500kb`.

## 5. Verify the outputs

```bash
# Verify the required outputs from both runs.
python3 "examples/quickstart/verify_outputs.py" \
  --test-root "test"
```

A successful check ends with:

```text
SUCCESS: both QuickStart workflows completed and required outputs were found.
```

Important result folders are:

```text
test/runs/illumina/
├── 01_samurai_illumina/
├── 03_cna_codification/
├── 04_cna_custom_plots/
└── 06_workflow_summary/

test/runs/ont/
├── 01_samurai_ont/
├── 03_cna_codification/
├── 04_cna_custom_plots/
└── 06_workflow_summary/
```

These public outputs are also shown in the [Results Gallery](gallery.md).

## Choose one of four execution methods

Run the common preparation command once, then choose exactly one analysis method below. Illumina must finish before ONT.

```bash
# Prepare or revalidate the public reads and YAML files.
nextflow run main.nf --make_test \
  --test_root "test"
```

### Docker

```bash
# Run both examples with Docker.
nextflow run main.nf --docker \
  -params-file "test/configs/illumina.quickstart.yml" \
  -work-dir "test/work/docker-illumina" \
  -resume

nextflow run main.nf --docker \
  -params-file "test/configs/ont.quickstart.yml" \
  -work-dir "test/work/docker-ont" \
  -resume
```

### Singularity or Apptainer

```bash
# Run both examples through Singularity or Apptainer.
nextflow run main.nf --singularity \
  -params-file "test/configs/illumina.quickstart.yml" \
  -work-dir "test/work/singularity-illumina" \
  -resume

nextflow run main.nf --singularity \
  -params-file "test/configs/ont.quickstart.yml" \
  -work-dir "test/work/singularity-ont" \
  -resume
```

### Poetry launcher

```bash
# Install Poetry and run both examples with the Docker backend.
poetry install --no-interaction

poetry run oncotracer --repo-dir . --backend docker \
  -params-file "test/configs/illumina.quickstart.yml" \
  -work-dir "test/work/poetry-illumina" \
  -resume

poetry run oncotracer --repo-dir . --backend docker \
  -params-file "test/configs/ont.quickstart.yml" \
  -work-dir "test/work/poetry-ont" \
  -resume
```

### Conda

```bash
# Run both examples with native Conda environments.
nextflow run main.nf --conda \
  -params-file "test/configs/illumina.quickstart.yml" \
  -work-dir "test/work/conda-illumina" \
  -resume

nextflow run main.nf --conda \
  -params-file "test/configs/ont.quickstart.yml" \
  -work-dir "test/work/conda-ont" \
  -resume
```

After both analyses finish, run the same verifier regardless of the selected method:

```bash
# Verify both completed QuickStart runs.
python3 "examples/quickstart/verify_outputs.py" \
  --test-root "test"
```

The Poetry example uses Docker as its scientific backend. The launcher also accepts `--backend singularity` and `--backend conda`.

## Continue from here

- [Automatic Setup](auto_params.md) generates a YAML for your own Illumina or ONT FASTQs and shows the same four execution methods.
- [QuickStart Example 2](public_cohort.md) runs three public HCC1143 libraries.
- [Full Tutorial](full_tutorial.md) runs the 12 public PRJNA754199 libraries.
- [Other Example Run: six tumors and four controls](six_tumor_four_control.md) is a mock example illustrating how four normal controls are used to correct six tumor profiles.
