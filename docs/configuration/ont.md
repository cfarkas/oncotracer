# ONT configuration

Use this route for Oxford Nanopore Technologies FASTQs organized in barcode directories. Native v2 discovers and merges the selected reads, aligns with minimap2, runs the explicitly selected CNA caller, refines CNA boundaries from BAM depth, and creates tables, plots, and summaries. The default liquid-biopsy route uses HMMcopy/ichorCNA; an explicit solid-biopsy route can use qDNAseq.

The liquid-biopsy caller uses the version-selected upstream HD_ULP ichorCNA reference object as a static scientific asset. That caller resource is not created from the cohort, and no submitted `NORMAL` sample is pooled into it.

## Recommended: Automatic Setup

### Arrange barcode FASTQs

Point `--reads-folder` at the parent `fastq_pass` directory:

```text
project/input/fastq_pass/
├── barcode01/
│   ├── reads_001.fastq.gz
│   └── reads_002.fastq.gz
└── barcode02/
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
barcode02,Control_A,NORMAL
CSV
```

`barcode` must match a directory name exactly.

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
ont_barcodes: barcode01
ont_sample_names: Patient_A
ont_normal_folder: /absolute/path/project/input/fastq_pass
ont_normal_barcodes: barcode02
ont_normal_sample_names: Control_A
ont_analysis_type: solid_biopsy
ont_caller: qdnaseq
ont_binsize_kb: 100
ont_min_age_minutes: 0
run_cna_classifier: false
force: false
```

Barcode and sample-name lists are positional. The first barcode maps to the first sample name.
`ont_barcodes`/`ont_sample_names` identify TUMOR samples. When the input table contains NORMAL rows, Automatic Setup writes them separately as `ont_normal_folder`, `ont_normal_barcodes`, and `ont_normal_sample_names`. Native v2 runs qDNAseq for every TUMOR and NORMAL sample independently; it never pools, averages, or subtracts the NORMAL group. Mixed or NORMAL-containing ONT cohorts therefore use `ont_analysis_type: solid_biopsy` and `ont_caller: qdnaseq`. The frozen Nextflow comparator does not support this role-preserving route.


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

## Explicit solid-biopsy qDNAseq route

For a solid-tumor ONT cohort, select qDNAseq explicitly and use a new `outdir` so its caller and reports remain separate from an ichorCNA run:

```yaml
mode: ont
lpwgs_root: /absolute/path/project
outdir: /absolute/path/project/results/ont_solid_qdnaseq
ont_folder: /absolute/path/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Tumor_A,Tumor_B
ont_analysis_type: solid_biopsy
ont_caller: qdnaseq
ont_binsize_kb: 100
ont_min_age_minutes: 0
run_cna_classifier: false
force: false
```

This route reuses the native qDNAseq implementation and its existing scientific settings, passes the long-read BAMs as unpaired data, and writes initial caller output under `01_samurai_ont/qdnaseq/`. It does not combine or overwrite `01_samurai_ont/results/ichorcna/`; retaining a distinct `outdir` also keeps the downstream stage-02 through stage-06 products separate. `ont_caller: qdnaseq` is rejected unless `ont_analysis_type: solid_biopsy` is present.

## Optional POD5 methylation classification

For an ONT run, `--methylation` can run modified-base basecalling and either Sturgeon (`--sturgeon`, CNS-tumor research) or MARLIN (`--marlin`, leukemia research) before the CNA branch. An explicit non-empty POD5 directory is mandatory; OncoTracer never searches for POD5 files:

```bash
cd /path/to/my/analyses_dir/

oncotracer run \
  --backend conda \
  --config "$PWD/project/config/ont.manual.yml" \
  --methylation \
  --sturgeon \
  --pod5-dir /absolute/path/to/pod5_pass \
  --gpu
```

The YAML must also contain explicit, checksum-pinned Dorado/Modkit/classifier resources. If Modkit detects zero usable modified-CpG calls, OncoTracer records `no_cpg_modifications`, skips the methylation classifier, and continues CNA. A CNA failure likewise does not discard a completed methylation result. Read [Optional ONT methylation](methylation.md) before enabling this research branch, especially the Sturgeon license, hg38 model/probe, backend, and GPU limitations.

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
| `ont_folder` | absolute directory | Parent containing selected barcode directories |
| `ont_barcodes` | comma-separated names | Barcode selection |
| `ont_sample_names` | comma-separated names | Biological names in matching order |
| `ont_analysis_type` | `liquid_biopsy` | Analysis preset |
| `ont_caller` | `ichorcna` | `ichorcna`, or `qdnaseq` for an explicit `solid_biopsy` analysis |
| `ont_binsize_kb` | `500` | Initial caller bin width; set it explicitly for qDNAseq |
| `ont_ref` | optional FASTA | Custom reference |
| `ont_min_age_minutes` | `0` | Minimum FASTQ age before use |
| `ont_force_realign` | `false` | Deliberate alignment refresh |
| `--methylation --sturgeon|--marlin --pod5-dir PATH` | optional CLI branch | Explicit POD5 methylation/classifier route; see the dedicated page |
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
