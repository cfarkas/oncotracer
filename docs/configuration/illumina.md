# Illumina Configuration

Use this route for single-end or paired-end Illumina FASTQs. OncoTracer aligns the reads, calls broad CNAs with SAMURAI/qDNAseq, refines boundaries, and creates tables, plots, and a workflow summary.

Automatic Setup is recommended when the FASTQs use supported names. The analysis then runs with `--docker` or `--singularity`.

## Recommended: Automatic Setup

### 1. Arrange paired-end FASTQs

```text
/home/student/oncotracer/project/input/fastq/
├── Patient_A_R1.fastq.gz
├── Patient_A_R2.fastq.gz
├── Patient_B_R1.fastq.gz
├── Patient_B_R2.fastq.gz
├── Control_1_R1.fastq.gz
├── Control_1_R2.fastq.gz
├── Control_2_R1.fastq.gz
└── Control_2_R2.fastq.gz
```

The text before `_R1` and `_R2` is the sample name. Names ending in `_1` and `_2`, and equivalent `.fq.gz` files, are also accepted. For single-end data, use one exact file such as `Patient_A.fastq.gz`. Do not mix single-end and paired-end samples in one run.

### 2. Create the sample table

```bash
# Create the Illumina sample table.
nano /home/student/oncotracer/project/input/samples.csv
```

Paste exactly this content:

```csv
sample_name,status
Patient_A,TUMOR
Patient_B,TUMOR
Control_1,NORMAL
Control_2,NORMAL
```

Save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

No `NORMAL` rows means no local panel of normals. One normal is rejected. Two or more normals are used to build the run-local qDNAseq reference, and corrected CNA outputs contain the tumor rows.

### 3. Generate the YAML and samplesheet

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Generate the Illumina YAML, samplesheet, and manifest.
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/project/input/fastq \
  --sample_table /home/student/oncotracer/project/input/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/illumina \
  --auto_outdir /home/student/oncotracer/project/results/illumina
```

Automatic Setup validates every listed FASTQ and stops before analysis.

```bash
# Inspect the generated YAML.
sed -n '1,140p' /home/student/oncotracer/project/config/illumina/illumina.auto.yml

# Inspect the generated FASTQ-to-sample mapping.
sed -n '1,20p' /home/student/oncotracer/project/config/illumina/illumina.samplesheet.csv

# Inspect sample counts and file hashes.
cat /home/student/oncotracer/project/config/illumina/auto_params_manifest.tsv
```

The generated YAML resembles:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/results/illumina
illumina_samplesheet: /home/student/oncotracer/project/config/illumina/illumina.samplesheet.csv
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

```bash
# Run or resume the Illumina analysis with Docker.
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/illumina/illumina.auto.yml \
  -work-dir /home/student/oncotracer/project/work/illumina \
  -resume
```

```bash
# Run or resume the same analysis with Singularity or Apptainer on HPC.
nextflow run main.nf --singularity \
  -params-file /home/student/oncotracer/project/config/illumina/illumina.auto.yml \
  -work-dir /home/student/oncotracer/project/work/illumina \
  -resume
```

```bash
# Read the final workflow summary.
cat /home/student/oncotracer/project/results/illumina/06_workflow_summary/workflow_summary.txt
```

## Manual setup

Use manual setup when the FASTQ naming does not match the automatic patterns or advanced settings are required.

### 1. Create the Illumina samplesheet

```bash
# Create the manual Illumina samplesheet.
nano /home/student/oncotracer/project/input/illumina.samplesheet.csv
```

Paste exactly this content for paired-end data:

```csv
sample,fastq_1,fastq_2,status
Patient_A,/home/student/oncotracer/project/input/fastq/Patient_A_R1.fastq.gz,/home/student/oncotracer/project/input/fastq/Patient_A_R2.fastq.gz,tumor
Patient_B,/home/student/oncotracer/project/input/fastq/Patient_B_R1.fastq.gz,/home/student/oncotracer/project/input/fastq/Patient_B_R2.fastq.gz,tumor
Control_1,/home/student/oncotracer/project/input/fastq/Control_1_R1.fastq.gz,/home/student/oncotracer/project/input/fastq/Control_1_R2.fastq.gz,normal
Control_2,/home/student/oncotracer/project/input/fastq/Control_2_R1.fastq.gz,/home/student/oncotracer/project/input/fastq/Control_2_R2.fastq.gz,normal
```

For single-end data, keep the four-column header and leave `fastq_2` empty.

### 2. Copy and edit the YAML

```bash
# Enter the repository.
cd /home/student/oncotracer

# Copy the minimal Illumina template.
cp params/illumina.minimal.yml params/my_illumina.yml

# Edit the copied YAML.
nano params/my_illumina.yml
```

Example manual YAML:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/results/manual_illumina
illumina_samplesheet: /home/student/oncotracer/project/input/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
illumina_build_pon: true
illumina_pon_normal_samples: Control_1,Control_2
illumina_pon_min_normals: 2
illumina_pon_name: Control_1_Control_2_PoN
illumina_pon_min_mapq: 37
illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1
run_cna_classifier: false
force: false
```

### 3. Check and run

```bash
# Inspect the saved YAML.
sed -n '1,160p' params/my_illumina.yml

# Check workflow wiring without running the scientific tools.
nextflow run main.nf -stub-run --docker \
  -params-file params/my_illumina.yml

# Run or resume the manual Illumina configuration.
nextflow run main.nf --docker \
  -params-file params/my_illumina.yml \
  -resume
```

Use `--singularity` instead of `--docker` on HPC.

## Important settings

| Setting | Purpose |
| --- | --- |
| `illumina_samplesheet` | Absolute path to `sample,fastq_1,fastq_2,status` CSV |
| `illumina_analysis_type` | SAMURAI preset; normally `solid_biopsy` |
| `illumina_caller` | Current supported caller: `qdnaseq` |
| `illumina_binsize_kb` | Initial qDNAseq bin size; default `100` |
| `illumina_build_pon` | Enable the local normal reference |
| `illumina_pon_normal_samples` | Exact comma-separated normal sample IDs |
| `illumina_pon_min_normals` | Minimum number of normal controls |
| `force` | Keep `false` for normal project runs |

See [Output Files](../outputs.md#illumina-local-panel-of-normals) for the normal-control manifest, QC, corrected tumor bins, segments, and completion marker.
