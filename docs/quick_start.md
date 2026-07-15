# Quick Start

This tutorial verifies a new OncoTracer installation with one public Illumina sample and one public Oxford Nanopore Technologies (ONT) sample. It downloads about **225 MB of compressed reads** in total. It is separate from the larger, optional [three-sample HCC1143 cohort](public_cohort.md).

## Before running

Complete [Installation](installation.md), then confirm these commands work:

```bash
git --version       # Git must be installed
java -version       # Java must be version 17 or newer
nextflow -version   # Nextflow must be available; run_test.sh can provide a local fallback
docker --version    # Docker must be installed
```

!!! warning "The first analysis is much larger than the example reads"
    On the first real run, SAMURAI downloads the hg38 reference (about **3.16 GB**) and BWA commonly takes **30–60 minutes** to create its index. This happens once; later `-resume` runs reuse it. Docker layers and workflow intermediates require additional disk space.

## Recommended: run and verify everything

Copy this complete terminal block:

```bash
git clone https://github.com/cfarkas/oncotracer.git  # create a fresh local clone
cd oncotracer                                        # enter the repository; main.nf is here
pwd                                                  # show the absolute location of this clone
bash run_test.sh --docker                            # prepare, run, and verify both public examples
```

`run_test.sh` performs these actions in order:

1. checks Java and Docker, and provides a repository-local Nextflow launcher if needed;
2. pulls the current `carlosfarkas/oncotracer:latest` Docker image;
3. downloads the public FASTQs, checks their expected size, MD5, and gzip integrity, and reuses valid existing copies;
4. writes absolute-path Illumina and ONT YAML files;
5. performs a stub wiring check for each branch;
6. runs the real Illumina/qDNAseq and ONT/ichorCNA analyses;
7. verifies the main tables, plots, and workflow summaries.

A successful run ends with:

```text
SUCCESS: both public workflows completed and produced the expected outputs.
```

!!! info "If Nextflow displays `0 of 1`"
    The outer `RUN_ILLUMINA_SAMURAI` or `RUN_ONT_SAMURAI` task waits for a nested SAMURAI workflow. Its counter can remain at `0 of 1` while alignment and CNA calling are active. The helper prints an activity message every 30 seconds. See [Troubleshooting](troubleshooting.md) before stopping it.

## Understand and run each command yourself

Use this route if you want to see what the helper does. Start from a fresh clone:

```bash
git clone https://github.com/cfarkas/oncotracer.git oncotracer-quickstart # clone into a clearly named folder
cd oncotracer-quickstart                                                  # enter it; run every main.nf command here
nextflow run main.nf --make_test                                         # download and validate reads, then write both YAML files
```

`--make_test` is a **preparation command**. It does not perform CNA analysis. It creates:

```text
test/
├── configs/
│   ├── illumina.quickstart.yml
│   └── ont.quickstart.yml
├── public/
│   ├── illumina_ERR12341627/
│   └── ont_DRR165691/
└── runs/
```

Inspect the generated files:

```bash
sed -n '1,120p' test/configs/illumina.quickstart.yml # display the Illumina YAML
sed -n '1,120p' test/configs/ont.quickstart.yml      # display the ONT YAML
```

### What the generated Illumina YAML means

The file is at `<your-clone>/test/configs/illumina.quickstart.yml`. The following box is an annotated **preview of YAML file contents**, not a terminal command. `--make_test` writes your clone's real absolute path in place of `/absolute/path/oncotracer-quickstart`.

```yaml
mode: illumina                                      # use paired-end Illumina processing
lpwgs_root: /absolute/path/oncotracer-quickstart/test # common input/output parent mounted in Docker
outdir: /absolute/path/oncotracer-quickstart/test/runs/illumina # final run directory
illumina_samplesheet: /absolute/path/oncotracer-quickstart/test/public/illumina_ERR12341627/illumina.samplesheet.csv # table linking the sample to R1 and R2
illumina_analysis_type: solid_biopsy                # SAMURAI analysis preset
illumina_caller: qdnaseq                            # CNA caller used for Illumina
illumina_binsize_kb: 100                            # analyze copy number in 100-kilobase bins
run_cna_classifier: false                           # skip the optional classifier/pathology stage
force: true                                         # allow this disposable tutorial output to be refreshed
```

The YAML points to this generated samplesheet:

```csv
sample,fastq_1,fastq_2,status
ERR12341627,/absolute/path/oncotracer-quickstart/test/public/illumina_ERR12341627/ERR12341627_1.fastq.gz,/absolute/path/oncotracer-quickstart/test/public/illumina_ERR12341627/ERR12341627_2.fastq.gz,tumor
```

