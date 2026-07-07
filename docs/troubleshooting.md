# Troubleshooting

## Nextflow Cannot Resume

Use `-resume` with the same working directory and similar parameters:

```bash
nextflow run main.nf -profile local -params-file params/illumina.example.yml -resume
```

If you change output folder names or important parameters, Nextflow may rerun processes.

## A Required Input Is Missing

Check the YAML paths first:

```bash
sed -n '1,120p' params/illumina.example.yml
```

Common issues:

- wrong BAM directory;
- wrong prior segment file;
- ONT barcode names not matching folder names;
- `ont_sample_names` count not matching `ont_barcodes` count;
- output folder exists but was produced by an older unnumbered workflow version.

## Pathology Rows Do Not Match Samples

Check:

- `pathology_sample_col` points to the correct column;
- sample IDs in `cna_events.tsv` match values in the pathology table;
- no hidden spaces or inconsistent capitalization;
- the pathology file is readable as CSV/TSV/XLS/XLSX.

Review:

```text
05_cna_classifier/07_pathology/pathology_status.txt
05_cna_classifier/07_pathology/pathology_records_matched.tsv
```

## Hugging Face Models Fail

This is non-fatal by design. Review:

```text
05_cna_classifier/06_knowledge/knowledge_llm_trials.tsv
05_cna_classifier/06_knowledge/knowledge_literature_ranker_trials.tsv
05_cna_classifier/07_pathology/pathology_model_trials.tsv
```

For deterministic/offline behavior, disable optional model layers.

## GISTIC2 Fails Or Is Skipped

The classifier default is tolerant: GISTIC2 failures do not necessarily stop the run unless strict mode is enabled. Check classifier logs and `04_gistic/` outputs.

## Old Unnumbered Folders Exist

Older test runs may contain folders such as:

```text
bam_refinement/
cna_codification/
cna_classifier/
workflow_summary/
```

Current OncoTracer runs write numbered folders:

```text
02_bam_refinement/
03_cna_codification/
04_cna_custom_plots/
05_cna_classifier/
06_workflow_summary/
```

Use the numbered folders for new analyses.
