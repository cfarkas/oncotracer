# Three-sample HCC1143 public cohort

This optional example moves beyond the small Quick Start inputs and runs three paired-end low-pass whole-genome sequencing libraries—six physical FASTQ files—through the Illumina workflow. It is intended for users who have already completed [Quick verification](quick_start.md) and want to test automatic configuration and multi-sample output handling with public data.

!!! warning "Workflow demonstration, not a biological conclusion"
    This cohort is a reproducible software example. The verified gallery artifact and measured result summary are still pending. Do not infer treatment effects or clinical meaning from this three-library demonstration.

## Public data and provenance

The libraries come from the HCC1143 triple-negative breast-cancer cell line in public project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331), associated with [Ben-David et al., *Nature Communications* (2018)](https://doi.org/10.1038/s41467-018-05729-w).

| OncoTracer sample | Treatment | Run accession | Files used |
| --- | --- | --- | --- |
| `HCC1143_DMSO` | 0.05% DMSO | `SRR7085656` | paired R1/R2 FASTQs |
| `HCC1143_BEZ235` | 1 uM BEZ235 | `SRR7085655` | paired R1/R2 FASTQs |
| `HCC1143_TRAMETINIB` | 1 uM Trametinib | `SRR7085657` | paired R1/R2 FASTQs |

All three samples are labeled `TUMOR`. DMSO is the experimental treatment control, but its DNA still comes from a cancer cell line; it is not a matched normal genome. Tiny unpaired singleton files exposed by ENA are deliberately excluded because the Illumina workflow requires matched R1/R2 inputs.

Exact URLs, byte counts, and MD5 checksums are recorded in [`examples/hcc1143_lpwgs/manifest.tsv`](https://github.com/cfarkas/oncotracer/blob/main/examples/hcc1143_lpwgs/manifest.tsv). The six selected files total 1,158,812,143 bytes, approximately 1.08 GiB.

## Requirements

Use Linux with:

- Java 17 or newer;
- Nextflow;
- Docker, Singularity/Apptainer, or a working Conda setup;
- approximately 1.08 GiB for the compressed reads;
- at least 40 GiB of free working space;
- 16 CPU cores and 64 GiB RAM recommended for the complete run.

The first Illumina analysis also prepares the hg38 reference and BWA index. Allow roughly 1–2 hours depending on the network, storage, and CPU. Later runs reuse completed downloads, reference files, indexes, and Nextflow work where possible.

## Run the complete example

Start from a fresh clone:

```bash
git clone https://github.com/cfarkas/oncotracer.git # clone the pipeline
cd oncotracer                                      # run commands from the repository root
bash examples/hcc1143_lpwgs/run_example.sh --docker
```

The runner performs these steps:

1. downloads all six FASTQs;
2. verifies each file against the exact ENA byte count and MD5, then runs `gzip -t`;
3. writes the three-row sample table;
4. runs `--auto_params` to generate the Illumina YAML and workflow samplesheet;
5. performs a Nextflow stub wiring check;
6. runs the real workflow with `-resume`;
7. checks that all three samples and the principal output files are present.

Use a different supported execution environment by replacing `--docker` with `--singularity` or `--conda`.

## Download or prepare without running the analysis

Download and validate the FASTQs, then stop:

```bash
bash examples/hcc1143_lpwgs/run_example.sh --download-only
```

Download the data, generate the YAML and samplesheet, display them, then stop before the stub and real runs:

```bash
bash examples/hcc1143_lpwgs/run_example.sh --prepare-only
```

The runtime flag and action can be combined when needed:

```bash
bash examples/hcc1143_lpwgs/run_example.sh --singularity --prepare-only
```

## Generated files and directories

By default, the example uses the repository's `test/` directory:

```text
test/
├── public/hcc1143_lpwgs/
│   ├── HCC1143_DMSO_R1.fastq.gz
│   ├── HCC1143_DMSO_R2.fastq.gz
│   ├── HCC1143_BEZ235_R1.fastq.gz
│   ├── HCC1143_BEZ235_R2.fastq.gz
│   ├── HCC1143_TRAMETINIB_R1.fastq.gz
│   ├── HCC1143_TRAMETINIB_R2.fastq.gz
│   └── samples.csv
├── configs/hcc1143_lpwgs/
│   ├── illumina.auto.yml
│   └── illumina.samplesheet.csv
└── runs/hcc1143_lpwgs/
```

Set `COHORT_ROOT` to place these inputs, configurations, and outputs elsewhere:

```bash
COHORT_ROOT=/absolute/path/to/oncotracer-cohort \
  bash examples/hcc1143_lpwgs/run_example.sh --docker
```

The generated workflow samplesheet contains one row per paired library. The source status table is intentionally:

```csv
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
```

See [Automatic Setup](auto_params.md) for how `--auto_params` discovers the FASTQs and translates this table into runnable configuration.

## Completion checks and outputs

The runner considers the cohort complete only when it finds:

- three BAM files in `01_samurai_illumina/alignment/`;
- all three sample names in `01_samurai_illumina/qdnaseq/all_segments.seg`;
- `03_cna_codification/cna_events.tsv`;
- `04_cna_custom_plots/cna_per_sample_pages.pdf`;
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`;
- `06_workflow_summary/workflow_summary.txt`.

Start with the human-readable summary:

```bash
cat test/runs/hcc1143_lpwgs/06_workflow_summary/workflow_summary.txt
```

Then use [Output Files](outputs.md) to inspect the QC, segment, event, and plot files in context. A file being present confirms workflow completion; it does not by itself validate a diagnosis or treatment-associated effect.

## Resume an interrupted run

Run the same command again:

```bash
bash examples/hcc1143_lpwgs/run_example.sh --docker
```

Valid downloads are reused, and the real Nextflow invocation uses `-resume`. Keep the corresponding `work/` directory and do not change the relevant inputs or configuration if you want Nextflow to reuse completed tasks.

## Attribution and limitations

When presenting results from this example, cite both the source study and OncoTracer as described in [Citation and Research Use](citation_research_use.md). Record the exact OncoTracer commit, container digest, caller, bin size, reference, and any warnings alongside generated figures.

This small public cohort is useful for testing multi-sample execution and teaching input semantics. It is not a matched tumor/normal design, does not establish treatment causality, and is not a substitute for an appropriately powered biological or clinical study. See [Results Gallery](gallery.md) for the current verification status.
