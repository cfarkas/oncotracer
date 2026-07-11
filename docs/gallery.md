# Gallery

These images are real OncoTracer copy-number analysis plots from the public example runs and the documented tutorial runs. They are included so new users can recognize successful output before opening their own result folders.

![Quickstart animation](assets/gallery/quickstart_walkthrough.svg)

## Illumina Copy-Number Outputs

![Illumina CNA genome overview](assets/gallery/illumina_cna_genome_overview.png)

![Illumina CNA event counts by sample](assets/gallery/illumina_cna_event_counts_by_sample.png)

![Illumina recurrent cytobands](assets/gallery/illumina_cna_recurrent_cytobands.png)

## ONT Copy-Number Outputs

![ONT CNA genome overview](assets/gallery/ont_cna_genome_overview.png)

![ONT CNA event counts by sample](assets/gallery/ont_cna_event_counts_by_sample.png)

![ONT recurrent cytobands](assets/gallery/ont_cna_recurrent_cytobands.png)

## SAMURAI Copy-Number Plots

The quickstart also writes SAMURAI caller plots inside the run folder:

```text
oncotracer/test/runs/illumina/01_samurai_illumina/cn_plots/qdnaseq/genome_plot.pdf
oncotracer/test/runs/illumina/01_samurai_illumina/qdnaseq/plots/DRR000542_bin_plot.pdf
oncotracer/test/runs/illumina/01_samurai_illumina/qdnaseq/plots/DRR000542_segment_plot.pdf
```

Open those PDFs after the Illumina quickstart to inspect the upstream qDNAseq/SAMURAI copy-number signal before OncoTracer boundary refinement and reporting.
