# CNA classification and report components

OncoTracer runs these Python scripts directly as part of its native engine.
The directory name is retained for internal resource paths; it is not a
separate workflow or installation route.

Enable CNA interpretation with `run_cna_classifier: true` in the run YAML,
then use `oncotracer run --backend conda --config PATH_TO_YAML`.

See [classifier settings](../../docs/configuration/pathology.md),
[report LLM settings](../../docs/llm_reports.md) and
[result files](../../docs/outputs.md). Methylation classifiers are configured
separately in the [methylation guide](../../docs/configuration/methylation.md).
