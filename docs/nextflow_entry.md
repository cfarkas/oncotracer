# Nextflow entry and visual setup

OncoTracer's repository root is a Nextflow entry. It opens the existing visual browser interface or launches an existing configuration. Selecting **complete short-variant analysis** for Illumina runs the complete bundled workflow with GLIMPSE2 enabled. ONT retains its compatible callers and does not use an unvalidated short-read imputation path.

## Local visual setup

```bash
cd /path/to/oncotracer
nextflow run main.nf --interactive --backend conda
```

From an input folder or any other directory, supply the installed repository’s
absolute path:

```bash
nextflow run /absolute/path/to/oncotracer-src/main.nf --interactive --backend conda
```

Paths and browsing start from the directory where you launched the command.
After backend installation, the native alternative needs neither the repository
path nor environment activation:

```bash
oncotracer --interactive --backend conda
```

Use `--backend docker` for Docker. Select the sequencing platform, then **Fresh**
or **FFPE** in the browser; no `--ffpe` flag is required.

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

## Concurrent projects

Open a separate terminal for each project:

```bash
# Terminal 1
oncotracer --interactive --backend conda --project /work/study-A

# Terminal 2
oncotracer --interactive --backend conda --project /work/study-B
```

Additional terminals work the same way; Docker is also supported. Without
`--port`, each session chooses an available loopback port, preferring 8888.
Use each terminal's complete printed URL, including its own session code.
An explicitly requested `--port` must be free; it is never silently changed.

The equivalent Nextflow command is:

```bash
nextflow run /absolute/path/to/oncotracer-src/main.nf --interactive --backend conda --project /work/study-B
```

Choose a different project/output directory for each simultaneous run. One
session can run one project at a time; separate sessions can run together.
Concurrent writes to the same output directory and saving over an active
configuration are rejected. Shared variant resources are prepared one run at a
time; independent computation can overlap. Assign each run
its share of CPU threads and memory; resource settings apply per run. Managed
tools can be shared by active runs, while installation or removal waits for
those tools to be free. Stop controls apply to their own session.

## Existing configurations

```bash
# Reuse aligned BAMs and run the complete variants workflow.
nextflow run main.nf --analysis variants --config /path/to/variants.yml

# Run the full configured OncoTracer analysis, including variants when enabled.
nextflow run main.nf --analysis full --config /path/to/run.yml

# Validate and print a plan without starting analysis.
nextflow run main.nf --analysis variants --config /path/to/variants.yml --dry_run
```

`--analysis variants` is the default. Paths are resolved relative to the directory where the command is launched. `--threads N` overrides the total configured CPU budget. `--parallel_samples N` selects the maximum number of Illumina sample workflows running at once (default: `variant_parallel_samples` from YAML, otherwise 1). CPU threads and `variant_memory` are total budgets divided among concurrent samples; they are not multiplied by the sample count. The complete workflow needs at least 32GB per simultaneous sample for the mandatory local report model. The active sample count is capped by the requested count, available sample count, CPU budget and this memory minimum; for example, requesting four samples with 32GB total runs one at a time. `--python /path/to/python3` selects the Python interpreter used by the controller; the scientific task interpreter is checked separately and can be selected automatically from the installed managed core environment. The full-analysis route also accepts `--backend` with the backends supported by OncoTracer. Existing-BAM analysis uses the local Nextflow runtime; omit `--backend` or use `host`.

The controller remains in the foreground and propagates analysis failures as a failed Nextflow run. It launches up to the configured number of sample workflows concurrently; each bundled workflow schedules its individual tasks within its assigned share of CPU and memory. This gateway does not provide an additional cluster scheduler. The visual setup includes “Samples running at once” and the total memory budget under resources, including when invoked with `--interactive`.

Completed analysis outputs are verified and reused by the bundled workflow's continuation mechanism. The gateway is deliberately not a cached Nextflow process: restarting it must reconnect to the analysis state, not skip the entire run because a previous controller invocation completed.

For example, process up to four samples at once with 32 total CPU threads:

```bash
nextflow run main.nf --analysis variants --config /path/to/variants.yml \
  --threads 32 --parallel_samples 4
```

Set `variant_memory: 128GB` in the YAML for a 128GB total analysis memory budget. In this example each sample receives at most 8 threads and 32GB. The native CLI equivalent uses `--parallel-samples 4`, for both `oncotracer variants` and `oncotracer run`. The concurrency setting applies to the Illumina short-variant stage; values above 1 are rejected for ONT or a full analysis without this stage.

## Remote repository entry

The migration branch is `feat/glimpse2-complete-short-variants`:

```bash
nextflow run cfarkas/oncotracer -r feat/glimpse2-complete-short-variants --interactive
nextflow run cfarkas/oncotracer -r feat/glimpse2-complete-short-variants --analysis variants --config /absolute/path/to/variants.yml
```

The launcher loads the selected revision even when the current directory contains an older OncoTracer checkout. There is no need to reinstall OncoTracer or change Python for this import-order fix; `--python` remains an optional interpreter override. Use `-latest` after a branch update to fetch the fix.

For reproducibility, replace the branch with its commit SHA. Remote invocation fetches the code and executes locally with the same dependency and resource requirements. It does not upload sequencing data to a hosted analysis service. The published main branch changes only after the migration is merged.

## Startup validation

Every real host/Conda/Poetry full run validates environment integrity and tool availability before reference preparation. GLIMPSE2 runs also test scientific Python imports and offline local report resources before sample computation. See [environment readiness](MULTISAMPLE_QUICKSTART.md#environment-readiness-before-starting). Dry-runs validate configuration without these runtime probes.

## Report options

Illumina's primary variant controls are preservation, optional patient metadata and trait/GWAS confidence. Resource folders, previous compatible analysis outputs and an alternative workflow source are grouped under advanced settings. There is no GLIMPSE2-off control.

The confidence values are `strict`, `medium` (default), and `exploratory`. They apply to trait/GWAS reporting; they do not relax clinical-variant or polygenic-scoring requirements. Patient metadata can include sample identifiers, names, age or birth year and sex; missing age/sex does not exclude a sample.

FFPE requires FFPERASE assessment and its existing resource/license checks. Imputation supplies additional germline evidence; absence from a reference panel does not establish somatic origin, and imputation support alone does not validate an artifact or a somatic mutation.