The sample is [ENA ERR12341627](https://www.ebi.ac.uk/ena/browser/view/ERR12341627), an OVCAR8 cancer whole-genome sequencing run represented by a paired R1/R2 FASTQ set.

### What the generated ONT YAML means

The file is at `<your-clone>/test/configs/ont.quickstart.yml`. Again, this is an annotated **preview of YAML contents**; it is not a command to paste into the terminal.

```yaml
mode: ont                                           # use Oxford Nanopore processing
lpwgs_root: /absolute/path/oncotracer-quickstart/test # common input/output parent mounted in Docker
outdir: /absolute/path/oncotracer-quickstart/test/runs/ont # final run directory
ont_folder: /absolute/path/oncotracer-quickstart/test/public/ont_DRR165691/fastq_pass # parent of barcode folders
ont_barcodes: barcode01                             # barcode directory that contains this sample's FASTQ
ont_sample_names: DRR165691                         # sample name assigned to barcode01
ont_analysis_type: liquid_biopsy                    # SAMURAI analysis preset
ont_caller: ichorcna                                # CNA caller used for ONT
ont_binsize_kb: 500                                 # analyze copy number in 500-kilobase bins
ont_min_age_minutes: 0                              # accept the completed tutorial FASTQ immediately
run_cna_classifier: false                           # skip the optional classifier/pathology stage
force: true                                         # allow this disposable tutorial output to be refreshed
```

### Wiring check versus real run

Run the Illumina branch:

```bash
nextflow run main.nf -stub-run --docker -params-file test/configs/illumina.quickstart.yml # create placeholder task outputs and check workflow wiring
nextflow run main.nf --docker -params-file test/configs/illumina.quickstart.yml -resume   # perform the real Illumina analysis
```

Then run the ONT branch:

```bash
nextflow run main.nf -stub-run --docker -params-file test/configs/ont.quickstart.yml # create placeholder task outputs and check workflow wiring
nextflow run main.nf --docker -params-file test/configs/ont.quickstart.yml -resume   # perform the real ONT analysis
```

A `-stub-run` is fast because it replaces process commands with lightweight placeholders. It checks parameter parsing and workflow connections, but it is **not full validation of the FASTQ contents, tools, reference preparation, or real analysis**. Only the commands without `-stub-run` perform the analysis.

`-resume` tells Nextflow to reuse unchanged tasks already present in its cache and `work/` directory. Keep `work/` if you want to resume after an interruption. It does not mean “continue from a particular sample,” and Nextflow reruns a task when its relevant command or inputs changed.

## Confirm the results

Start with the two summaries:

```bash
cat test/runs/illumina/06_workflow_summary/workflow_summary.txt # Illumina output inventory
cat test/runs/ont/06_workflow_summary/workflow_summary.txt      # ONT output inventory
```

Important output locations are:

```text
test/runs/illumina/
├── 01_samurai_illumina/        # alignment and qDNAseq results
├── 03_cna_codification/        # CNA event and notation tables
├── 04_cna_custom_plots/        # OncoTracer PDF plots
└── 06_workflow_summary/        # human-readable output summary

test/runs/ont/
├── 01_samurai_ont/             # alignment and ichorCNA results
├── 03_cna_codification/        # CNA event and notation tables
├── 04_cna_custom_plots/        # OncoTracer PDF plots
└── 06_workflow_summary/        # human-readable output summary
```

See [Output Files](outputs.md) for interpretation and [Gallery](gallery.md) for example plots.

## Next: run the real six-FASTQ cohort

The default verification deliberately uses small, single-sample inputs. After it succeeds, the opt-in HCC1143 example demonstrates a three-sample Illumina cohort: three paired libraries, or six physical FASTQ files. The read download is **1.08 GiB**.

```bash
bash examples/hcc1143_lpwgs/run_example.sh --docker # download, validate, configure, run, and verify all three samples
```

Read the [complete public-cohort tutorial](public_cohort.md) and the repository's [`examples/hcc1143_lpwgs`](https://github.com/cfarkas/oncotracer/tree/main/examples/hcc1143_lpwgs) notes for provenance, resource expectations, preparation-only options, and results.

## Next: run your own data

- Use [Automatic Setup](auto_params.md) as the recommended default for an Illumina FASTQ folder or ONT barcode tree.
- Use [Manual YAML Editing](configuration/yaml_basics.md) as the second option when automatic detection does not fit.
- Use [Pathology and Classifier](configuration/pathology.md) only when your pathology sample identifiers match your sequencing sample identifiers.

Use `--singularity` instead of `--docker` on an HPC system configured with Apptainer/Singularity.
