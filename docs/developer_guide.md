# Developer Guide

This guide is for contributors changing code, tests, examples, or documentation. Users running FASTQs should start with [QuickStart Example 1](quick_start.md) or [Automatic Setup](auto_params.md).

The examples use `.` as the repository path.

## Repository map

```text
main.nf                         # top-level DSL2 workflow
nextflow.config                 # defaults and execution profiles
params/                         # user-facing YAML templates
bin/scripts/                    # launch, download, setup, and refinement helpers
bin/cna_codification/           # event conversion and plotting code
bin/cna_classifier_nf/          # optional nested classifier/report workflow
docs/                           # MkDocs source
examples/                       # public examples and provenance files
tests/                          # focused source and behavior tests
test/                           # downloaded fixtures, generated configs, work, and outputs
```

Do not commit patient data, credentials, registry tokens, downloaded references, BAMs, FASTQs, `work/`, `.nextflow/`, Conda caches, or generated `site/` content.

## Start from a fresh branch

```bash

# Clone OncoTracer into a given directory.

git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
git switch -c your-change-name

# Confirm that the working tree contains only intended changes.
git status --short
```

## Prepare the small public test data

```bash

# Create or reuse the Conda environment, download the public reads, and generate both YAML files.
nextflow run main.nf --make_test --conda \
  --lpwgs_root "test" \
  --test_root "test"

# Inspect both generated run plans.
sed -n '1,120p' "test/configs/illumina.quickstart.yml"
sed -n '1,120p' "test/configs/ont.quickstart.yml"
```

Keep download preparation separate from workflow testing so a network or checksum error is not mistaken for a pipeline error.

## Fast checks for every change

```bash

# Check Bash syntax in versioned shell scripts.
find "bin" "examples" "tests" \
  -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n

# Run the focused setup and panel-of-normals tests.
bash "tests/test_generate_auto_params.sh"
bash "tests/test_illumina_pon_preflight.sh"
bash "tests/test_qdnaseq_local_pon.sh"

# Check documentation wording, paths, and command blocks.
python3 "tests/test_docs_style.py"

# Check the generated Illumina and ONT workflow connections with Conda.
nextflow run main.nf -stub-run --conda \
  -params-file "test/configs/illumina.quickstart.yml"
nextflow run main.nf -stub-run --conda \
  -params-file "test/configs/ont.quickstart.yml"

# Check whitespace and review the exact source changes.
git diff --check
git status --short
```

A stub run validates parameters, channels, and process connections. It does not execute the scientific tools or validate final output files.

## Full QuickStart verification

```bash

# Run or resume the public Illumina example with Conda.
nextflow run main.nf --conda \
  -params-file "test/configs/illumina.quickstart.yml" \
  -work-dir "test/work/illumina" \
  -resume

# Run or resume the public ONT example after Illumina finishes.
nextflow run main.nf --conda \
  -params-file "test/configs/ont.quickstart.yml" \
  -work-dir "test/work/ont" \
  -resume

# Verify the required output groups from both workflows.
python3 "examples/quickstart/verify_outputs.py" \
  --test-root "test"
```

At least one uncached run is required when changing task commands, environments, images, callers, parsing, or expected output files.

```bash

# Require representative Illumina and ONT outputs.
test -s "test/runs/illumina/03_cna_codification/cna_events.tsv"
test -s "test/runs/illumina/04_cna_custom_plots/cna_per_sample_pages.pdf"
test -s "test/runs/ont/03_cna_codification/cna_events.tsv"
test -s "test/runs/ont/04_cna_custom_plots/cna_per_sample_pages.pdf"

# Read both workflow summaries.
cat "test/runs/illumina/06_workflow_summary/workflow_summary.txt"
cat "test/runs/ont/06_workflow_summary/workflow_summary.txt"
```

## Test the HCC1143 public example when affected

Download the six FASTQs using [QuickStart Example 2](public_cohort.md#2-download-the-six-fastq-files), then create the exact sample table:

```bash
# Set the standard repository and HCC1143 paths.
READS_DIR="test/public/hcc1143_lpwgs"

# Create or replace the exact HCC1143 table.
cat > "$READS_DIR/samples.csv" <<'CSV'
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
CSV
```

```bash

# Create or reuse the Conda environment, then generate the HCC1143 YAML and samplesheet.
nextflow run main.nf --auto_params --conda \
  --lpwgs_root "test" \
  --mode illumina \
  --reads_folder "test/public/hcc1143_lpwgs" \
  --sample_table "test/public/hcc1143_lpwgs/samples.csv" \
  --auto_config_dir "test/configs/hcc1143_lpwgs" \
  --auto_outdir "test/runs/hcc1143_lpwgs"

# Check the generated workflow connections.
nextflow run main.nf -stub-run --conda \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs_stub"

# Run or resume the three-library analysis with Conda.
nextflow run main.nf --conda \
  -params-file "test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "test/work/hcc1143_lpwgs" \
  -resume
```

The six-tumor/four-normal page is a mock configuration example used to test and explain how normal controls enable a local qDNAseq panel of normals.

## Build the documentation

```bash

# Create and activate an isolated documentation environment.
python3 -m venv ".venv-docs"
source ".venv-docs/bin/activate"

# Install dependencies and build with strict checks.
python -m pip install --upgrade pip
python -m pip install -r "docs/requirements.txt"
mkdocs build --strict

# Optionally preview the site locally.
mkdocs serve
```

Every Bash command box should begin with a brief `#` comment, use current parameter names, identify its working paths, and distinguish configuration from analysis. Public data claims must include accession and provenance information.

## Changes requiring focused review

Document and test changes to:

- sample and barcode matching rules;
- genome build and coordinate conventions;
- qDNAseq or ichorCNA defaults;
- boundary-refinement behavior;
- event and notation schemas;
- numbered output directories and filenames;
- classifier settings and evidence wording;
- research-use limitations.

Do not hide a scientific behavior change inside a formatting or dependency update.

## Adding an example

A runnable public example should include accessions, stable URLs, byte counts, checksums, `gzip -t`, exact sample metadata, resource expectations, generated YAML, validation commands, and output verification. Do not commit large public FASTQs.

A mock example should clearly state its teaching purpose and use obvious placeholder sample names.

## Before requesting review

```bash

# Review changes and run the documentation checks.
git diff --stat
git diff --check
git status --short
python3 "tests/test_docs_style.py"
mkdocs build --strict
```

Summarize the routes tested, whether runs were fresh or resumed, the selected execution environment, and any test not performed. Deploy GitHub Pages only after source changes are reviewed and merged.
