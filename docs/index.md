# OncoTracer

OncoTracer turns Illumina or Oxford Nanopore (ONT) sequencing data into DNA copy-number tables and plots. With ONT methylation data, it can also run a leukemia or CNS-tumor research classifier.

## Choose your first task

| Your starting point | Start with |
| --- | --- |
| You want to test the software | [Install](installation.md), then [QuickStart 1](quick_start.md) |
| You have Illumina or ONT FASTQs | [Set up your project](setup.md) |
| You have ONT methylation data | [Methylation guide](configuration/methylation.md) |
| You want small variants from reads or existing BAMs | [Variant guide](variants.md) |
| You want LLM-assisted report text | [Report LLM settings and audit](llm_reports.md) |
| You want manuscript panels from saved evidence | [Paper report](paper_report.md) |
| You have a result or an error | [Read the outputs](outputs.md) or [troubleshoot](troubleshooting.md) |

## The usual workflow

```bash
oncotracer setup --project "$PWD/my-study"
```

This opens the browser at **127.0.0.1:8888**. Choose ONT or Illumina, browse to
FASTQs, and drag detected samples into Normal or Cancer. Edit names and settings,
choose a project folder, then **Save configuration and check** and **Run analysis**.
Keep the terminal open; use the complete printed URL if the browser does not open.
`setup --terminal` provides terminal prompts. See the [setup walkthrough](setup.md).

If you saved without running, use:

```bash
oncotracer check --config "$PWD/my-study/config/run.yml"
oncotracer run --config "$PWD/my-study/config/run.yml" --backend conda
```

New to terminal commands? See [copying commands and creating sample tables](command_basics.md).
For many FASTQs or ONT barcodes, use the [batch examples](auto_params.md).

## What to expect

The first run takes longer because it prepares the human reference genome and analysis tools. Use `oncotracer system --path /path/to/project` for hardware guidance; see [requirements](installation.md#requirements) and [prebuilt indexes](reference_indexes.md). Your input files stay in their original folders; results go to the `outdir` saved in your configuration. [Uninstall](uninstall.md) removes selected tools without deleting projects.

Open `index.html` for the results dashboard and `06_workflow_summary/final_report.html`
for saved CNA, literature and available methylation findings. `workflow_summary.txt`
in the same summary directory records output locations and completion. A completed computation does not by itself establish a reliable tumor classification. The [methylation guide](configuration/methylation.md#read-the-result) explains insufficient-data results.

For a short workflow overview, read [How OncoTracer works](native_architecture.md).
For larger examples, use [QuickStart 2](public_cohort.md) or the [full tutorial](full_tutorial.md).
Developer references cover [implementation details](architecture_details.md) and
[release validation](parity_release.md).
