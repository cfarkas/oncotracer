# ONT configuration

Use this route for Oxford Nanopore Technologies FASTQs organized in barcode directories. Native v2 discovers and merges the selected reads, aligns with minimap2, counts genomic bins with HMMcopy, runs ichorCNA, refines CNA boundaries from BAM depth, and creates tables, plots, and summaries.

## Recommended: Automatic Setup

### Arrange barcode FASTQs

Point `--reads-folder` at the parent `fastq_pass` directory:

```text
project/input/fastq_pass/
├── barcode01/
│   ├── reads_001.fastq.gz
│   └── reads_002.fastq.gz
├── barcode02/
│   └── reads_001.fastq.gz
└── barcode03/
    └── reads_001.fastq.gz
```

Each selected barcode may contain one or more `.fastq`, `.fq`, `.fastq.gz`, or `.fq.gz` files directly inside it.

### Create the barcode table

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/input/fastq_pass"

cat > "$PROJECT_DIR/input/ont_samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Patient_B,TUMOR
barcode03,Patient_Normal,NORMAL
CSV
```

`barcode` must match a directory name exactly. At least one row must be `TUMOR`.

### Generate the YAML

```bash
PROJECT_DIR="$PWD/project"

oncotracer auto \
  --mode ont \
  --reads-folder "$PROJECT_DIR/input/fastq_pass" \
  --sample-table "$PROJECT_DIR/input/ont_samples.csv" \
  --config-dir "$PROJECT_DIR/config/ont" \
  --outdir "$PROJECT_DIR/results/ont"
```

Automatic Setup validates selected barcode directories and compressed FASTQs. It writes `ont.auto.yml` and `auto_params_manifest.tsv`; it does not start alignment or CNA analysis.

### Inspect and run

```bash
PROJECT_DIR="$PWD/project"

sed -n '1,200p' "$PROJECT_DIR/config/ont/ont.auto.yml"
cat "$PROJECT_DIR/config/ont/auto_params_manifest.tsv"

oncotracer run \
  --backend conda \
  --config "$PROJECT_DIR/config/ont/ont.auto.yml"

cat "$PROJECT_DIR/results/ont/06_workflow_summary/workflow_summary.txt"
```

A generated YAML resembles:

```yaml
mode: ont
lpwgs_root: /absolute/path/project
outdir: /absolute/path/project/results/ont
ont_folder: /absolute/path/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
ont_normal_folder: /absolute/path/project/input/fastq_pass
ont_normal_barcodes: barcode03
ont_normal_sample_names: Patient_Normal
run_cna_classifier: false
force: false
```

Barcode and sample-name lists are positional. The first barcode maps to the first sample name.

## Manual YAML

Use a manual file when selecting a subset of barcodes, using a custom reference, or applying advanced settings:

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/config" "$PROJECT_DIR/results/manual_ont"

cat > "$PROJECT_DIR/config/ont.manual.yml" <<YAML
mode: ont
lpwgs_root: $PROJECT_DIR
outdir: $PROJECT_DIR/results/manual_ont
ont_folder: $PROJECT_DIR/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
run_cna_classifier: false
force: false
YAML

oncotracer run \
  --backend conda \
  --config "$PROJECT_DIR/config/ont.manual.yml" \
  --dry-run

oncotracer run \
  --backend conda \
  --config "$PROJECT_DIR/config/ont.manual.yml"
```

## Optional normal/control barcodes

Add:

```yaml
ont_normal_folder: /absolute/path/project/input/normal_fastq_pass
ont_normal_barcodes: barcode01,barcode02
ont_normal_sample_names: Normal_A,Normal_B
ont_build_pon: true
```

Review the supported study design and native summary before interpreting normalized calls.

## Custom reference

```yaml
ont_ref: /absolute/path/project/reference/custom_reference.fa
```

The FASTA must exist and be visible to the selected backend. Keep it under `lpwgs_root` where possible.

## Completed-file age

For directories receiving live sequencing output, exclude newly written FASTQs:

```yaml
ont_min_age_minutes: 10
```

The default public examples use `0` because the downloaded files are complete.

## Force realignment

```yaml
ont_force_realign: true
```

Use this only when an existing ONT alignment is invalid or the relevant alignment inputs/settings changed. Prefer a new `outdir` for a scientifically distinct analysis.

## Main ONT settings

| Setting | Typical value | Purpose |
| --- | --- | --- |
| `ont_folder` | absolute directory | Parent containing tumor barcode directories |
| `ont_barcodes` | comma-separated names | Tumor barcode selection |
| `ont_sample_names` | comma-separated names | Biological names in matching order |
| `ont_analysis_type` | `liquid_biopsy` | Analysis preset |
| `ont_caller` | `ichorcna` | ONT CNA caller |
| `ont_binsize_kb` | `500` | Initial genomic bin width |
| `ont_ref` | optional FASTA | Custom reference |
| `ont_normal_folder` | optional directory | Parent containing normal barcodes |
| `ont_normal_barcodes` | optional names | Normal barcode selection |
| `ont_normal_sample_names` | optional names | Positional normal sample names |
| `ont_min_age_minutes` | `0` | Minimum FASTQ age before use |
| `ont_force_realign` | `false` | Deliberate alignment refresh |
| `run_cna_classifier` | `false` | Add native classifier/reports |
| `force` | `false` | Preserve reusable stages |

## Pre-run checks

```bash
PROJECT_DIR="$PWD/project"

find "$PROJECT_DIR/input/fastq_pass" -maxdepth 2 -type d -print | sort
find "$PROJECT_DIR/input/fastq_pass" -maxdepth 2 -type f -print | sort | head -30
gzip -t "$PROJECT_DIR/input/fastq_pass/barcode01/reads_001.fastq.gz"
sed -n '1,200p' "$PROJECT_DIR/config/ont/ont.auto.yml"
```

Confirm that every listed barcode exists, contains non-empty FASTQs, and maps to exactly one intended sample name.
