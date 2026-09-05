# Output files

Every run writes a numbered directory tree under the YAML `outdir`. OncoTracer claims only an absent or empty directory, records its exact runtime ownership in `.oncotracer-native/output-owner.json`, and prevents concurrent writers with an exclusive run lock. Resume and `--force` require that owner record to match; an existing nonempty unowned tree is preserved and rejected. Start with the summary, then move from the upstream caller through refined segments, final tables, and plots.

Set one shell variable so the commands below are easy to reuse:

```bash
# Run this command from the oncotracer directory.
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
| `07_methylation/` | What optional ONT modified-base/classifier result and provenance were produced? | Independent optional research result; review its status before predictions |

Do not report temporary alignment/caller intermediates or `.oncotracer-native/` ledger files as scientific results. Preserve the ledger and trace for audit, but use the numbered stage-02/03 outputs for exact scientific values.

## Stage 01: alignment and initial caller

### Illumina

```bash
# Run this command from the oncotracer directory.
find "$OUT/01_samurai_illumina" -maxdepth 2 -type f | sort | sed -n '1,80p' # inventory
sed -n '1,8p' "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"          # initial qDNAseq segments
find "$OUT/01_samurai_illumina/alignment" -maxdepth 1 -name '*.bam' -print # aligned BAMs
```

Useful quality-control files include `fastqc/`, `multiqc/`, `picard/`, and `pipeline_info/`. Open the MultiQC HTML on a workstation before interpreting CNA calls.

#### Independent tumor and normal outputs

Native qDNAseq writes one set of files for every QC-valid samplesheet row,
including rows marked `normal`:

```text
01_samurai_illumina/qdnaseq/
├── all_segments.seg
├── all_calls.seg
├── qdnaseq_sample_status.json
├── qdnaseq_summary_mqc.txt
├── bins/
│   └── <sample>_markdup_bins.bed
├── segments/
│   ├── <sample>_.seg
│   └── <sample>.calls.seg
└── plots/
    └── <sample>_markdup_segment_plot.pdf
```

The samplesheet preserves whether each input was submitted as `tumor` or
`normal`. That status does not pool, average, or subtract samples: no local
sample-derived panel is created. Audit independent completion as follows:

```bash
SHEET="$PWD/project/config/illumina.samplesheet.csv"
QDNA="$OUT/01_samurai_illumina/qdnaseq"

cat "$SHEET"
cat "$QDNA/qdnaseq_sample_status.json"
find "$QDNA/bins" "$QDNA/segments" "$QDNA/plots" -maxdepth 1 -type f -print | sort
```

`qdnaseq_sample_status.json` is authoritative for caller completeness. A
failed or mathematically invalid sample is recorded there and excluded from
aggregate tables without being repurposed as a reference for the remaining
samples.

### ONT

```bash
# Run this command from the oncotracer directory.
find "$OUT/01_samurai_ont" -maxdepth 3 -type f | sort | sed -n '1,100p' # inventory
sed -n '1,80p' "$OUT/01_samurai_ont/logs/run_summary.txt"               # used/skipped barcode summary
if test -s "$OUT/01_samurai_ont/results/ichorcna/ichorcna_sample_status.json"; then
  cat "$OUT/01_samurai_ont/results/ichorcna/ichorcna_sample_status.json" # ichorCNA status
  sed -n '1,8p' "$OUT/01_samurai_ont/results/ichorcna/segments_logR_corrected_gistic.seg"
fi
if test -s "$OUT/01_samurai_ont/qdnaseq/all_segments.seg"; then
  cat "$OUT/01_samurai_ont/qdnaseq/qdnaseq_sample_status.json"        # qDNAseq status
  sed -n '1,8p' "$OUT/01_samurai_ont/qdnaseq/all_segments.seg"          # qDNAseq segments
