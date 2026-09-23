# OncoTracer

Analyze Illumina and Oxford Nanopore (ONT) reads for copy-number changes,
optional small variants, and ONT methylation research classifications.
Choose your samples and settings in the browser, then follow the run to its results.

## Start here

| Step | What to do |
| --- | --- |
| **1. Install once** | [Choose Conda or Docker](installation.md). Both use the same browser interface. |
| **2. Configure** | [Open setup](setup.md), select your FASTQs, and assign samples. |
| **3. Run** | Click **Save configuration and check**, then **Run analysis**. [Read the results](outputs.md). |

**[Try the interactive demo](assets/setup-demo/index.html)** before installing.
It uses synthetic samples and simulated progress.

[![Explore the browser setup with synthetic samples](assets/setup-demo-preview.png)](assets/setup-demo/index.html)

## Already installed?

Activate your OncoTracer launcher environment, then:

```bash
oncotracer setup --project "$PWD/my-study"
```

Keep the terminal open. The browser lets you select Conda or Docker, choose the
reference, and start the run. The first analysis also prepares reference files
and takes longer. See [hardware requirements](installation.md#requirements).

**Terminal-only alternative** for a new project:

```bash
oncotracer setup --terminal --project "$PWD/my-study" --backend conda --run
```

Answer the terminal questions; configuration is checked before execution.
For scripts and SSH forwarding, see [CLI and headless servers](headless.md).

If you saved the configuration without running, use:

```bash
oncotracer check --config "$PWD/my-study/config/run.yml"
oncotracer setup --project "$PWD/my-study" --run
```

This starts the saved project with its selected backend and settings.
See [run, stop and resume](running.md) for more options.

## Choose a tutorial

- **[QuickStart 1](quick_start.md):** start here for small public Illumina and ONT examples.
- **[QuickStart 2](public_cohort.md):** try a three-library Illumina study.
- **[Full public cohort](full_tutorial.md):** reproduce a larger archive after your first run.

## Add an analysis when needed

[Small variants](variants.md) · [ANNOVAR annotation](annovar.md) · [ONT methylation](configuration/methylation.md) ·
[LLM-assisted reports](llm_reports.md) · [Manuscript panels](paper_report.md)

[How OncoTracer works](native_architecture.md) explains the workflow.
[Uninstall](uninstall.md) covers tool removal and recovery.
[Repository map](repository_guide.md) explains the folders; advanced settings live
under **Reference**, and [release validation](parity_release.md) under **Help and Development**.
