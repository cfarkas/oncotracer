# ONT Configuration

Use this route for Oxford Nanopore FASTQs arranged in barcode directories. OncoTracer combines reads per selected barcode, aligns them, calls broad CNAs with SAMURAI/ichorCNA, refines boundaries, and creates tables, plots, and a workflow summary.

Automatic Setup is recommended because it validates the barcode folders and writes matching barcode/sample lists.

## Recommended: Automatic Setup

### 1. Arrange the barcode folders

Point `--reads_folder` to the `fastq_pass` directory, not to an individual barcode.

```text
/home/student/oncotracer/project/input/fastq_pass/
├── barcode01/
│   ├── reads_001.fastq.gz
│   └── reads_002.fastq.gz
├── barcode02/
│   └── reads_001.fastq.gz
└── barcode03/
    └── reads_001.fastq.gz
```

Each barcode can contain one or more `.fastq`, `.fq`, `.fastq.gz`, or `.fq.gz` files directly inside it.

### 2. Create the barcode table

```bash
# Create the ONT barcode-to-sample table.
nano /home/student/oncotracer/project/input/fastq_pass/samples.csv
```

Paste exactly this content:

```csv
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Patient_B,TUMOR
barcode03,Control_1,NORMAL
```

Save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

The barcode value must match the directory name exactly. At least one row must be `TUMOR`.

### 3. Generate the ONT YAML

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Generate the ONT YAML and manifest.
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder /home/student/oncotracer/project/input/fastq_pass \
  --sample_table /home/student/oncotracer/project/input/fastq_pass/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/ont \
  --auto_outdir /home/student/oncotracer/project/results/ont
```

Automatic Setup checks that each barcode exists and contains readable FASTQs. It then stops before analysis.

```bash
# Inspect the generated ONT YAML.
sed -n '1,160p' /home/student/oncotracer/project/config/ont/ont.auto.yml

# Inspect sample counts and the YAML hash.
cat /home/student/oncotracer/project/config/ont/auto_params_manifest.tsv
```

The generated YAML resembles:

```yaml
mode: ont
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/results/ont
ont_folder: /home/student/oncotracer/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
ont_normal_folder: /home/student/oncotracer/project/input/fastq_pass
ont_normal_barcodes: barcode03
ont_normal_sample_names: Control_1
run_cna_classifier: false
force: false
```

Barcode and sample-name lists are positional: the first barcode maps to the first sample name.

### 4. Run with Docker or Singularity

```bash
# Run or resume the ONT analysis with Docker.
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/ont/ont.auto.yml \
  -work-dir /home/student/oncotracer/project/work/ont \
  -resume
```

```bash
# Run or resume the same analysis with Singularity or Apptainer on HPC.
nextflow run main.nf --singularity \
  -params-file /home/student/oncotracer/project/config/ont/ont.auto.yml \
  -work-dir /home/student/oncotracer/project/work/ont \
  -resume
```

```bash
# Read the final workflow summary.
cat /home/student/oncotracer/project/results/ont/06_workflow_summary/workflow_summary.txt
```

## Manual setup

Use manual setup when selecting only part of a barcode tree, using a custom reference, or adding advanced settings.

### 1. Copy and edit the YAML

```bash
# Enter the repository.
cd /home/student/oncotracer

# Copy the minimal ONT template.
cp params/ont.minimal.yml params/my_ont.yml

# Edit paths, barcodes, and sample names.
nano params/my_ont.yml
```

Example manual YAML:

```yaml
mode: ont
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/results/manual_ont
ont_folder: /home/student/oncotracer/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
force: false
```

Optional normal/control settings can be added to the same YAML:

```yaml
ont_normal_folder: /home/student/oncotracer/project/input/fastq_pass
ont_normal_barcodes: barcode03
ont_normal_sample_names: Control_1
```

### 2. Check and run

```bash
# Inspect the saved YAML.
sed -n '1,160p' params/my_ont.yml

# Check workflow wiring without running the scientific tools.
nextflow run main.nf -stub-run --docker \
  -params-file params/my_ont.yml

# Run or resume the manual ONT configuration.
nextflow run main.nf --docker \
  -params-file params/my_ont.yml \
  -resume
```

Use `--singularity` instead of `--docker` on HPC.

## Important settings

| Setting | Purpose |
| --- | --- |
| `ont_folder` | Parent directory containing barcode folders |
| `ont_barcodes` | Comma-separated tumor barcode directories |
| `ont_sample_names` | Comma-separated sample names in the same order |
| `ont_analysis_type` | SAMURAI preset; normally `liquid_biopsy` |
| `ont_caller` | Current supported caller: `ichorcna` |
| `ont_binsize_kb` | Initial ichorCNA bin size; default `500` |
| `ont_min_age_minutes` | Minimum age of input files; use `0` for completed runs |
| `ont_normal_*` | Optional normal/control folder, barcodes, and sample names |
| `force` | Keep `false` for normal project runs |

See [Automatic Setup](../auto_params.md#ont-example) for another complete example and [Output Files](../outputs.md) for result locations.