fi
find "$OUT/01_samurai_ont/bam" -maxdepth 1 -name '*.bam' -print        # aligned BAMs
```

For ichorCNA, `ichorcna_sample_status.json` is authoritative for caller completeness. For qDNAseq, the equivalent record is `qdnaseq_sample_status.json`. A failed or mathematically invalid sample is recorded there and excluded from aggregate caller tables and downstream reports; later viable samples continue. `workflow_summary.json` reports `partial_failure` and lists both completed and failed samples. Nested partial caller files remain available for diagnosis but are not published as complete top-level inputs. For solid-biopsy qDNAseq, use a separate `outdir`; its initial caller output is `01_samurai_ont/qdnaseq/`.

Also inspect `logs/used_fastq.tsv`, `logs/skipped_fastq.tsv`, `logs/skipped_samples.tsv`, and `logs/warning_samples.tsv`. A completed workflow can still contain a skipped barcode warning that matters scientifically.

## Stage 07: optional ONT methylation

This directory exists when methylation was requested, using POD5 or existing modified-base BAMs:

```bash
# Run this command from the oncotracer directory.
cat "$OUT/07_methylation/methylation_status.json"
cat "$OUT/07_methylation/methylation_provenance.json"
find "$OUT/07_methylation" -maxdepth 3 -type f | sort | sed -n '1,120p'
```

Start with `methylation_status.json`. Each sample is `complete`, `no_cpg_modifications`, `no_classifier_probes` (MARLIN, current source), or `failed`. A zero-call sample is not sent to the classifier. For MARLIN, `covered_classifier_probes` counts the supplied probes with data; zero means no prediction was made. `methylation_provenance.json` records input inventories, tools, models, and device choice. See the [resource reference](configuration/methylation_reference.md) for exact provenance fields.

With `methylation_only: true`, `cna_status` is `not_requested`; absence of CNA outputs is expected.

Methylation and CNA are independent branches: stage 07 remains valid when CNA fails, and stages 01–06 may remain valid when methylation is incomplete. In either partial case, the final command exits nonzero and `workflow_summary.json` records `cna_status`, `methylation_status`, and the relevant sample lists. Do not present a missing classifier output as a negative classification.

## Stage 02: refined segmentation

The dataset subdirectory is normally `illumina_qdnaseq_100kb`, `ONT_ichorcna_500kb`, or an explicit ONT qDNAseq name such as `ONT_qdnaseq_100kb`, depending on the YAML. List it rather than guessing:

```bash
# Run this command from the oncotracer directory.
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
# Run this command from the oncotracer directory.
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
# Run this command from the oncotracer directory.
find "$OUT/04_cna_custom_plots" -maxdepth 2 -type f | sort
xdg-open "$OUT/04_cna_custom_plots/cna_per_sample_pages.pdf"                  # one page per sample
xdg-open "$OUT/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf"   # cohort profiles
```

On a headless server, copy PDFs to your workstation. Common outputs include genome overview, event burden/counts, recurrent cytobands, gene-panel frequency, per-sample pages, and log2-ratio profiles. PNG/SVG files are convenient for slides; the TSV tables remain the source for exact values.

## Stage 05: optional classifier and pathology

This directory exists only when `run_cna_classifier: true`.

```bash
# Run this command from the oncotracer directory.
sed -n '1,8p' "$OUT/05_cna_classifier/02_classification/cna_patient_classification.tsv"
sed -n '1,8p' "$OUT/05_cna_classifier/06_knowledge/sample_knowledge_summary.tsv"
sed -n '1,8p' "$OUT/05_cna_classifier/07_pathology/pathology_concordance.tsv"
```

Read [Models and pathology](models_pathology.md) before interpreting these files. They are research interpretations derived from stage 03, not replacements for the underlying event table or for diagnostic review.

## Confirm a run before sharing it

```bash
# Run this command from the oncotracer directory.
test -s "$OUT/06_workflow_summary/workflow_summary.txt"                # summary exists
test -s "$OUT/03_cna_codification/cna_events.tsv"                     # event table exists
test -s "$OUT/03_cna_codification/cna_cytogenomic_notation.tsv"       # notation table exists
test -s "$OUT/04_cna_custom_plots/cna_per_sample_pages.pdf"           # plots exist
find "$OUT" -type f -name '*.command.err' -size +0c -print             # review any non-empty task stderr files
```

Non-empty standard error is not automatically a failure--many tools write progress there--but it must be reviewed. Preserve the YAML, samplesheet, `oncotracer provenance --json`, input/reference checksums, container digest or five Conda explicit specifications, and workflow summary with any released result.
