# Programs Used

OncoTracer coordinates several tools and bundled scripts.

## Workflow Engine

- Nextflow: workflow execution, process isolation, resume support.
- Java: required by Nextflow.
- Conda/Mamba: environment management for classifier and GISTIC2 stages.

## CNA Generation and Refinement

Depending on mode and starting point:

- SAMURAI ONT wrapper for ONT barcode processing.
- ichorCNA-style ONT CNA outputs when starting from existing ONT results.
- qDNAseq/SAMURAI Illumina outputs when running Illumina mode.
- BAM-level local coverage refinement through `bam_cnv_boundary_refine.sh` and its bundled Python code.

## CNA Codification and Plotting

- `cna_to_cytogenomic_notation.py`: converts converter-ready CNA bins into event and notation tables.
- `plot_cna_events.py`: creates custom cohort and per-sample CNA plots.
- Python libraries used by plotting/codification include pandas, numpy, and matplotlib.

## Classifier and Reports

The classifier subworkflow uses:

- pandas/numpy/scipy/scikit-learn for table processing and classification support;
- matplotlib for plots;
- GISTIC2 for cohort-level recurrent CNA analysis when available/applicable;
- requests for literature metadata retrieval;
- reportlab and pypdf for PDF report generation;
- transformers/torch/safetensors/huggingface_hub for optional model layers.

## Network-Dependent Features

These can depend on internet access:

- public literature metadata/abstract retrieval;
- Hugging Face model downloads;
- automatic GISTIC2 hg38 refgene download if configured as `auto`.

The workflow is designed to keep deterministic outputs where possible if optional network/model steps fail.
