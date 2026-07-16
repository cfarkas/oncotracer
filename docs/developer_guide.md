# Developer guide

This guide is for contributors changing code, tests, examples, or documentation. Users running their own FASTQs should start with [QuickStart Example 1](quick_start.md) or [Run your own FASTQs automatically](auto_params.md).

## Repository map

```text
main.nf                         # top-level DSL2 workflow
nextflow.config                 # defaults and runtime profiles
params/                         # user-facing YAML templates
bin/scripts/                    # launch, download, auto-config, and refinement helpers
bin/cna_codification/           # event conversion and plotting code
bin/cna_classifier_nf/          # optional nested classifier/report workflow
docs/                           # MkDocs source
examples/                       # reproducible opt-in examples and manifests
test/                           # downloaded fixtures, generated configs, and test outputs
run_test.sh                     # public Illumina + ONT end-to-end check
```

Do not hand-edit generated FASTQs, test outputs, `work/`, `.nextflow/`, or `site/` and then treat those changes as source changes.

## Start from a fresh branch

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
git switch -c your-change-name
git status --short
```

Keep unrelated changes out of the branch. Never commit patient data, credentials, container tokens, downloaded reference genomes, BAMs, or public FASTQs.

## Prepare the public test data first

The stub commands below require generated absolute-path YAMLs. Create them before testing:

```bash
nextflow run main.nf --make_test                    # download/validate public FASTQs and write test/configs/*.yml
sed -n '1,120p' test/configs/illumina.quickstart.yml
sed -n '1,120p' test/configs/ont.quickstart.yml
```

The downloader reuses a file only when its validation checks pass. Keep the preparation step separate from code tests so a download error is not mistaken for a workflow error.

## Fast checks for every change

```bash
find bin examples -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n # shell syntax
nextflow run main.nf -stub-run --docker -params-file test/configs/illumina.quickstart.yml
nextflow run main.nf -stub-run --docker -params-file test/configs/ont.quickstart.yml
git diff --check                                          # whitespace/conflict-marker check
git status --short                                        # review the exact change set
```

A stub run validates channels, parameters, and process wiring. It does not execute the scientific tools or prove that output files are valid.

## Full end-to-end verification

For a change that can affect execution, run:

```bash
bash run_test.sh --docker
```

This prepares/revalidates the two public datasets, checks both stub workflows, runs Illumina and ONT with `-resume`, and tests core outputs. A successful cached rerun is useful, but at least one uncached run is required when changing task commands, containers, callers, parsing, or output contracts.

Verify results explicitly:

```bash
test -s test/runs/illumina/03_cna_codification/cna_events.tsv
test -s test/runs/illumina/04_cna_custom_plots/cna_per_sample_pages.pdf
test -s test/runs/ont/03_cna_codification/cna_events.tsv
test -s test/runs/ont/04_cna_custom_plots/cna_per_sample_pages.pdf
cat test/runs/illumina/06_workflow_summary/workflow_summary.txt
cat test/runs/ont/06_workflow_summary/workflow_summary.txt
```

Review plots and tables, not just file existence, after scientific or visualization changes.

## Test the six-FASTQ example when it is affected

The HCC1143 cohort is deliberately opt-in because it is larger:

```bash
bash examples/hcc1143_lpwgs/run_example.sh --docker --download-only # provenance/checksum/gzip validation
bash examples/hcc1143_lpwgs/run_example.sh --docker --prepare-only  # also test auto-generated YAML/samplesheet
bash examples/hcc1143_lpwgs/run_example.sh --docker                 # complete cohort run and output checks
```

Record the commit, image digest, start/end time, reference, caller/bin size, and inspected outputs before adding its plots to the gallery. Do not replace a pending gallery notice with an unverified screenshot.

## Build documentation locally

Create an isolated documentation environment:

```bash
python3 -m venv .venv-docs
source .venv-docs/bin/activate
python -m pip install --upgrade pip
python -m pip install -r docs/requirements.txt
mkdocs build --strict
mkdocs serve
```

Open `http://127.0.0.1:8000/` while `mkdocs serve` is running. The strict build must pass: broken internal links, missing navigation pages, and Markdown mistakes should be fixed before review.

Documentation examples are part of the interface. For every command box, check:

- it begins from the repository directory or states its required working directory;
- every placeholder is identified before the user copies it;
- paths remain below `lpwgs_root`;
- the YAML shown matches current parameter names;
- validation and real-run commands are visibly different;
- expected outputs and failure checks are stated;
- a public accession/result claim has provenance.

## Change discipline

Changes to these contracts require deliberate review and migration notes:

- sample/barcode matching rules;
- genome build and coordinate conventions;
- qDNAseq/ichorCNA caller behavior or default bin sizes;
- boundary-refinement acceptance rules;
- event/notation schemas;
- numbered output directories and filenames;
- classifier contexts, thresholds, or evidence wording;
- research-use limitations.

Do not hide a scientific behavior change inside a documentation, formatting, or dependency update. Add a focused test and describe the expected before/after result.

## Adding a public example

A reproducible public example should include:

1. archive project/run accession and study citation;
2. one immutable URL per file;
3. compressed byte count and archive checksum;
4. an automated `gzip -t` check;
5. explicit inclusion/exclusion rules;
6. sample/status metadata;
7. resource expectations and opt-in behavior for large data;
8. generated YAML plus stub and real-run checks;
9. an output manifest and gallery provenance record.

Do not commit large public reads to Git. Download them into ignored test/example storage.

## Before requesting review

```bash
git diff --stat
git diff --check
git status --short
mkdocs build --strict
```

Summarize which routes were tested, whether tests were cached or fresh, runtime/container identity, and any test not performed. Only maintainers should deploy GitHub Pages (`mkdocs gh-deploy`) after the source change has been reviewed and merged.
