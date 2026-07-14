# Output files

Every run writes a numbered directory tree under the YAML `outdir`. Start with the summary, then move from the upstream caller through refined segments, final tables, and plots.

Set one shell variable so the commands below are easy to reuse:

```bash
OUT="$PWD/project/runs/my_first_run" # replace this with the exact outdir from your YAML
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

If `cat` says the file does not exist, either the run has not finished or `OUT` does not match the YAML.

## Which result is authoritative?

| Stage | Main question | Status |
| --- | --- | --- |
| `01_samurai_illumina/` or `01_samurai_ont/` | What did alignment and the initial CNA caller produce? | Upstream caller/QC output; important provenance, but refinement follows |
| `02_bam_refinement/` | Where are the final refined segment boundaries and bins? | Authoritative refined segmentation |
| `03_cna_codification/` | Which CNA events and cytogenomic descriptions does OncoTracer report? | Authoritative machine-readable OncoTracer CNA results |
| `04_cna_custom_plots/` | How do those tables look visually? | Derived presentation; use tables for exact values |
| `05_cna_classifier/` | What optional CNA-pattern/pathology research interpretation was produced? | Optional and non-diagnostic |
| `06_workflow_summary/` | Where are the important folders? | Index/pointer file, not a scientific result |

Do not report files from `work/` as results. That directory is Nextflow's resumable task cache and may contain temporary or duplicated files.

## Stage 01: alignment and initial caller

### Illumina

```bash
find "$OUT/01_samurai_illumina" -maxdepth 2 -type f | sort | sed -n '1,80p' # inventory
sed -n '1,8p' "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"          # initial qDNAseq segments
find "$OUT/01_samurai_illumina/alignment" -maxdepth 1 -name '*.bam' -print # aligned BAMs
```

Useful quality-control files include `fastqc/`, `multiqc/`, `picard/`, and `pipeline_info/`. Open the MultiQC HTML on a workstation before interpreting CNA calls.

### ONT

```bash
find "$OUT/01_samurai_ont" -maxdepth 3 -type f | sort | sed -n '1,100p' # inventory
sed -n '1,80p' "$OUT/01_samurai_ont/logs/run_summary.txt"               # used/skipped barcode summary
sed -n '1,8p' "$OUT/01_samurai_ont/results/ichorcna/segments_logR_corrected_gistic.seg" # initial ichorCNA segments
find "$OUT/01_samurai_ont/bam" -maxdepth 1 -name '*.bam' -print        # aligned BAMs
```

Also inspect `logs/used_fastq.tsv`, `logs/skipped_fastq.tsv`, `logs/skipped_samples.tsv`, and `logs/warning_samples.tsv`. A completed workflow can still contain a skipped barcode warning that matters scientifically.

## Stage 02: refined segmentation

The dataset subdirectory is normally `illumina_qdnaseq_100kb` or `ONT_ichorcna_500kb`, depending on the YAML. List it rather than guessing:

```bash
find "$OUT/02_bam_refinement" -mindepth 1 -maxdepth 1 -type d -print
sed -n '1,8p' "$OUT"/02_bam_refinement/*/04_final_results/final_segments.tsv
sed -n '1,8p' "$OUT"/02_bam_refinement/*/01_tables/boundary_refinement_statistics.csv
```

Use `04_final_results/final_segments.tsv` as the primary refined segment table. `01_tables/` and `03_consolidated/` preserve detailed calculations and comparisons for audit. `02_samurai_compatible/` is an interoperability representation, not a second independent call set.

Key files:

| File | Use |
| --- | --- |
| `04_final_results/final_segments.tsv` | Refined segment coordinates and values |
| `04_final_results/final_segments.bed` | BED representation for genome tools |
| `04_final_results/refined_bins_boundary_bp_difference.csv` | Per-bin boundary difference audit |
| `01_tables/sample_refinement_summary.csv` | Per-sample refinement counts/status |
| `critical_outputs_manifest.csv` | Inventory of required outputs |

## Stage 03: final CNA tables

```bash
sed -n '1,12p' "$OUT/03_cna_codification/cna_events.tsv"
sed -n '1,12p' "$OUT/03_cna_codification/cna_cytogenomic_notation.tsv"
wc -l "$OUT/03_cna_codification/cna_events.tsv" "$OUT/03_cna_codification/cna_cytogenomic_notation.tsv"
```

- `cna_events.tsv` is the main event-level result for downstream analysis.
- `cna_cytogenomic_notation.tsv` provides the corresponding cytoband-oriented descriptions.
- `input_bed_files.tsv` and `input_cna_files.tsv` record the exact inputs used by codification.

A table containing only a header can be a valid CNA-flat result; confirm the sample in the notation/QC outputs rather than assuming the workflow failed.

## Stage 04: plots

```bash
find "$OUT/04_cna_custom_plots" -maxdepth 2 -type f | sort
xdg-open "$OUT/04_cna_custom_plots/cna_per_sample_pages.pdf"                  # one page per sample
xdg-open "$OUT/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf"   # cohort profiles
```

On a headless server, copy PDFs to your workstation. Common outputs include genome overview, event burden/counts, recurrent cytobands, gene-panel frequency, per-sample pages, and log2-ratio profiles. PNG/SVG files are convenient for slides; the TSV tables remain the source for exact values.

## Stage 05: optional classifier and pathology

This directory exists only when `run_cna_classifier: true`.

```bash
sed -n '1,8p' "$OUT/05_cna_classifier/02_classification/cna_patient_classification.tsv"
sed -n '1,8p' "$OUT/05_cna_classifier/06_knowledge/sample_knowledge_summary.tsv"
sed -n '1,8p' "$OUT/05_cna_classifier/07_pathology/pathology_concordance.tsv"
```

Read [Models and pathology](models_pathology.md) before interpreting these files. They are research interpretations derived from stage 03, not replacements for the underlying event table or for diagnostic review.

## Confirm a run before sharing it

```bash
test -s "$OUT/06_workflow_summary/workflow_summary.txt"                # summary exists
test -s "$OUT/03_cna_codification/cna_events.tsv"                     # event table exists
test -s "$OUT/03_cna_codification/cna_cytogenomic_notation.tsv"       # notation table exists
test -s "$OUT/04_cna_custom_plots/cna_per_sample_pages.pdf"           # plots exist
find "$OUT" -type f -name '*.command.err' -size +0c -print             # review any non-empty task stderr files
```

Non-empty standard error is not automatically a failure--many tools write progress there--but it must be reviewed. Preserve the YAML, samplesheet, commit hash (`git rev-parse HEAD`), container digest, and workflow summary with any released result.
