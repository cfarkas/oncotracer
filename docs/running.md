# Run OncoTracer

Run every command from the cloned repository directory—the directory that contains `main.nf`.

## Choose the shortest correct route

| Starting point | What to do |
| --- | --- |
| You want to verify the installation with public data | Run `bash run_test.sh --docker`; see [Quick Start](quick_start.md). |
| You have paired Illumina FASTQs with regular R1/R2 names | Generate configuration with `--auto_params`, then run the generated YAML. |
| You have ONT FASTQs in barcode directories | Generate configuration with `--auto_params`, then run the generated YAML. |
| Automatic naming does not fit the study | Create a manual [Illumina YAML](configuration/illumina.md) or [ONT YAML](configuration/ont.md). |
| You already have a checked YAML | Go directly to [Check wiring, then run](#check-wiring-then-run). |

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

## 2. Recommended: generate configuration from a reads folder

`--auto_params` writes configuration files and stops. After inspecting them, launch a second command for the real analysis.

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

Generate, inspect, and run:

```bash
ROOT=$(pwd)
mkdir -p project/config/illumina project/runs                          # create output parents
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder "$ROOT/project/input/illumina_fastq" \
  --sample_table "$ROOT/project/input/illumina_fastq/samples.csv" \
  --auto_config_dir "$ROOT/project/config/illumina" \
  --auto_outdir "$ROOT/project/runs/illumina_auto"                    # create files; no analysis yet
sed -n '1,120p' project/config/illumina/illumina.auto.yml              # inspect the YAML
sed -n '1,20p' project/config/illumina/illumina.samplesheet.csv        # inspect detected R1/R2 paths
nextflow run main.nf --docker -params-file project/config/illumina/illumina.auto.yml -resume # real analysis
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

Generate, inspect, and run:

```bash
ROOT=$(pwd)
mkdir -p project/config/ont project/runs                               # create output parents
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder "$ROOT/project/input/fastq_pass" \
  --sample_table "$ROOT/project/input/fastq_pass/samples.csv" \
  --auto_config_dir "$ROOT/project/config/ont" \
  --auto_outdir "$ROOT/project/runs/ont_auto"                         # create YAML; no analysis yet
sed -n '1,160p' project/config/ont/ont.auto.yml                        # inspect barcode/sample mappings
nextflow run main.nf --docker -params-file project/config/ont/ont.auto.yml -resume # real analysis
```

Automatic setup validates its supported file layout and compressed FASTQs. See [Automatic Setup](auto_params.md) for multiple samples, normal controls, filename rules, and optional destinations.

## 3. Manual configuration route

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

## Check wiring, then run

The optional stub command checks that Nextflow can construct the selected workflow:

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml # optional workflow-wiring check
```

A stub uses placeholder task outputs. It does **not** align reads, validate the full toolchain, download/test the reference, or verify scientific outputs. Check real input files as described in [Input Files](inputs.md), then start the real run:

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
| `05_cna_classifier` | Only when `run_cna_classifier: true` | Runs optional CNA classification and pathology concordance. |
| `06_workflow_summary` | Always | Records important output paths. |

The `01` wrapper launches an upstream SAMURAI Nextflow workflow. Therefore the outer Nextflow display can remain at `RUN_*_SAMURAI (0 of 1)` while alignment and CNA tasks are active inside it. This alone does not mean the run is stalled. The first Illumina run can also spend substantial time downloading and indexing hg38; later runs reuse those files below `lpwgs_root`.

## Watch and verify a run

Keep the terminal open. A successful run ends with `Succeeded` and no `Failed` count. After completion, inspect the summary and key results:

```bash
cat project/runs/illumina_auto/06_workflow_summary/workflow_summary.txt # show important paths
ls -lh project/runs/illumina_auto/03_cna_codification/cna_events.tsv    # verify the event table
ls -lh project/runs/illumina_auto/04_cna_custom_plots/*.pdf             # verify plot PDFs
```

For a manual run or ONT run, replace `project/runs/illumina_auto` with the exact `outdir` from that YAML.

If the command stops, read the first `ERROR` message and see [Troubleshooting](troubleshooting.md). Do not delete `work/` before diagnosing the problem because it contains task logs needed by `-resume`.

## Resume and rerun safely

`-resume` tells Nextflow to reuse completed tasks when their inputs, command, and configuration are unchanged:

```bash
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume # continue or efficiently repeat
```

Use the same command, repository, YAML, `outdir`, and `work/` directory. If you change inputs or settings, Nextflow reruns affected tasks.

Keep `force: false` for real projects. For a different scientific configuration, copy the YAML and use a new `outdir`; this preserves the original results and makes comparisons auditable.
