# Developer guide

This guide is for contributors changing code, tests, examples, or documentation. Users running FASTQs should start with [QuickStart Example 1](quick_start.md) or [Automatic Setup](auto_params.md).

## Repository map

```text
main.nf                         # top-level DSL2 workflow
nextflow.config                 # defaults and runtime profiles
params/                         # user-facing YAML templates
bin/scripts/                    # launch, download, automatic-setup, and refinement helpers
bin/cna_codification/           # event conversion and plotting code
bin/cna_classifier_nf/          # optional nested classifier/report workflow
docs/                           # MkDocs source
examples/                       # public examples and provenance files
tests/                          # focused source and behavior tests
test/                           # downloaded fixtures, generated configs, work, and outputs
run_test.sh                     # legacy maintainer helper
```

Do not commit patient data, credentials, container tokens, downloaded references, BAMs, FASTQs, `work/`, `.nextflow/`, or generated `site/` content.

## Start from a fresh branch

```bash
# Clone the repository and create a focused working branch.
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
git switch -c your-change-name

# Confirm that the working tree contains only intended changes.
git status --short
```

## Prepare the small public test data

```bash
# Download and validate the public Illumina and ONT reads and generate their YAML files.
nextflow run main.nf --make_test

# Inspect both generated run plans.
sed -n '1,120p' test/configs/illumina.quickstart.yml
sed -n '1,120p' test/configs/ont.quickstart.yml
```

Keep download preparation separate from workflow testing so a network or checksum error is not mistaken for a pipeline error.

## Fast checks for every change

```bash
# Check Bash syntax in versioned shell scripts.
find bin examples tests -type f -name '*.sh' -print0 \
  | xargs -0 -n1 bash -n

# Run the focused automatic-setup and panel-of-normals tests.
bash tests/test_generate_auto_params.sh
bash tests/test_illumina_pon_preflight.sh
bash tests/test_qdnaseq_local_pon.sh

# Check documentation wording and example command blocks.
python3 tests/test_docs_style.py

# Check the generated Illumina and ONT workflow connections.
nextflow run main.nf -stub-run --docker \
  -params-file test/configs/illumina.quickstart.yml
nextflow run main.nf -stub-run --docker \
  -params-file test/configs/ont.quickstart.yml

# Check whitespace and review the exact source changes.
git diff --check
git status --short
```

A stub run validates parameters, channels, and process connections. It does not execute the scientific tools or validate final output files.

## Full QuickStart verification

Run the top-level analyses one after the other:

```bash
# Run or resume the public Illumina example.
nextflow run main.nf --docker \
  -params-file test/configs/illumina.quickstart.yml \
  -work-dir test/work/illumina \
  -resume

# Run or resume the public ONT example after Illumina finishes.
nextflow run main.nf --docker \
  -params-file test/configs/ont.quickstart.yml \
  -work-dir test/work/ont \
  -resume

# Verify the required output groups from both workflows.
python3 examples/quickstart/verify_outputs.py \
  --test-root test
```

At least one uncached run is required when changing task commands, images, callers, parsing, or expected output files.

Check representative outputs:

```bash
# Require the main Illumina table and plot.
test -s test/runs/illumina/03_cna_codification/cna_events.tsv
test -s test/runs/illumina/04_cna_custom_plots/cna_per_sample_pages.pdf

# Require the main ONT table and plot.
test -s test/runs/ont/03_cna_codification/cna_events.tsv
test -s test/runs/ont/04_cna_custom_plots/cna_per_sample_pages.pdf

# Read both workflow summaries.
cat test/runs/illumina/06_workflow_summary/workflow_summary.txt
cat test/runs/ont/06_workflow_summary/workflow_summary.txt
```

Review the tables and plots after scientific or visualization changes; file existence alone is not enough.

## Test the HCC1143 public example when affected

First download and validate the six FASTQs using [QuickStart Example 2](public_cohort.md#2-download-the-six-fastq-files), then run:

```bash
# Generate the HCC1143 YAML and samplesheet.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder test/public/hcc1143_lpwgs \
  --sample_table test/public/hcc1143_lpwgs/samples.csv \
  --auto_config_dir test/configs/hcc1143_lpwgs \
  --auto_outdir test/runs/hcc1143_lpwgs

# Check the generated workflow connections.
nextflow run main.nf -stub-run --docker \
  -params-file test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir test/work/hcc1143_lpwgs_stub

# Run or resume the three-library analysis.
nextflow run main.nf --docker \
  -params-file test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir test/work/hcc1143_lpwgs \
  -resume
```

Record the commit, image identity, start/end time, reference, caller, bin size, and inspected outputs before publishing a gallery result.

The six-tumor/four-control page is not a runnable repository test. Its `ONCO001`–`ONCO006` and `CTRL001`–`CTRL004` FASTQs are not included or downloaded; it is a user-data command template.

## Build the documentation

```bash
# Create and activate an isolated documentation environment.
python3 -m venv .venv-docs
source .venv-docs/bin/activate

# Install the documentation dependencies and build with strict checks.
python -m pip install --upgrade pip
python -m pip install -r docs/requirements.txt
mkdocs build --strict

# Optionally preview the site locally.
mkdocs serve
```

Every example command box should identify its working directory, comment the commands briefly, use current parameter names, and distinguish configuration from the real analysis. Public data claims must include an accession and provenance source.

## Changes that require focused review

Document and test changes to these user-visible interfaces:

- sample and barcode matching rules;
- genome build and coordinate conventions;
- qDNAseq or ichorCNA defaults;
- boundary-refinement behavior;
- event and notation schemas;
- numbered output directories and filenames;
- classifier settings and evidence wording;
- research-use limitations.

Do not hide a scientific behavior change inside a formatting or dependency update. Add a focused test and state the expected before/after behavior.

## Adding a public example

A runnable public example should include:

1. project/run accessions and the study citation;
2. one stable download URL per file;
3. byte counts and checksums;
4. `gzip -t` validation;
5. inclusion and exclusion rules;
6. the exact sample table;
7. resource expectations;
8. generated YAML, stub check, and real-run commands;
9. output verification and gallery provenance.

Do not commit large public FASTQs. Download them into ignored example/test storage.

A local-data template must state prominently that the data are not included and that the commands cannot run until the user supplies the files.

## Before requesting review

```bash
# Review the change set and run the strict documentation build.
git diff --stat
git diff --check
git status --short
python3 tests/test_docs_style.py
mkdocs build --strict
```

Summarize the routes tested, whether runs were fresh or resumed, the selected runtime/image, and any test that was not performed. Deploy GitHub Pages only after the source change is reviewed and merged.
