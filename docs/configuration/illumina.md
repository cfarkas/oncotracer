# Illumina configuration

Use this route for single-end or paired-end Illumina FASTQs. Native v2 validates the inputs, aligns with BWA, performs samtools/Picard processing, runs qDNAseq or a local qDNAseq normal panel, refines CNA boundaries from BAM depth, and creates CNA tables, plots, and summaries.

## Recommended: Automatic Setup

### Arrange the FASTQs

Paired-end example:

```text
project/input/illumina_fastq/
├── Patient_A_R1.fastq.gz
├── Patient_A_R2.fastq.gz
├── Patient_B_R1.fastq.gz
├── Patient_B_R2.fastq.gz
├── Control_A_R1.fastq.gz
├── Control_A_R2.fastq.gz
├── Control_B_R1.fastq.gz
└── Control_B_R2.fastq.gz
```

Single-end files may be named `<sample>.fastq.gz`. Do not mix layouts in one run.

### Create the sample table

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/input/illumina_fastq"

cat > "$PROJECT_DIR/input/illumina_samples.csv" <<'CSV'
sample_name,status
Patient_A,TUMOR
Patient_B,TUMOR
Control_A,NORMAL
Control_B,NORMAL
CSV
```

`sample_name` must match the FASTQ filename prefix exactly.

### Generate the YAML and samplesheet

```bash
PROJECT_DIR="$PWD/project"

oncotracer auto \
  --mode illumina \
  --reads-folder "$PROJECT_DIR/input/illumina_fastq" \
  --sample-table "$PROJECT_DIR/input/illumina_samples.csv" \
  --config-dir "$PROJECT_DIR/config/illumina" \
  --outdir "$PROJECT_DIR/results/illumina"
```

Automatic Setup validates every gzip file and writes:

```text
project/config/illumina/
├── auto_params_manifest.tsv
├── illumina.auto.yml
└── illumina.samplesheet.csv
```

It does not start the analysis.

### Inspect and run

```bash
PROJECT_DIR="$PWD/project"

sed -n '1,180p' "$PROJECT_DIR/config/illumina/illumina.auto.yml"
sed -n '1,30p' "$PROJECT_DIR/config/illumina/illumina.samplesheet.csv"
cat "$PROJECT_DIR/config/illumina/auto_params_manifest.tsv"

oncotracer run \
  --backend conda \
  --config "$PROJECT_DIR/config/illumina/illumina.auto.yml"

cat "$PROJECT_DIR/results/illumina/06_workflow_summary/workflow_summary.txt"
```

## Local qDNAseq panel of normals

Automatic Setup applies this rule:

| Normal rows | Behavior |
| ---: | --- |
| 0 | Run qDNAseq without a local panel |
| 1 | Reject the configuration |
| 2 or more | Build and apply a run-local median-log₂ reference |

Generated settings:

```yaml
illumina_build_pon: true
illumina_pon_normal_samples: Control_A,Control_B
illumina_pon_min_normals: 2
illumina_pon_name: Control_A_Control_B_PoN
illumina_pon_min_mapq: 37
```

All tumor and normal BAMs use the same alignment, bin definition, paired-read setting, and mapping-quality threshold. The panel stores the per-bin control reference and subtracts it from each tumor profile. Normal samples remain reference/QC inputs; corrected downstream bins, segments, CNA events, and plots contain tumor samples only.

Review:

```text
01_samurai_illumina/qdnaseq_local_pon/
├── pon/normal_panel_manifest.tsv
├── qc/normal_panel_sample_qc.tsv
├── all_tumors.qdnaseq_pon_corrected_bins.tsv
└── qdnaseq_local_pon.done
```

The completion marker must contain `QDNASEQ_LOCAL_PON_SUCCESS`.

## Manual samplesheet

Use a manual samplesheet for unusual filenames:

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/config"

cat > "$PROJECT_DIR/config/illumina.samplesheet.csv" <<CSV
sample,fastq_1,fastq_2,status
Patient_A,$PROJECT_DIR/input/illumina_fastq/Patient_A_R1.fastq.gz,$PROJECT_DIR/input/illumina_fastq/Patient_A_R2.fastq.gz,tumor
Patient_B,$PROJECT_DIR/input/illumina_fastq/Patient_B_R1.fastq.gz,$PROJECT_DIR/input/illumina_fastq/Patient_B_R2.fastq.gz,tumor
Control_A,$PROJECT_DIR/input/illumina_fastq/Control_A_R1.fastq.gz,$PROJECT_DIR/input/illumina_fastq/Control_A_R2.fastq.gz,normal
Control_B,$PROJECT_DIR/input/illumina_fastq/Control_B_R1.fastq.gz,$PROJECT_DIR/input/illumina_fastq/Control_B_R2.fastq.gz,normal
CSV
```

For single-end data, retain the four-column header and leave `fastq_2` empty for every row.

## Manual YAML

```bash
PROJECT_DIR="$PWD/project"

cat > "$PROJECT_DIR/config/illumina.manual.yml" <<YAML
mode: illumina
lpwgs_root: $PROJECT_DIR
outdir: $PROJECT_DIR/results/manual_illumina
illumina_samplesheet: $PROJECT_DIR/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
illumina_build_pon: true
illumina_pon_normal_samples: Control_A,Control_B
illumina_pon_min_normals: 2
illumina_pon_name: Control_A_Control_B_PoN
illumina_pon_min_mapq: 37
run_cna_classifier: false
force: false
YAML

oncotracer run \
  --backend conda \
  --config "$PROJECT_DIR/config/illumina.manual.yml" \
  --dry-run

oncotracer run \
  --backend conda \
  --config "$PROJECT_DIR/config/illumina.manual.yml"
```

## Main Illumina settings

| Setting | Typical value | Purpose |
| --- | --- | --- |
| `illumina_samplesheet` | absolute CSV path | Exact FASTQ/sample/status contract |
| `illumina_analysis_type` | `solid_biopsy` | Analysis preset |
| `illumina_caller` | `qdnaseq` | Illumina CNA caller |
| `illumina_binsize_kb` | `100` | Initial qDNAseq bin width |
| `illumina_build_pon` | `false` or `true` | Enable local normal panel |
| `illumina_pon_normal_samples` | comma-separated IDs | Exact normal sample set |
| `illumina_pon_min_normals` | `2` or study-specific value | Minimum controls |
| `illumina_pon_min_mapq` | `37` | Panel read mapping-quality threshold |
| `run_cna_classifier` | `false` | Add native cancer-context reports |
| `force` | `false` | Preserve reusable stages |

## Pre-run checks

```bash
PROJECT_DIR="$PWD/project"
SHEET="$PROJECT_DIR/config/illumina/illumina.samplesheet.csv"

test -s "$SHEET"
sed -n '1,30p' "$SHEET"
gzip -t "$PROJECT_DIR/input/illumina_fastq/Patient_A_R1.fastq.gz"
gzip -t "$PROJECT_DIR/input/illumina_fastq/Patient_A_R2.fastq.gz"
```

Use a new YAML and `outdir` when changing bin size, panel membership, or other scientific settings.
