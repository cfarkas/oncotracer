# OncoTracer

![OncoTracer: sequencing reads to copy-number alterations](docs/assets/oncotracer-hero.png)

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://cfarkas.github.io/oncotracer/)
[![Docker](https://img.shields.io/badge/docker-carlosfarkas%2Foncotracer-blue)](https://hub.docker.com/r/carlosfarkas/oncotracer)
[![Nextflow](https://img.shields.io/badge/nextflow-%E2%89%A525.04-green)](https://www.nextflow.io/)

OncoTracer is a Nextflow research workflow for **low-pass whole-genome sequencing (LP-WGS)**. It accepts Illumina single-end or paired-end FASTQ files and Oxford Nanopore Technologies (ONT) FASTQ files, then reports **copy-number alterations (CNAs)**: genomic regions with gains or losses of DNA.

```text
FASTQ -> SAMURAI qDNAseq/ichorCNA -> boundary refinement -> CNA tables -> plots and reports
```

Read the [documentation](https://cfarkas.github.io/oncotracer/).

For a complete public patient-cohort demonstration, use the [Full Tutorial](https://cfarkas.github.io/oncotracer/full_tutorial/): all 12 single-end plasma libraries currently exposed by PRJNA754199, from checksum-validated download through SAMURAI plots, boundary-refinement statistics, and research-use CNA interpretation.

## Before you start

Use Linux and install these host prerequisites:

- [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)
- [Java 17 or newer](https://www.nextflow.io/docs/latest/install.html#requirements)
- [Nextflow](https://www.nextflow.io/docs/latest/install.html)
- [Docker Engine](https://docs.docker.com/engine/install/) or, on HPC, [Apptainer](https://apptainer.org/docs/admin/main/installation.html)
- Python 3, samtools, BWA, minimap2, pigz, and curl or wget for the host-side stage-01/reference helpers

> [!IMPORTANT]
> Launch every OncoTracer preparation, test, and analysis with `nextflow run`.
> `--docker` and `--singularity` are options on that Nextflow command;
> Nextflow selects and starts the required containers. Do not type `docker run`,
> `docker exec`, `apptainer run`, `apptainer exec`, `singularity run`, or
> `singularity exec` to start OncoTracer yourself.

The first real analysis downloads the hg38 reference (about **3.16 GB**) and
BWA may take **30–60 minutes** to index it. That pinned task requests 72 GB,
so an uncached first run needs at least 80 GiB of addressable RAM. A valid
cached index is reused by later runs. Container images and working files
require additional disk space.

<a id="quick-verification-one-illumina-and-one-ont-sample"></a>

## QuickStart Example 1: one Illumina + one ONT sample

This is the smallest end-to-end check. It downloads about **225 MB of public reads**, runs both branches, and verifies their expected outputs:

```bash
git clone https://github.com/cfarkas/oncotracer.git /home/student/oncotracer
cd /home/student/oncotracer

nextflow run /home/student/oncotracer/main.nf --make_test \
  --test_root /home/student/oncotracer/test

nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/illumina.quickstart.yml \
  -work-dir /home/student/oncotracer/test/work/illumina \
  -resume

nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/ont.quickstart.yml \
  -work-dir /home/student/oncotracer/test/work/ont \
  -resume

python3 /home/student/oncotracer/examples/quickstart/verify_outputs.py \
  --test-root /home/student/oncotracer/test
```

Run the two analyses one after the other. Java, Git, Nextflow, and the selected container runtime must already be installed. See [QuickStart Example 1](https://cfarkas.github.io/oncotracer/quick_start/) for the generated YAML, verification command, expected runtime behavior, and output paths.

<a id="real-three-sample-public-example-six-fastqs"></a>

## QuickStart Example 2: three-sample public cohort

The optional HCC1143 example uses **1.08 GiB**: three paired LP-WGS samples, or six FASTQ files. After following the literal download and validation commands in the [public-cohort tutorial](https://cfarkas.github.io/oncotracer/public_cohort/), generate the configuration and run it directly through Nextflow:

```bash
cd /home/student/oncotracer
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/test/public/hcc1143_lpwgs \
  --sample_table /home/student/oncotracer/test/public/hcc1143_lpwgs/samples.csv \
  --auto_config_dir /home/student/oncotracer/test/configs/hcc1143_lpwgs \
  --auto_outdir /home/student/oncotracer/test/runs/hcc1143_lpwgs

nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer/test/configs/hcc1143_lpwgs/illumina.auto.yml \
  -work-dir /home/student/oncotracer/test/work/hcc1143_lpwgs \
  -resume
```

Allow at least 40 GiB of working space and 80 GiB of addressable RAM; the
pinned BWA task alone requests 72 GB. Read the [example notes](examples/hcc1143_lpwgs/README.md),
[public-cohort tutorial](https://cfarkas.github.io/oncotracer/public_cohort/),
and [result gallery](https://cfarkas.github.io/oncotracer/gallery/) before starting.

<a id="six-tumors-four-controls-local-pon"></a>

## QuickStart Example 3: six tumors + four normal controls

This paired-end Illumina pattern uses `ONCO001` through `ONCO006` as tumors and `CTRL001` through `CTRL004` as a four-sample local qDNAseq panel of normals (PoN). Keep the local data outside the Git clone. After placing the 20 FASTQs and ten-row `samples.csv` under `/home/student/oncotracer_projects/onco6_ctrl4`, the two OncoTracer commands are:

```bash
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer_projects/onco6_ctrl4/input/fastq \
  --sample_table /home/student/oncotracer_projects/onco6_ctrl4/input/samples.csv \
  --auto_config_dir /home/student/oncotracer_projects/onco6_ctrl4/config \
  --auto_outdir /home/student/oncotracer_projects/onco6_ctrl4/results

CUDA_VISIBLE_DEVICES="" NVIDIA_VISIBLE_DEVICES=none \
NXF_OPTS="-XX:ActiveProcessorCount=20 -Xms512m -Xmx8g" \
taskset --cpu-list 0-19 \
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.auto.yml \
  -work-dir /home/student/oncotracer_projects/onco6_ctrl4/work/analysis \
  -resume
```

The [six-tumor/four-control guide](https://cfarkas.github.io/oncotracer/six_tumor_four_control/) shows the exact FASTQ names, sample table, GNU Screen steps, generated PoN settings, completion checks, and tumor-only corrected outputs. On HPC, change only the Nextflow option `--docker` to `--singularity`; never invoke the container runtime yourself.

Before copying the hardcoded CPU list, confirm at least 80 GiB of addressable
RAM and follow the guide's `taskset -pc $$` check to verify that logical CPUs
0–19 belong to your allocation.

## Run your own FASTQs

The recommended default is to point `--auto_params` at a reads folder and a small tumor/normal table. OncoTracer detects uniform Illumina single-end files, Illumina pairs, or ONT barcode folders and creates the YAML and Illumina samplesheet:

- [Automatic setup from your FASTQ folder](https://cfarkas.github.io/oncotracer/auto_params/) — recommended default
- [Manual YAML and path guide](https://cfarkas.github.io/oncotracer/configuration/yaml_basics/) — second option for unusual layouts or advanced settings
- [Input-file formats](https://cfarkas.github.io/oncotracer/inputs/)

The real analysis command uses `-resume`, which lets Nextflow continue from unchanged completed steps after an interruption.

### Illumina local panel of normals

> [!IMPORTANT]
> Current `main` fixes a fail-open path where Illumina `NORMAL` rows could
> be accepted while PoN refinement was disabled, so controls were aligned but
> not applied to tumor correction. That configuration now stops before
> analysis instead of silently ignoring the controls.

For Illumina cohorts, rows marked `NORMAL` can build a run-local qDNAseq panel
of normals (PoN). Automatic Setup disables the local PoN when there are no
normal rows, stops with a configuration error when there is exactly one, and
enables it when there are at least two. The reference is the per-bin median
log2 signal across the selected controls, which is then used to correct the
tumor profiles. Corrected CNA outputs contain `TUMOR` samples only; controls
remain provenance inputs and are recorded in the manifest and QC files.

Control handling is fail-closed: OncoTracer does not accept `NORMAL` rows
while PoN construction is off, and an enabled panel must name every and only
the normal rows. All tumor and normal BAMs use one coherent alignment stage,
bin definition, paired-read setting, and MAPQ threshold. The normal-panel QC
compares each control with the median of the other `N-1` controls
(leave-one-out) in `qc/normal_panel_sample_qc.tsv`. The six explicit settings
are `illumina_build_pon`, `illumina_pon_normal_samples`,
`illumina_pon_min_normals`, `illumina_pon_name`,
`illumina_pon_min_mapq`, and `illumina_pon_r_container`.

PoN generation invalidates the prior completion marker before work starts and
publishes `qdnaseq_local_pon.done` atomically and last, after required outputs
pass validation. Its exact success value is `QDNASEQ_LOCAL_PON_SUCCESS`; a
missing, empty, or different value means the panel is incomplete even if
partial files remain. Automatic Setup similarly publishes the samplesheet and
manifest before the YAML, making the YAML the final transactional commit point
for a runnable configuration. An interrupted build therefore cannot be
mistaken for a complete panel. See
[Illumina configuration](https://cfarkas.github.io/oncotracer/configuration/illumina/)
for the six settings and [output files](https://cfarkas.github.io/oncotracer/outputs/#illumina-local-panel-of-normals)
for the audit artifacts.

## Main outputs

- `01_samurai_illumina/qdnaseq_local_pon/`: local-PoN manifest, QC, reference bins, and tumor-only corrected CNA outputs when enabled
- `06_workflow_summary/workflow_summary.txt`: start here after a run
- `03_cna_codification/cna_events.tsv`: CNA event table
- `03_cna_codification/cna_cytogenomic_notation.tsv`: cytogenomic notation
- `04_cna_custom_plots/cna_per_sample_pages.pdf`: per-sample plots
- `04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`: cohort plot

## Research-use limitation

OncoTracer is not a standalone diagnostic system. Results require expert review, laboratory validation, and integration with pathology and orthogonal molecular evidence.
