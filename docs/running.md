# Run OncoTracer

Run every command from the cloned repository directory—the directory that contains `main.nf`.

## Choose the shortest correct route

| Starting point | What to do |
| --- | --- |
| You want to verify the installation with public data | Run `bash run_test.sh --docker`; see [QuickStart Example 1](quick_start.md). |
| You have uniformly single-end or paired Illumina FASTQs with supported names | Generate configuration with `--auto_params`, then run the generated YAML. |
| You have ONT FASTQs in barcode directories | Generate configuration with `--auto_params`, then run the generated YAML. |
| Automatic naming does not fit the study | Use the second option: a manual [Illumina YAML](configuration/illumina.md) or [ONT YAML](configuration/ont.md). |
| You already have a checked YAML | Go directly to [Run a YAML](#run-a-yaml). |

## 1. Enter the repository and choose a runtime

```bash
cd oncotracer                                                        # main.nf must be in the current directory
pwd                                                                  # print the absolute repository path
nextflow -version                                                     # verify Nextflow
docker --version                                                      # verify Docker for the commands below
```

Use exactly one runtime flag on a real run:

| Flag | Use when |
| --- | --- |
| `--docker` | Docker is installed. Recommended for a Linux workstation. |
| `--singularity` | Singularity or Apptainer is configured, usually on HPC. |
| `--conda` | Containers are unavailable and the Conda environments are prepared. |

The examples below use Docker. Replace only `--docker` with `--singularity` when appropriate.

## 2. Recommended default: generate configuration from a reads folder

`--auto_params` writes configuration files and stops. After Automatic Setup finishes, launch a second command for the real analysis.

### Illumina

Required layout:

```text
project/input/illumina_fastq/
├── Patient_A_R1.fastq.gz
├── Patient_A_R2.fastq.gz
└── samples.csv
```

`samples.csv`:

```csv
sample_name,status
Patient_A,TUMOR
```

The example below assumes the repository is `/home/student/oncotracer`.
OncoTracer creates the configuration and result folders; no `mkdir` command
is needed.

Generate and run:

```bash
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/project/input/illumina_fastq \
  --sample_table /home/student/oncotracer/project/input/illumina_fastq/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/illumina \
  --auto_outdir /home/student/oncotracer/project/runs/illumina_auto
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/illumina/illumina.auto.yml \
  -resume
```

### ONT

Required layout:

```text
project/input/fastq_pass/
├── barcode01/
│   └── reads.fastq.gz
└── samples.csv
```

`samples.csv`:

```csv
barcode,sample_name,status
barcode01,Patient_A,TUMOR
```

Generate and run:

```bash
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder /home/student/oncotracer/project/input/fastq_pass \
  --sample_table /home/student/oncotracer/project/input/fastq_pass/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/ont \
  --auto_outdir /home/student/oncotracer/project/runs/ont_auto
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/ont/ont.auto.yml \
  -resume
```

Automatic setup validates its supported file layout and compressed FASTQs. See [Automatic Setup](auto_params.md) for multiple samples, normal controls, filename rules, and optional destinations.

## 3. Second option: manual configuration

For Illumina:

```bash
cp params/illumina.minimal.yml params/my_illumina.yml                  # create an editable copy
nano params/my_illumina.yml                                           # replace every example path
```

For ONT:

```bash
cp params/ont.minimal.yml params/my_ont.yml                            # create an editable copy
nano params/my_ont.yml                                                # replace paths, barcodes, and names
```

In Nano, save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`. Print the saved file with `sed -n '1,160p' params/my_illumina.yml` or the ONT equivalent. All configured input, reference, and output paths must be absolute and below `lpwgs_root`.

Do not add `illumina_samurai_outdir` or `ont_samurai_outdir`. OncoTracer derives stage `01` from `outdir`.

<a id="check-wiring-then-run"></a>

## Run a YAML

After checking the input files described in [Input Files](inputs.md), start the
real run:

```bash
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume # real Illumina analysis
```

For ONT, substitute `params/my_ont.yml`.

## What the real workflow does

These stages run in order inside the configured `outdir`:

| Stage | Runs when | What it does |
| --- | --- | --- |
| `01_samurai_illumina` or `01_samurai_ont` | Always | Aligns FASTQs and calls initial broad CNAs with qDNAseq (Illumina) or ichorCNA (ONT). |
| `02_bam_refinement` | Always | Uses BAM read depth to test and refine coarse CNA boundaries; keeps an original boundary when evidence for moving it is insufficient. |
| `03_cna_codification` | Always | Converts final CNA segments into event tables and cytogenomic notation. |
| `04_cna_custom_plots` | Always | Creates per-sample and combined copy-number PDF plots. |
| `05_cna_classifier` | Only when `run_cna_classifier: true` | Runs optional CNA classification and, when pathology is supplied, concordance reporting. |
| `06_workflow_summary` | Always | Records important output paths. |

The `01` wrapper launches an upstream SAMURAI Nextflow workflow. Therefore the outer Nextflow display can remain at `RUN_*_SAMURAI (0 of 1)` while alignment and CNA tasks are active inside it. This alone does not mean the run is stalled. The first Illumina run can also spend substantial time downloading and indexing hg38; later runs reuse those files below `lpwgs_root`.

## Watch and verify a run

Keep the terminal open. A successful run ends with `Succeeded` and no `Failed` count. After completion, inspect the summary and key results:

```bash
cat /home/student/oncotracer/project/runs/illumina_auto/06_workflow_summary/workflow_summary.txt
ls -lh /home/student/oncotracer/project/runs/illumina_auto/03_cna_codification/cna_events.tsv
ls -lh /home/student/oncotracer/project/runs/illumina_auto/04_cna_custom_plots/*.pdf
```

For a manual run or ONT run, replace `/home/student/oncotracer/project/runs/illumina_auto` with the exact `outdir` from that YAML.

If the command stops, read the first `ERROR` message and see [Troubleshooting](troubleshooting.md). Do not delete `work/` before diagnosing the problem because it contains task logs needed by `-resume`.

## Resume and rerun safely

`-resume` tells Nextflow to reuse completed tasks when their inputs, command, and configuration are unchanged:

```bash
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume # continue or efficiently repeat
```

Use the same command, repository, YAML, `outdir`, and `work/` directory. If you change inputs or settings, Nextflow reruns affected tasks.

Keep `force: false` for real projects. For a different scientific configuration, copy the YAML and use a new `outdir`; this keeps the original results separate and easier to compare.
