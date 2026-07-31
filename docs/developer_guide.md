# Developer Guide

This guide is for contributors changing code, tests, examples, or documentation. Users running FASTQs should start with [QuickStart Example 1](quick_start.md) or [Automatic Setup](auto_params.md).

The examples use `/path/to/my/directory/oncotracer` as the repository path.

## Repository map

```text
main.nf                         # top-level DSL2 workflow
nextflow.config                 # defaults and runtime profiles
params/                         # user-facing YAML templates
bin/scripts/                    # launch, download, setup, and refinement helpers
bin/cna_codification/           # event conversion and plotting code
bin/cna_classifier_nf/          # optional nested classifier/report workflow
docs/                           # MkDocs source
examples/                       # public examples and provenance files
tests/                          # focused source and behavior tests
test/                           # downloaded fixtures, generated configs, work, and outputs
```

Do not commit patient data, credentials, container tokens, downloaded references, BAMs, FASTQs, `work/`, `.nextflow/`, or generated `site/` content.

## Start from a fresh branch

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Clone the repository and create a focused working branch.
git clone https://github.com/cfarkas/oncotracer.git "$REPO_DIR"
cd "$REPO_DIR"
git switch -c your-change-name

# Confirm that the working tree contains only intended changes.
git status --short
```

## Prepare the small public test data

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Download and validate the public Illumina and ONT reads and generate their YAML files.
nextflow run "$REPO_DIR/main.nf" --make_test \
  --test_root "$REPO_DIR/test"

# Inspect both generated run plans.
sed -n '1,120p' "$REPO_DIR/test/configs/illumina.quickstart.yml"
sed -n '1,120p' "$REPO_DIR/test/configs/ont.quickstart.yml"
```

Keep download preparation separate from workflow testing so a network or checksum error is not mistaken for a pipeline error.

## Fast checks for every change

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Check Bash syntax in versioned shell scripts.
find "$REPO_DIR/bin" "$REPO_DIR/examples" "$REPO_DIR/tests" \
  -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n

# Run the focused setup and panel-of-normals tests.
bash "$REPO_DIR/tests/test_generate_auto_params.sh"
bash "$REPO_DIR/tests/test_illumina_pon_preflight.sh"
bash "$REPO_DIR/tests/test_qdnaseq_local_pon.sh"

# Check documentation wording, paths, and command blocks.
python3 "$REPO_DIR/tests/test_docs_style.py"

# Check the generated Illumina and ONT workflow connections.
nextflow run "$REPO_DIR/main.nf" -stub-run --docker \
  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml"
nextflow run "$REPO_DIR/main.nf" -stub-run --docker \
  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml"

# Check whitespace and review the exact source changes.
git diff --check
git status --short
```

A stub run validates parameters, channels, and process connections. It does not execute the scientific tools or validate final output files.

## Full QuickStart verification

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Run or resume the public Illumina example.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/illumina.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/illumina" \
  -resume

# Run or resume the public ONT example after Illumina finishes.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/ont.quickstart.yml" \
  -work-dir "$REPO_DIR/test/work/ont" \
  -resume

# Verify the required output groups from both workflows.
python3 "$REPO_DIR/examples/quickstart/verify_outputs.py" \
  --test-root "$REPO_DIR/test"
```

At least one uncached run is required when changing task commands, images, callers, parsing, or expected output files.

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Require representative Illumina and ONT outputs.
test -s "$REPO_DIR/test/runs/illumina/03_cna_codification/cna_events.tsv"
test -s "$REPO_DIR/test/runs/illumina/04_cna_custom_plots/cna_per_sample_pages.pdf"
test -s "$REPO_DIR/test/runs/ont/03_cna_codification/cna_events.tsv"
test -s "$REPO_DIR/test/runs/ont/04_cna_custom_plots/cna_per_sample_pages.pdf"

# Read both workflow summaries.
cat "$REPO_DIR/test/runs/illumina/06_workflow_summary/workflow_summary.txt"
cat "$REPO_DIR/test/runs/ont/06_workflow_summary/workflow_summary.txt"
```

## Test the HCC1143 public example when affected

Download the six FASTQs using [QuickStart Example 2](public_cohort.md#2-download-the-six-fastq-files), then create the exact sample table:

```bash
# Set the standard repository and HCC1143 paths.
REPO_DIR=/path/to/my/directory/oncotracer
READS_DIR="$REPO_DIR/test/public/hcc1143_lpwgs"

# Create or replace the exact HCC1143 table.
cat > "$READS_DIR/samples.csv" <<'CSV'
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
CSV
```

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Generate the HCC1143 YAML and samplesheet.
nextflow run "$REPO_DIR/main.nf" --auto_params \
  --mode illumina \
  --reads_folder "$REPO_DIR/test/public/hcc1143_lpwgs" \
  --sample_table "$REPO_DIR/test/public/hcc1143_lpwgs/samples.csv" \
  --auto_config_dir "$REPO_DIR/test/configs/hcc1143_lpwgs" \
  --auto_outdir "$REPO_DIR/test/runs/hcc1143_lpwgs"

# Check the generated workflow connections.
nextflow run "$REPO_DIR/main.nf" -stub-run --docker \
  -params-file "$REPO_DIR/test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "$REPO_DIR/test/work/hcc1143_lpwgs_stub"

# Run or resume the three-library analysis.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/test/configs/hcc1143_lpwgs/illumina.auto.yml" \
  -work-dir "$REPO_DIR/test/work/hcc1143_lpwgs" \
  -resume
```

The six-tumor/four-control page is not a runnable repository test. Its FASTQs are not included or downloaded; it is a user-data command template.

## Build the documentation

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Create and activate an isolated documentation environment.
python3 -m venv "$REPO_DIR/.venv-docs"
source "$REPO_DIR/.venv-docs/bin/activate"

# Install dependencies and build with strict checks.
python -m pip install --upgrade pip
python -m pip install -r "$REPO_DIR/docs/requirements.txt"
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

## Adding a public example

A runnable public example should include accessions, stable URLs, byte counts, checksums, `gzip -t`, exact sample metadata, resource expectations, generated YAML, validation commands, and output verification. Do not commit large public FASTQs.

A local-data template must state prominently that the data are not included and that the commands cannot run until the user supplies the files.

## Before requesting review

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Review changes and run the documentation checks.
git diff --stat
git diff --check
git status --short
python3 "$REPO_DIR/tests/test_docs_style.py"
mkdocs build --strict
```

Summarize the routes tested, whether runs were fresh or resumed, the selected runtime/image, and any test not performed. Deploy GitHub Pages only after source changes are reviewed and merged.
