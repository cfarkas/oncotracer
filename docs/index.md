# OncoTracer

OncoTracer turns Illumina or Oxford Nanopore (ONT) sequencing data into DNA copy-number tables and plots. With ONT methylation data, it can also run a leukemia or CNS-tumor research classifier.

## Choose your first task

| Your starting point | Start with |
| --- | --- |
| You want to test the software | [Install](installation.md), then [QuickStart 1](quick_start.md) |
| You have Illumina or ONT FASTQs | [Set up your project](setup.md) |
| You have ONT methylation data | [Methylation guide](configuration/methylation.md) |
| You want LLM-assisted report text | [Report LLM settings and audit](llm_reports.md) |
| You have a result or an error | [Read the outputs](outputs.md) or [troubleshoot](troubleshooting.md) |

## The usual workflow

```bash
oncotracer setup --project /absolute/path/to/my-study
oncotracer check --config /absolute/path/to/my-study/config/run.yml
oncotracer run --config /absolute/path/to/my-study/config/run.yml --backend conda
```

Replace the project path with your own. `setup` asks for inputs and saves settings, `check` checks those settings, and `run` analyzes the reads. Each command has help: for example, `oncotracer setup --help`.

## What to expect

The first run takes longer because it prepares the human reference genome and analysis tools. Use `oncotracer system --path /path/to/project` for hardware guidance; see [requirements](installation.md#requirements) and [prebuilt indexes](reference_indexes.md). Your input files stay in their original folders; results go to the `outdir` saved in your configuration. [Uninstall](uninstall.md) removes selected tools without deleting projects.

Start reading results at `06_workflow_summary/workflow_summary.txt`. A completed computation does not by itself establish a reliable tumor classification. The [methylation guide](configuration/methylation.md#read-the-result) explains insufficient-data results.

For larger examples, use [QuickStart 2](public_cohort.md) or the [full tutorial](full_tutorial.md). Technical details about recorded commands, checksums, and release tests are in [architecture](native_architecture.md) and [release validation](parity_release.md).
