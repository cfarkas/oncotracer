# Configure a run

A configuration is a YAML text file containing your input paths, sample names, and analysis settings. One configuration describes one analysis. It does not contain the reads themselves.

## Start with setup

After [installation](installation.md), run:

Choose either browser setup:

```bash
oncotracer setup --project /absolute/path/to/my-study
```

Or terminal questions for the same project:

```bash
oncotracer setup --terminal --project /absolute/path/to/my-study --backend conda
```

Replace the project path with yours. In the browser, select inputs, choose Conda and
**Save configuration and check**. In the terminal, answer the questions and
finish with **save**. Both save the commented `config/run.yml`.

Then check and run that configuration from the terminal:

```bash
oncotracer check --config /absolute/path/to/my-study/config/run.yml
oncotracer run --backend conda --config /absolute/path/to/my-study/config/run.yml
```

Skip this run command if you already started the analysis from setup. For another
backend, change `--backend` explicitly. [The setup guide](setup.md) covers sample
selection; [terminal and headless servers](headless.md) covers unattended commands
and remote browser access.

## Which settings should I change?

| Setting | Meaning |
| --- | --- |
| `mode` | Sequencing platform: `illumina` or `ont` |
| `outdir` | Where analysis results go |
| `lpwgs_root` | Where reusable reference files go |
| `threads` | CPU worker threads to request |
| `variant_specimen_type` | `fresh` or `ffpe` for optional variant analysis |
| `variant_callers` | Platform-compatible variant callers; see the variant guide |
| `methylation` | Add ONT methylation analysis |
| `methylation_only` | Skip copy-number analysis when `true` |
| `methylation_classifier` | `marlin` for leukemia or `sturgeon` for CNS research |
| `methylation_gpu` | Allow GPU work when `true`; default is CPU |
| `run_cna_classifier` | Add interpretation of copy-number changes; separate from methylation |

Keep the generated caller and bin-size settings for your first run. Leave `force: false` to reuse completed matching stages when you resume. Use a new `outdir` for a different analysis so you can compare its results with the original.

## Other configuration routes

- For many FASTQ files and a sample table, use [batch setup](auto_params.md).
- For variant callers, Fresh/FFPE processing and matched normals, use the [variant guide](variants.md).
- For methylation tools, model files, and CPU options, use the [methylation guide](configuration/methylation.md).
- To edit YAML yourself, see [YAML basics](configuration/yaml_basics.md), [Illumina settings](configuration/illumina.md), or [ONT settings](configuration/ont.md).
- For an individual advanced field, use the [parameter reference](configuration/parameter_reference.md).

For CNA analysis, tumor and normal samples are analyzed independently; normal rows are not pooled into a reference. Somatic Strelka2 variant calling requires an explicit [tumor-to-normal pairing](variants_reference.md#strelka2-germline-and-somatic-calling).
