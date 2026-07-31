# Developer Guide

This guide is for contributors changing code, tests, examples, or documentation. Users analyzing FASTQs should start with [QuickStart Example 1](quick_start.md) or [Automatic Setup](auto_params.md).

## Repository map

```text
main.nf                         # top-level DSL2 workflow
nextflow.config                 # defaults and runtime profiles
params/                         # user-facing YAML templates
bin/scripts/                    # launch, download, auto-config, and refinement helpers
bin/cna_codification/           # event conversion and plotting code
bin/cna_classifier_nf/          # optional classifier/report workflow
docs/                           # MkDocs source
examples/                       # public examples and manifests
tests/                          # local unit and integration checks
test/                           # downloaded fixtures and generated outputs
```

Do not commit patient data, credentials, downloaded references, BAMs, or FASTQs.

## Start a branch

```bash
# Clone the repository.
git clone https://github.com/cfarkas/oncotracer.git

# Enter the repository.
cd oncotracer

# Create a focused branch.
git switch -c your-change-name

# Confirm the branch and worktree state.
git status --short --branch
```

## Prepare the public QuickStart data

```bash
# Download and validate the public QuickStart reads and generate both YAML files.
nextflow run main.nf --make_test

# Inspect the generated Illumina YAML.
sed -n '1,140p' test/configs/illumina.quickstart.yml

# Inspect the generated ONT YAML.
sed -n '1,140p' test/configs/ont.quickstart.yml
```

## Fast checks

```bash
# Check Bash syntax in versioned shell scripts.
find bin examples tests -type f -name '*.sh' -print0 \
  | xargs -0 -n1 bash -n

# Run the Automatic Setup tests.
bash tests/test_generate_auto_params.sh

# Run the Illumina preflight tests.
bash tests/test_illumina_pon_preflight.sh

# Run the local qDNAseq PoN helper tests.
bash tests/test_qdnaseq_local_pon.sh

# Run the documentation style audit.
python3 tests/test_documentation_examples.py

# Check Illumina workflow wiring.
nextflow run main.nf -stub-run --docker \
  -params-file test/configs/illumina.quickstart.yml

# Check ONT workflow wiring.
nextflow run main.nf -stub-run --docker \
  -params-file test/configs/ont.quickstart.yml

# Check whitespace and conflict markers.
git diff --check

# Review the exact changed files.
git status --short
```

A stub run checks workflow wiring but does not execute the scientific tools.

## Full QuickStart verification

```bash
# Run or resume the Illumina QuickStart.
nextflow run main.nf --docker \
  -params-file test/configs/illumina.quickstart.yml \
  -work-dir test/work/illumina \
  -resume

# Run or resume the ONT QuickStart after Illumina finishes.
nextflow run main.nf --docker \
  -params-file test/configs/ont.quickstart.yml \
  -work-dir test/work/ont \
  -resume

# Verify required outputs from both workflows.
python3 examples/quickstart/verify_outputs.py \
  --test-root test
```

Run an uncached analysis when changing task commands, containers, callers, parsers, or result filenames.

## Test the three-sample public example

Download the six FASTQs with [QuickStart Example 2](public_cohort.md), then run:

```bash
# Generate the HCC1143 YAML and samplesheet.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder test/public/hcc1143_lpwgs \
  --sample_table test/public/hcc1143_lpwgs/samples.csv \
  --auto_config_dir test/configs/hcc1143_lpwgs \
  --auto_outdir test/runs/hcc1143_lpwgs

# Check the HCC1143 workflow wiring.
nextflow run main.nf -stub-run --docker \
  -params-file test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir test/work/hcc1143_lpwgs_stub

# Run or resume the HCC1143 analysis.
nextflow run main.nf --docker \
  -params-file test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir test/work/hcc1143_lpwgs \
  -resume
```

The six-tumor/four-control page is not a public test. Its 20 FASTQs are not included and cannot be tested without user-provided data.

## Build the documentation

```bash
# Create an isolated Python environment.
python3 -m venv .venv-docs

# Activate the environment.
source .venv-docs/bin/activate

# Update pip.
python -m pip install --upgrade pip

# Install the documentation dependencies.
python -m pip install -r docs/requirements.txt

# Build the documentation and fail on broken links or warnings.
mkdocs build --strict

# Preview the documentation locally.
mkdocs serve
```

Every example command block should explain each command with a short `#` comment. Public examples should include accession, checksum validation, resource estimates, an exact sample table, and explicit output checks.

## Before requesting review

```bash
# Summarize the changed files.
git diff --stat

# Check whitespace and conflict markers.
git diff --check

# Run the documentation audit.
python3 tests/test_documentation_examples.py

# Build MkDocs strictly.
mkdocs build --strict

# Review the final worktree state.
git status --short
```

Summarize which tests ran, which runtime and image were used, whether analyses were cached or fresh, and any test that could not be completed.
