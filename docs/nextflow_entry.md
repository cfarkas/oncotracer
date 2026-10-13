# Nextflow entry and visual setup

OncoTracer's repository root is a Nextflow entry. It opens the existing visual browser interface or launches an existing configuration. Selecting **complete short-variant analysis** for Illumina runs the complete bundled workflow with GLIMPSE2 enabled. ONT retains its compatible callers and does not use an unvalidated short-read imputation path.

## Local visual setup

```bash
cd /path/to/oncotracer
nextflow run main.nf --interactive
```

This opens the local OncoTracer browser with Illumina and short variants selected. Select preservation, samples and the remaining report options, then save, review and start the analysis. `--interactive` does not invoke the terminal question wizard. Keep the launching terminal open while using the interface. If no browser is available, use the full local URL printed in that terminal.

Optional settings:

```bash
nextflow run main.nf --interactive --port 8889 --start_dir /path/to/data
```

For the existing-BAM review screen:

```bash
nextflow run main.nf --interactive --config /path/to/variants.yml
```

The configuration must supply the existing BAM manifest and reference. The browser protects the original inputs and prepares a new run folder.

## Existing configurations

```bash
# Reuse aligned BAMs and run the complete variants workflow.
nextflow run main.nf --analysis variants --config /path/to/variants.yml

# Run the full configured OncoTracer analysis, including variants when enabled.
nextflow run main.nf --analysis full --config /path/to/run.yml

# Validate and print a plan without starting analysis.
nextflow run main.nf --analysis variants --config /path/to/variants.yml --dry_run
```

`--analysis variants` is the default. Paths are resolved relative to the directory where the command is launched. `--threads N` overrides the configured CPU budget. `--python /path/to/python3` selects the Python interpreter used by the controller; it must contain OncoTracer's Python dependencies. The full-analysis route also accepts `--backend` with the backends supported by OncoTracer. Existing-BAM analysis uses the local Nextflow runtime; omit `--backend` or use `host`.

The controller remains in the foreground and propagates analysis failures as a failed Nextflow run. It launches sample workflows sequentially; the bundled workflow schedules the individual analysis tasks. This gateway does not provide an additional cluster scheduler or multiply per-sample resource budgets.

Completed analysis outputs are verified and reused by the bundled workflow's continuation mechanism. The gateway is deliberately not a cached Nextflow process: restarting it must reconnect to the analysis state, not skip the entire run because a previous controller invocation completed.

## Remote repository entry

The migration branch is `feat/glimpse2-complete-short-variants`:

```bash
nextflow run cfarkas/oncotracer -r feat/glimpse2-complete-short-variants --interactive
nextflow run cfarkas/oncotracer -r feat/glimpse2-complete-short-variants --analysis variants --config /absolute/path/to/variants.yml
```

For reproducibility, replace the branch with its commit SHA. Remote invocation fetches the code and executes locally with the same dependency and resource requirements. It does not upload sequencing data to a hosted analysis service. The published main branch changes only after the migration is merged.

## Report options

Illumina's primary variant controls are preservation, optional patient metadata and trait/GWAS confidence. Resource folders, previous compatible analysis outputs and an alternative workflow source are grouped under advanced settings. There is no GLIMPSE2-off control.

The confidence values are `strict`, `medium` (default), and `exploratory`. They apply to trait/GWAS reporting; they do not relax clinical-variant or polygenic-scoring requirements. Patient metadata can include sample identifiers, names, age or birth year and sex; missing age/sex does not exclude a sample.

FFPE requires FFPERASE assessment and its existing resource/license checks. Imputation supplies additional germline evidence; absence from a reference panel does not establish somatic origin, and imputation support alone does not validate an artifact or a somatic mutation.
