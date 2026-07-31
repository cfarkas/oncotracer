# Other Example Run: six Illumina tumors and four controls

This page is a **configuration template for user-provided data**. The repository does not contain FASTQs for `ONCO001`–`ONCO006` or `CTRL001`–`CTRL004`, and OncoTracer does not download them. The commands below will stop until you provide all 20 paired-end FASTQ files.

This is not a QuickStart example. Use [QuickStart Example 1](quick_start.md) or [QuickStart Example 2](public_cohort.md) for downloadable public data.

The example uses six tumor samples and four normal controls. Automatic Setup creates an Illumina YAML that enables a run-local qDNAseq panel of normals, and corrected CNA outputs contain the six tumors.

Use `--docker` with [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer), or use `--singularity` on a configured HPC system.

## 1. Create the project folders

```bash
# Create the FASTQ, configuration, result, and work directories.
mkdir -p \
  /home/student/oncotracer_projects/onco6_ctrl4/input/fastq \
  /home/student/oncotracer_projects/onco6_ctrl4/config \
  /home/student/oncotracer_projects/onco6_ctrl4/results \
  /home/student/oncotracer_projects/onco6_ctrl4/work
```

## 2. Supply the 20 FASTQ files

Place exactly one R1/R2 pair for each sample directly in `input/fastq`:

```text
/home/student/oncotracer_projects/onco6_ctrl4/
├── input/
│   ├── samples.csv
│   └── fastq/
│       ├── ONCO001_R1.fastq.gz
│       ├── ONCO001_R2.fastq.gz
│       ├── ONCO002_R1.fastq.gz
│       ├── ONCO002_R2.fastq.gz
│       ├── ONCO003_R1.fastq.gz
│       ├── ONCO003_R2.fastq.gz
│       ├── ONCO004_R1.fastq.gz
│       ├── ONCO004_R2.fastq.gz
│       ├── ONCO005_R1.fastq.gz
│       ├── ONCO005_R2.fastq.gz
│       ├── ONCO006_R1.fastq.gz
│       ├── ONCO006_R2.fastq.gz
│       ├── CTRL001_R1.fastq.gz
│       ├── CTRL001_R2.fastq.gz
│       ├── CTRL002_R1.fastq.gz
│       ├── CTRL002_R2.fastq.gz
│       ├── CTRL003_R1.fastq.gz
│       ├── CTRL003_R2.fastq.gz
│       ├── CTRL004_R1.fastq.gz
│       └── CTRL004_R2.fastq.gz
├── config/
├── results/
└── work/
```

The sample name is the filename text before `_R1` or `_R2`. Consolidate multiple lanes before using Automatic Setup so each sample has one R1 and one R2 file.

```bash
# Count the FASTQ files in the expected directory.
find /home/student/oncotracer_projects/onco6_ctrl4/input/fastq \
  -maxdepth 1 -type f -name '*.fastq.gz' | wc -l
```

The command must print `20`. It will not print `20` until the user-provided files are present.

## 3. Create the tumor/normal table

```bash
# Create the sample table in Nano.
nano /home/student/oncotracer_projects/onco6_ctrl4/input/samples.csv
```

Paste exactly this content:

```csv
sample_name,status
ONCO001,TUMOR
ONCO002,TUMOR
ONCO003,TUMOR
ONCO004,TUMOR
ONCO005,TUMOR
ONCO006,TUMOR
CTRL001,NORMAL
CTRL002,NORMAL
CTRL003,NORMAL
CTRL004,NORMAL
```

Save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

```bash
# Display the saved sample table.
sed -n '1,20p' /home/student/oncotracer_projects/onco6_ctrl4/input/samples.csv
```

## 4. Generate the YAML automatically

Automatic Setup validates the 20 FASTQs, writes the Illumina samplesheet and YAML, and then stops. It does not analyze the reads.

```bash
# Enter the cloned OncoTracer repository.
cd /home/student/oncotracer

# Generate the six-tumor/four-control YAML and samplesheet.
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer_projects/onco6_ctrl4/input/fastq \
  --sample_table /home/student/oncotracer_projects/onco6_ctrl4/input/samples.csv \
  --auto_config_dir /home/student/oncotracer_projects/onco6_ctrl4/config \
  --auto_outdir /home/student/oncotracer_projects/onco6_ctrl4/results \
  -work-dir /home/student/oncotracer_projects/onco6_ctrl4/work/auto_params
```

```bash
# Inspect the generated YAML.
sed -n '1,140p' /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.auto.yml

# Inspect the generated FASTQ-to-sample mapping.
sed -n '1,20p' /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.samplesheet.csv

# Inspect sample counts and file hashes.
cat /home/student/oncotracer_projects/onco6_ctrl4/config/auto_params_manifest.tsv
```

The generated YAML should contain:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: "CTRL001,CTRL002,CTRL003,CTRL004"
illumina_pon_min_normals: 4
illumina_pon_name: CTRL001_CTRL002_CTRL003_CTRL004_PoN
illumina_pon_min_mapq: 37
illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1
```

```bash
# Confirm six tumor rows in the generated samplesheet.
grep -c ',tumor$' /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.samplesheet.csv

# Confirm four normal rows in the generated samplesheet.
grep -c ',normal$' /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.samplesheet.csv
```

The commands must print `6` and `4`.

## 5. Run with Docker or Singularity

Choose one runtime command.

```bash
# Run or resume the analysis with Docker.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.auto.yml \
  -work-dir /home/student/oncotracer_projects/onco6_ctrl4/work/analysis \
  -resume
```

```bash
# Run or resume the same analysis with Singularity or Apptainer on HPC.
nextflow run /home/student/oncotracer/main.nf --singularity \
  -params-file /home/student/oncotracer_projects/onco6_ctrl4/config/illumina.auto.yml \
  -work-dir /home/student/oncotracer_projects/onco6_ctrl4/work/analysis \
  -resume
```

Keep the same YAML and work directory when resuming an interrupted run.

## 6. Verify the panel and tumor outputs

```bash
# Set convenient paths for the result checks.
OUT=/home/student/oncotracer_projects/onco6_ctrl4/results
PON="$OUT/01_samurai_illumina/qdnaseq_local_pon"

# Require the exact local-PoN completion marker.
test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = QDNASEQ_LOCAL_PON_SUCCESS \
  && echo "PoN completed successfully"

# Inspect the normal-control manifest.
sed -n '1,10p' "$PON/pon/normal_panel_manifest.tsv"

# Inspect leave-one-out quality control for the four controls.
sed -n '1,10p' "$PON/qc/normal_panel_sample_qc.tsv"

# List the corrected tumor bin files.
find "$PON/bins" -maxdepth 1 -type f -name '*_markdup_bins.bed' -printf '%f\n' | sort

# Read the workflow summary.
cat "$OUT/06_workflow_summary/workflow_summary.txt"
```

The manifest and normal QC should contain `CTRL001`–`CTRL004`. Corrected bin files should contain `ONCO001`–`ONCO006` and no controls.

Four controls form a small, run-specific reference. Review their QC before interpreting CNA results. OncoTracer is for research use and is not a standalone diagnostic system.
