# Outputs

OncoTracer writes a numbered output structure inside `outdir`.

```text
outdir/
  01_samurai_illumina/ or 01_samurai_ont/
  02_bam_refinement/
  03_cna_codification/
  04_cna_custom_plots/
  05_cna_classifier/          # when enabled
  06_workflow_summary/
```

## Key Files

| File | Meaning |
| --- | --- |
| `06_workflow_summary/workflow_summary.txt` | Short run summary and important output paths. |
| `03_cna_codification/cna_events.tsv` | CNA event table. |
| `03_cna_codification/cna_cytogenomic_notation.tsv` | Cytogenomic notation table. |
| `04_cna_custom_plots/cna_per_sample_pages.pdf` | Per-sample CNA plot pages. |
| `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf` | Cohort-level genome profile plot. |

## Example Plots

![Illumina CNA overview](assets/tutorial/illumina_cna_genome_overview.png)

![ONT CNA overview](assets/tutorial/ont_cna_genome_overview.png)
