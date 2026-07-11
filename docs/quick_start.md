# Quick Start

This page contains complete, copy-pasteable public-data runs. Run every `main.nf` command from the cloned repository directory.

## Requirements

Install Nextflow and Docker first. See [Installation](installation.md).

## Complete Illumina public test

Copy this entire code box into a terminal:

```bash
git clone https://github.com/cfarkas/oncotracer.git oncotracer-illumina                 # clone a fresh repository for the Illumina tutorial
cd oncotracer-illumina                                                               # enter the repository; main.nf is in this directory
current_dir=$(pwd)                                                                   # save the absolute repository path
echo $current_dir                                                                    # verify where inputs, work files, and results will be created
nextflow -version                                                                    # verify that Nextflow is installed
docker --version                                                                     # verify that Docker is installed and visible
nextflow run main.nf --make_test                                                     # download both public FASTQ examples and generate absolute-path YAML files
sed -n 1,120p test/configs/illumina.quickstart.yml                                   # inspect the generated Illumina YAML
nextflow run main.nf -stub-run --docker -params-file test/configs/illumina.quickstart.yml  # validate parameters and workflow connections
nextflow run main.nf --docker -params-file test/configs/illumina.quickstart.yml -resume    # run SAMURAI/qDNAseq and all OncoTracer steps
cat test/runs/illumina/06_workflow_summary/workflow_summary.txt                      # confirm the final output locations
```

`--make_test` creates this YAML using the real absolute path of your clone:

```yaml
mode: illumina                                      # select the Illumina branch
lpwgs_root: /absolute/path/oncotracer-illumina/test # folder mounted into Docker; all test inputs and outputs are below it
outdir: /absolute/path/oncotracer-illumina/test/runs/illumina # main results directory
illumina_samplesheet: /absolute/path/oncotracer-illumina/test/public/illumina_DRR000542/illumina.samplesheet.csv # paired FASTQ table
illumina_samurai_outdir: /absolute/path/oncotracer-illumina/test/runs/illumina/01_samurai_illumina # qDNAseq/SAMURAI output
illumina_analysis_type: solid_biopsy                # SAMURAI analysis preset
illumina_caller: qdnaseq                            # Illumina CNA caller
illumina_binsize_kb: 100                            # qDNAseq bin size in kilobases
run_cna_classifier: false                           # keep optional classifier/pathology reporting off for this tutorial
force: true                                         # allow the tutorial outputs to be refreshed
```

The generated samplesheet is:

```csv
sample,fastq_1,fastq_2,status
DRR000542,/absolute/path/oncotracer-illumina/test/public/illumina_DRR000542/DRR000542_1.fastq.gz,/absolute/path/oncotracer-illumina/test/public/illumina_DRR000542/DRR000542_2.fastq.gz,tumor
```

Expected plots include the SAMURAI/qDNAseq genome, bin, and segment PDFs under `test/runs/illumina/01_samurai_illumina/`, plus OncoTracer plots under `test/runs/illumina/04_cna_custom_plots/`.

## Complete ONT public test

Copy this entire code box into a terminal:

```bash
git clone https://github.com/cfarkas/oncotracer.git oncotracer-ont                      # clone a fresh repository for the ONT tutorial
cd oncotracer-ont                                                                    # enter the repository; main.nf is in this directory
current_dir=$(pwd)                                                                   # save the absolute repository path
echo $current_dir                                                                    # verify where inputs, work files, and results will be created
nextflow -version                                                                    # verify that Nextflow is installed
docker --version                                                                     # verify that Docker is installed and visible
nextflow run main.nf --make_test                                                     # download both public FASTQ examples and generate absolute-path YAML files
sed -n 1,120p test/configs/ont.quickstart.yml                                        # inspect the generated ONT YAML
nextflow run main.nf -stub-run --docker -params-file test/configs/ont.quickstart.yml # validate parameters and workflow connections
nextflow run main.nf --docker -params-file test/configs/ont.quickstart.yml -resume   # run SAMURAI/ichorCNA and all OncoTracer steps
cat test/runs/ont/06_workflow_summary/workflow_summary.txt                           # confirm the final output locations
```

