# Configure a run

A configuration is a YAML text file containing your input paths, sample names, and analysis settings. One configuration describes one analysis. It does not contain the reads themselves.

## Start with setup

With the [current-source installation](installation.md#current-source-for-the-new-setup-workflow), run:

```bash
oncotracer setup --project /absolute/path/to/my-study
oncotracer check --config /absolute/path/to/my-study/config/run.yml
oncotracer run --backend conda --config /absolute/path/to/my-study/config/run.yml
```

Replace the project path with yours. Setup asks for inputs, saves a commented YAML, and prints the exact next commands. [The setup guide](setup.md) explains each flag and shows how to supply answers directly.

## Which settings should I change?

| Setting | Meaning |
| --- | --- |
| `mode` | Sequencing platform: `illumina` or `ont` |
| `outdir` | Where analysis results go |
| `lpwgs_root` | Where reusable reference files go |
| `threads` | CPU worker threads to request |
| `methylation` | Add ONT methylation analysis |
| `methylation_only` | Skip copy-number analysis when `true` |
| `methylation_classifier` | `marlin` for leukemia or `sturgeon` for CNS research |
| `methylation_gpu` | Allow GPU work when `true`; default is CPU |
| `run_cna_classifier` | Add interpretation of copy-number changes; separate from methylation |

Keep the generated caller and bin-size settings for your first run. Leave `force: false` to reuse completed matching stages when you resume. Use a new `outdir` for a different analysis so you can compare its results with the original.

## Other configuration routes

- For many FASTQ files and a sample table, use [batch setup](auto_params.md). This also works with the v2.0.0 executable.
- For methylation tools, model files, and CPU options, use the [methylation guide](configuration/methylation.md).
- To edit YAML yourself, see [YAML basics](configuration/yaml_basics.md), [Illumina settings](configuration/illumina.md), or [ONT settings](configuration/ont.md).
- For an individual advanced field, use the [parameter reference](configuration/parameter_reference.md).

Tumor and normal samples are analyzed independently. Normal rows are not pooled into a reference for the tumor rows.
