# Automatic Setup from a Reads Folder

`--auto_params` is the recommended way to configure standard Illumina or ONT FASTQs. You provide a reads folder and a small sample table. OncoTracer validates the layout, writes a YAML, and then stops. For Illumina, it also writes the FASTQ samplesheet.

The later analysis runs through Nextflow with either `--docker` or `--singularity`. The Docker option uses [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer).

## What Automatic Setup creates

```text
project/
├── input/
│   ├── fastq/
│   └── samples.csv
├── config/
│   ├── auto_params_manifest.tsv
│   ├── illumina.auto.yml or ont.auto.yml
│   └── illumina.samplesheet.csv     # Illumina only
└── results/
```

`--auto_params` does not align reads or call CNAs. Use the generated YAML in a second `nextflow run` command.

## Illumina example

### 1. Arrange the FASTQs

For paired-end data, place one R1 and one R2 file per sample directly in the reads folder:

```text
/data/study42/input/fastq/
├── Sample_A_R1.fastq.gz
├── Sample_A_R2.fastq.gz
├── Sample_B_R1.fastq.gz
├── Sample_B_R2.fastq.gz
├── Control_1_R1.fastq.gz
├── Control_1_R2.fastq.gz
├── Control_2_R1.fastq.gz
└── Control_2_R2.fastq.gz
```

Supported paired names include `<sample>_R1.fastq.gz` with `<sample>_R2.fastq.gz`, or `<sample>_1.fastq.gz` with `<sample>_2.fastq.gz`. The same patterns ending in `.fq.gz` are accepted.

For single-end data, use one exact file per sample, such as `Sample_A.fastq.gz`. Do not mix single-end and paired-end samples in one run.

### 2. Create the sample table

```bash
# Create the Illumina sample table.
nano /data/study42/input/samples.csv
```

Paste exactly this content:

```csv
sample_name,status
Sample_A,TUMOR
Sample_B,TUMOR
Control_1,NORMAL
Control_2,NORMAL
```

Save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

The `sample_name` value must match the FASTQ prefix exactly. `status` must be `TUMOR` or `NORMAL`.

Normal-control behavior is simple:

- no `NORMAL` rows: run without a local qDNAseq panel of normals;
- one `NORMAL` row: configuration stops because one control is insufficient;
- two or more `NORMAL` rows: use those controls to build the local qDNAseq reference and report corrected tumor outputs.

### 3. Generate and inspect the YAML

```bash
# Enter the cloned OncoTracer repository.
cd /home/student/oncotracer

# Generate the Illumina YAML, samplesheet, and manifest.
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode illumina \
  --reads_folder /data/study42/input/fastq \
  --sample_table /data/study42/input/samples.csv \
  --auto_config_dir /data/study42/config \
  --auto_outdir /data/study42/results

# Inspect the generated YAML.
sed -n '1,140p' /data/study42/config/illumina.auto.yml

# Inspect the generated FASTQ-to-sample mapping.
sed -n '1,20p' /data/study42/config/illumina.samplesheet.csv

# Inspect sample counts and file hashes.
cat /data/study42/config/auto_params_manifest.tsv
```

A generated YAML for the table above includes the normal-control settings automatically:

```yaml
mode: illumina
lpwgs_root: /data/study42
outdir: /data/study42/results
illumina_samplesheet: /data/study42/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
illumina_build_pon: true
illumina_pon_normal_samples: "Control_1,Control_2"
illumina_pon_min_normals: 2
illumina_pon_name: Control_1_Control_2_PoN
illumina_pon_min_mapq: 37
illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1
run_cna_classifier: false
force: false
```

### 4. Run with Docker or Singularity

Choose one runtime command.

```bash
# Run or resume the generated Illumina configuration with Docker.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /data/study42/config/illumina.auto.yml \
  -work-dir /data/study42/work \
  -resume
```

```bash
# Run or resume the same configuration with Singularity or Apptainer on HPC.
nextflow run /home/student/oncotracer/main.nf --singularity \
  -params-file /data/study42/config/illumina.auto.yml \
  -work-dir /data/study42/work \
  -resume
```

```bash
# Read the final workflow summary.
cat /data/study42/results/06_workflow_summary/workflow_summary.txt
```

## ONT example

### 1. Arrange barcode folders

Point `--reads_folder` to the `fastq_pass` directory. Each selected barcode directory must contain at least one FASTQ file.

```text
/data/study42/fastq_pass/
├── barcode01/
│   └── reads_001.fastq.gz
├── barcode02/
│   └── reads_001.fastq.gz
└── barcode03/
    └── reads_001.fastq.gz
```

### 2. Create the barcode table

```bash
# Create the ONT barcode-to-sample table.
nano /data/study42/fastq_pass/samples.csv
```

Paste exactly this content:

```csv
barcode,sample_name,status
barcode01,Sample_A,TUMOR
barcode02,Sample_B,TUMOR
barcode03,Control_1,NORMAL
```

The barcode value must match the directory name exactly. At least one row must be `TUMOR`.

### 3. Generate and inspect the ONT YAML

```bash
# Enter the cloned OncoTracer repository.
cd /home/student/oncotracer

# Generate the ONT YAML and manifest.
nextflow run /home/student/oncotracer/main.nf --auto_params \
  --mode ont \
  --reads_folder /data/study42/fastq_pass \
  --sample_table /data/study42/fastq_pass/samples.csv \
  --auto_config_dir /data/study42/config_ont \
  --auto_outdir /data/study42/results_ont

# Inspect the generated ONT YAML.
sed -n '1,160p' /data/study42/config_ont/ont.auto.yml

# Inspect sample counts and the YAML hash.
cat /data/study42/config_ont/auto_params_manifest.tsv
```

A generated ONT YAML contains matching barcode and sample-name lists:

```yaml
mode: ont
lpwgs_root: /data/study42
outdir: /data/study42/results_ont
ont_folder: /data/study42/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Sample_A,Sample_B
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
ont_normal_folder: /data/study42/fastq_pass
ont_normal_barcodes: barcode03
ont_normal_sample_names: Control_1
run_cna_classifier: false
force: false
```

### 4. Run with Docker or Singularity

```bash
# Run or resume the generated ONT configuration with Docker.
nextflow run /home/student/oncotracer/main.nf --docker \
  -params-file /data/study42/config_ont/ont.auto.yml \
  -work-dir /data/study42/work_ont \
  -resume
```

```bash
# Run or resume the same ONT configuration with Singularity or Apptainer on HPC.
nextflow run /home/student/oncotracer/main.nf --singularity \
  -params-file /data/study42/config_ont/ont.auto.yml \
  -work-dir /data/study42/work_ont \
  -resume
```

## Common setup errors

- The sample name does not match the FASTQ prefix.
- A paired sample is missing R1 or R2.
- Single-end and paired-end files are mixed in one Illumina run.
- A gzip file is empty or incomplete.
- An ONT barcode listed in the CSV does not exist.
- A configured input or output path is outside `lpwgs_root`.

Use [Input Files and Folder Layouts](inputs.md) for the accepted naming rules and [Troubleshooting](troubleshooting.md) for error messages.
