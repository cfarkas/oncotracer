<a id="native-architecture"></a>

# How OncoTracer works

OncoTracer connects established analysis tools to find DNA copy-number changes (CNAs):
regions with extra or missing copies of DNA. You use the `oncotracer` command;
you do not need to run each tool yourself.

<a id="stage-graph"></a>

## What happens to your data?

You provide sequencing files and a configuration file. OncoTracer then:

1. Checks the input files and prepares the human reference genome.
2. Aligns the reads to that reference.
3. Estimates copy-number changes and refines their boundaries using the reads.
4. Saves result tables, plots, logs, and a run summary.

The default Illumina workflow uses BWA, Picard, and qDNAseq. The default Oxford
Nanopore (ONT) workflow uses minimap2, HMMcopy, and ichorCNA. See the
[tool list](programs.md) if you need the details.

## What is optional?

- The [optional CNA classifier](configuration/pathology.md) adds research
  predictions and expanded reports.
  [LLM-assisted report text](llm_reports.md) has separate settings.
- [ONT methylation](configuration/methylation.md) is a separate analysis. It needs
  modified-base BAMs or raw POD5 signal, plus additional tools and models.
  FASTQ files alone are not enough.

A completed run or a classifier prediction does not establish a diagnosis.

<a id="native-invariant"></a>

## What does “native” mean?

The Python application launches the analysis tools directly. Nextflow is not
required. The tools still need to be installed through a
[supported installation method](installation.md).

## Can I restart a run?

Yes. Repeat the same `oncotracer run` command. Completed steps are reused only
when their recorded inputs, settings, and outputs still pass checks. Incomplete
steps run again. See [running and resuming](running.md#resume-behavior).

## How do I check what ran?

Start with the [run summary and output files](outputs.md). OncoTracer records
the commands, tool versions, and completion status alongside the results.
To show the installed OncoTracer version and source identity:

```bash
oncotracer provenance --json
```

<a id="single-file-executable"></a>
<a id="installer-ownership-boundary"></a>

## For developers

[Implementation details](architecture_details.md) cover software packaging,
cache verification, and installation safety. [Release validation](parity_release.md)
describes the automated comparisons. Neither page is needed to start an analysis.