`--make_test` creates this YAML using the real absolute path of your clone:

```yaml
mode: ont                                           # select the Oxford Nanopore branch
lpwgs_root: /absolute/path/oncotracer-ont/test      # folder mounted into Docker; all test inputs and outputs are below it
outdir: /absolute/path/oncotracer-ont/test/runs/ont # main results directory
ont_folder: /absolute/path/oncotracer-ont/test/public/ont_DRR165691/fastq_pass # folder containing barcode FASTQ directories
ont_barcodes: barcode01                             # barcode directory to analyze
ont_sample_names: DRR165691                         # biological sample name assigned to barcode01
ont_samurai_outdir: /absolute/path/oncotracer-ont/test/runs/ont/01_samurai_ont # ichorCNA/SAMURAI output
ont_analysis_type: liquid_biopsy                    # SAMURAI analysis preset
ont_caller: ichorcna                                # ONT CNA caller
ont_binsize_kb: 500                                 # ichorCNA bin size in kilobases
ont_min_age_minutes: 0                              # analyze completed FASTQ immediately
run_cna_classifier: false                           # keep optional classifier/pathology reporting off for this tutorial
force: true                                         # refresh supported tutorial outputs
```

Expected outputs include ichorCNA segment/depth tables under `test/runs/ont/01_samurai_ont/results/ichorcna/` and the ichorCNA-derived copy-number profile at `test/runs/ont/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf`.

## Use your own Illumina FASTQ files

```bash
git clone https://github.com/cfarkas/oncotracer.git              # clone the repository
cd oncotracer                                                    # enter the repository
cp params/illumina.minimal.yml params/my_illumina.yml            # create an editable YAML without changing the versioned template
realpath .                                                       # print the absolute repository path
realpath /path/to/Sample_A_R1.fastq.gz                            # print the absolute read-1 FASTQ path
realpath /path/to/Sample_A_R2.fastq.gz                            # print the absolute read-2 FASTQ path
nano params/my_illumina.yml                                      # replace every example path with your absolute paths
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml # validate before computation
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume   # run from FASTQ to final reports
```

```yaml
mode: illumina                                      # select the Illumina branch
lpwgs_root: /home/user/oncotracer_project           # common parent visible to Docker for input and output paths
outdir: /home/user/oncotracer_project/runs/sample_a # main output directory
illumina_samplesheet: /home/user/oncotracer_project/input/illumina_samplesheet.csv # CSV pointing to paired FASTQ files
illumina_samurai_outdir: /home/user/oncotracer_project/runs/sample_a/01_samurai_illumina # upstream output directory
```

## Use your own ONT FASTQ files

```bash
git clone https://github.com/cfarkas/oncotracer.git              # clone the repository
cd oncotracer                                                    # enter the repository
cp params/ont.minimal.yml params/my_ont.yml                      # create an editable YAML without changing the versioned template
realpath .                                                       # print the absolute repository path
realpath /path/to/fastq_pass                                     # print the absolute ONT FASTQ folder path
find /path/to/fastq_pass -maxdepth 2 -type f | head              # confirm the barcode FASTQ layout
nano params/my_ont.yml                                           # replace every example path, barcode, and sample name
nextflow run main.nf -stub-run --docker -params-file params/my_ont.yml # validate before computation
nextflow run main.nf --docker -params-file params/my_ont.yml -resume   # run from FASTQ to final reports
```

```yaml
mode: ont                                           # select the Oxford Nanopore branch
lpwgs_root: /home/user/oncotracer_project           # common parent visible to Docker for input and output paths
outdir: /home/user/oncotracer_project/runs/ont_a    # main output directory
ont_folder: /home/user/oncotracer_project/input/fastq_pass # folder containing barcode FASTQ directories
ont_barcodes: barcode01,barcode02                   # barcode directories in input order
ont_sample_names: Patient_A,Patient_B               # sample names in exactly the same order
ont_samurai_outdir: /home/user/oncotracer_project/runs/ont_a/01_samurai_ont # upstream output directory
```

Use `--singularity` instead of `--docker` on supported HPC systems.
