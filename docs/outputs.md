# Output Files

Every run writes numbered directories below the YAML `outdir`. Start with the workflow summary, then inspect the caller output, refined segments, final CNA tables, plots, and optional classifier reports.

```bash
# Set OUT to the exact outdir from the run YAML.
OUT=/path/to/project/results

# Read the workflow summary first.
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

## Result directories

| Directory | Main contents |
| --- | --- |
| `01_samurai_illumina/` or `01_samurai_ont/` | Alignment, initial caller output, QC, and provenance |
| `02_bam_refinement/` | Refined segment boundaries and supporting tables |
| `03_cna_codification/` | Final CNA event and cytogenomic tables |
| `04_cna_custom_plots/` | Per-sample and cohort plots |
| `05_cna_classifier/` | Optional research classifier and pathology comparison |
| `06_workflow_summary/` | Important result locations |

Do not report files from `work/`; that directory is the resumable Nextflow task cache.

## Stage 01: Illumina

```bash
# List Illumina stage-01 files.
find "$OUT/01_samurai_illumina" -maxdepth 2 -type f -print | sort | sed -n '1,80p'

# Inspect the initial qDNAseq segment table.
sed -n '1,12p' "$OUT/01_samurai_illumina/qdnaseq/all_segments.seg"

# List aligned BAM files.
find "$OUT/01_samurai_illumina/alignment" -maxdepth 1 -name '*.bam' -print
```

Review the FastQC, MultiQC, Picard, alignment, and qDNAseq outputs before interpreting CNAs.

### Illumina local panel of normals

When `illumina_build_pon: true`, two or more selected `NORMAL` samples form a run-local qDNAseq reference. Corrected bin, segment, and plot outputs contain tumor samples only. Controls remain in the manifest and QC files.

Main files are below:

```text
01_samurai_illumina/qdnaseq_local_pon/
├── all_segments.seg
├── all_calls.seg
├── all_tumors.qdnaseq_pon_corrected_bins.tsv
├── qdnaseq_local_pon_summary.tsv
├── qdnaseq_local_pon_versions.tsv
├── qdnaseq_local_pon.done
├── bins/
├── segments/
├── plots/
├── pon/
│   ├── normal_panel_manifest.tsv
│   └── <PON>.reference_bins.tsv
└── qc/
    ├── sample_qc.tsv
    └── normal_panel_sample_qc.tsv
```

| File | Purpose |
| --- | --- |
| `pon/normal_panel_manifest.tsv` | Exact normal samples used |
| `pon/<PON>.reference_bins.tsv` | Per-bin normal reference |
| `qc/normal_panel_sample_qc.tsv` | Leave-one-out control QC |
| `qc/sample_qc.tsv` | Per-sample processing QC |
| `all_tumors.qdnaseq_pon_corrected_bins.tsv` | Corrected tumor bin values |
| `all_segments.seg` | Combined corrected tumor segments |
| `all_calls.seg` | Corrected tumor segments with discrete calls |
| `qdnaseq_local_pon.done` | Final completion marker; successful content is `QDNASEQ_LOCAL_PON_SUCCESS` |

```bash
# Set the local-PoN directory.
PON="$OUT/01_samurai_illumina/qdnaseq_local_pon"

# Require the exact completion marker.
test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = QDNASEQ_LOCAL_PON_SUCCESS

# Inspect the normal-control manifest.
sed -n '1,20p' "$PON/pon/normal_panel_manifest.tsv"

# Inspect leave-one-out control QC.
sed -n '1,20p' "$PON/qc/normal_panel_sample_qc.tsv"

# Inspect the panel summary.
sed -n '1,20p' "$PON/qdnaseq_local_pon_summary.tsv"

# Inspect corrected tumor segments.
sed -n '1,12p' "$PON/all_segments.seg"

# List corrected tumor bin and segment files.
find "$PON/bins" "$PON/segments" -maxdepth 1 -type f -print | sort
```

Do not interpret partial panel outputs when the completion marker is absent or different.

## Stage 01: ONT

```bash
# List ONT stage-01 files.
find "$OUT/01_samurai_ont" -maxdepth 3 -type f -print | sort | sed -n '1,100p'

# Read the barcode run summary.
sed -n '1,100p' "$OUT/01_samurai_ont/logs/run_summary.txt"

# Inspect the initial ichorCNA segment table.
sed -n '1,12p' "$OUT/01_samurai_ont/results/ichorcna/segments_logR_corrected_gistic.seg"

# List aligned ONT BAM files.
find "$OUT/01_samurai_ont/bam" -maxdepth 1 -name '*.bam' -print
```

Also inspect used, skipped, and warning FASTQ logs.

## Stage 02: refined segments

```bash
# List the refinement dataset directory.
find "$OUT/02_bam_refinement" -mindepth 1 -maxdepth 1 -type d -print

# Inspect the final refined segments.
sed -n '1,12p' "$OUT"/02_bam_refinement/*/04_final_results/final_segments.tsv

# Inspect refinement statistics.
sed -n '1,12p' "$OUT"/02_bam_refinement/*/01_tables/boundary_refinement_statistics.csv
```

Use `04_final_results/final_segments.tsv` as the primary refined segment table.

## Stage 03: final CNA tables

```bash
# Inspect final CNA events.
sed -n '1,20p' "$OUT/03_cna_codification/cna_events.tsv"

# Inspect cytogenomic notation.
sed -n '1,20p' "$OUT/03_cna_codification/cna_cytogenomic_notation.tsv"

# Count rows in both final tables.
wc -l \
  "$OUT/03_cna_codification/cna_events.tsv" \
  "$OUT/03_cna_codification/cna_cytogenomic_notation.tsv"
```

A table containing only a header can represent a CNA-flat sample; confirm the workflow summary and sample-level outputs before treating it as an error.

## Stage 04: plots

```bash
# List all generated plot files.
find "$OUT/04_cna_custom_plots" -maxdepth 2 -type f -print | sort

# Open the per-sample PDF on a graphical Linux workstation.
xdg-open "$OUT/04_cna_custom_plots/cna_per_sample_pages.pdf"

# Open the combined cohort profiles.
xdg-open "$OUT/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf"
```

On a headless server, copy PDFs to a workstation. Use the TSV tables for exact values.

## Stage 05: optional classifier and pathology

This directory exists only when `run_cna_classifier: true`.

```bash
# Inspect CNA-based research classifications.
sed -n '1,12p' "$OUT/05_cna_classifier/02_classification/cna_patient_classification.tsv"

# Inspect the knowledge summary.
sed -n '1,12p' "$OUT/05_cna_classifier/06_knowledge/sample_knowledge_summary.tsv"

# Inspect pathology comparison when a pathology CSV was supplied.
sed -n '1,12p' "$OUT/05_cna_classifier/07_pathology/pathology_concordance.tsv"
```

Classifier and pathology outputs are research interpretations derived from the CNA tables.

## Confirm a run before sharing it

```bash
# Require the workflow summary.
test -s "$OUT/06_workflow_summary/workflow_summary.txt"

# Require the final CNA event table.
test -s "$OUT/03_cna_codification/cna_events.tsv"

# Require the cytogenomic notation table.
test -s "$OUT/03_cna_codification/cna_cytogenomic_notation.tsv"

# Require the per-sample plot PDF.
test -s "$OUT/04_cna_custom_plots/cna_per_sample_pages.pdf"

# List non-empty task stderr files for review.
find "$OUT" -type f -name '*.command.err' -size +0c -print
```

Preserve the run YAML, sample table, generated samplesheet, OncoTracer commit, installation manifest, and workflow summary with any shared result.
