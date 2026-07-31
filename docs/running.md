# Run OncoTracer

Run commands from the cloned repository directory, where `main.nf` is located. Choose Docker on a workstation/server or Singularity/Apptainer on HPC.

The Docker option uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).

## Choose a route

| Starting point | Route |
| --- | --- |
| Verify the installation with public data | [QuickStart Example 1](quick_start.md) |
| Use standard Illumina or ONT FASTQ names | [Automatic Setup](auto_params.md) |
| Run the three-sample public HCC1143 cohort | [QuickStart Example 2](public_cohort.md) |
| Configure six tumors and four controls | [Other Example Run](six_tumor_four_control.md); the FASTQs are not included |
| Use non-standard paths or settings | [Manual configuration](configuration.md) |
| Run a checked YAML | [Run a YAML](#run-a-yaml) |

## 1. Enter the repository and check the runtime

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Confirm the repository path and Nextflow installation.
pwd
nextflow -version

# Confirm Docker on a workstation or server.
command -v docker

# Confirm Singularity or Apptainer on HPC.
command -v singularity
command -v apptainer
```

Only one container runtime is required.

## 2. Generate a YAML automatically

### Illumina

Place paired reads and a sample table under one project directory:

```text
/home/student/oncotracer/project/input/illumina_fastq/
├── Patient_A_R1.fastq.gz
├── Patient_A_R2.fastq.gz
└── samples.csv
```

Paste this sample table:

```csv
sample_name,status
Patient_A,TUMOR
```

```bash
# Generate the Illumina YAML and samplesheet.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/project/input/illumina_fastq \
  --sample_table /home/student/oncotracer/project/input/illumina_fastq/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/illumina \
  --auto_outdir /home/student/oncotracer/project/runs/illumina_auto

# Run or resume the generated YAML with Docker.
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/illumina/illumina.auto.yml \
  -work-dir /home/student/oncotracer/project/work/illumina \
  -resume
```

### ONT

Place reads inside barcode directories and create an explicit barcode table:

```text
/home/student/oncotracer/project/input/fastq_pass/
├── barcode01/
│   └── reads.fastq.gz
└── samples.csv
```

Paste this sample table:

```csv
barcode,sample_name,status
barcode01,Patient_A,TUMOR
```

```bash
# Generate the ONT YAML.
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder /home/student/oncotracer/project/input/fastq_pass \
  --sample_table /home/student/oncotracer/project/input/fastq_pass/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/ont \
  --auto_outdir /home/student/oncotracer/project/runs/ont_auto

# Run or resume the generated YAML with Docker.
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/ont/ont.auto.yml \
  -work-dir /home/student/oncotracer/project/work/ont \
  -resume
```

On HPC, replace `--docker` with `--singularity` in the analysis command.

## 3. Manual configuration

```bash
# Copy an Illumina template for editing.
cp params/illumina.minimal.yml params/my_illumina.yml

# Edit the copied file.
nano params/my_illumina.yml

# Copy an ONT template when configuring ONT manually.
cp params/ont.minimal.yml params/my_ont.yml

# Edit the copied ONT file.
nano params/my_ont.yml
```

Keep all configured inputs and outputs below `lpwgs_root`.

<a id="run-a-yaml"></a>

## Run a YAML

```bash
# Check workflow wiring without executing the analysis tools.
nextflow run main.nf -stub-run --docker \
  -params-file params/my_illumina.yml

# Run or resume the Illumina YAML with Docker.
nextflow run main.nf --docker \
  -params-file params/my_illumina.yml \
  -resume
```

Use `params/my_ont.yml` for ONT or replace `--docker` with `--singularity` on HPC.

## Workflow stages

| Stage | Purpose |
| --- | --- |
| `01_samurai_illumina` or `01_samurai_ont` | Alignment and initial CNA calling |
| `02_bam_refinement` | BAM-supported boundary refinement |
| `03_cna_codification` | CNA event and cytogenomic tables |
| `04_cna_custom_plots` | Per-sample and cohort plots |
| `05_cna_classifier` | Optional research classifier and pathology comparison |
| `06_workflow_summary` | Important output locations |

The outer SAMURAI task can remain at `0 of 1` while its nested Nextflow workflow is active. This alone does not indicate a stalled run.

## Estimated time for the first analysis

An uncached run downloads hg38 and builds its BWA index. Indexing commonly takes **30–60 minutes** and the pinned task requests 72 GB, so use at least **80 GiB RAM**. Later runs reuse a valid index.

## Verify and resume

```bash
# Read the workflow summary.
cat /home/student/oncotracer/project/runs/illumina_auto/06_workflow_summary/workflow_summary.txt

# Confirm the final CNA event table.
ls -lh /home/student/oncotracer/project/runs/illumina_auto/03_cna_codification/cna_events.tsv

# List the generated PDF plots.
ls -lh /home/student/oncotracer/project/runs/illumina_auto/04_cna_custom_plots/*.pdf
```

```bash
# Resume the same run with the same YAML and work directory.
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/illumina/illumina.auto.yml \
  -work-dir /home/student/oncotracer/project/work/illumina \
  -resume
```

Do not delete the work directory before diagnosing an error; it contains the task logs used by `-resume`.
