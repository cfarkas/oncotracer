# ONT configuration

Use this page for ONT caller settings and independent normal samples. For a
first run, follow the [multi-barcode example](../auto_params.md#ont-multiple-barcodes-and-fastq-batches).
Each selected barcode becomes one analysis sample; its FASTQ batches are
combined without mixing different samples.

The liquid-biopsy caller uses the version-selected upstream HD_ULP ichorCNA reference object as a static scientific asset. That caller resource is not created from the cohort, and no submitted `NORMAL` sample is pooled into it.

**Terminal / headless versions:** automatic, manual and solid-biopsy examples
below run through the CLI.

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

`barcode` must match a directory name exactly. Paste the entire block through
`CSV`; `cat >` replaces that named file if it exists. Use completed FASTQs,
not files still being written by sequencing.

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

Automatic Setup validates selected barcode directories and compressed FASTQs.
It writes `ont.auto.yml` and `auto_params_manifest.tsv`; it does not start analysis.
Add `--hg38_build PATH` to reuse prepared indexes or `--build_reference` to build
locally. Otherwise run downloads prebuilt indexes. [Genome indexes](../reference_indexes.md).

### Inspect and run

```bash
PROJECT_DIR="$PWD/project"

sed -n '1,200p' "$PROJECT_DIR/config/ont/ont.auto.yml"
oncotracer check --config "$PROJECT_DIR/config/ont/ont.auto.yml"

oncotracer run \
  --backend conda \
  --config "$PROJECT_DIR/config/ont/ont.auto.yml"

cat "$PROJECT_DIR/results/ont/06_workflow_summary/workflow_summary.txt"
```

A generated YAML resembles:

```yaml
mode: ont
lpwgs_root: /absolute/path/project/config/ont/reference
hg38_auto_download: true
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
`ont_barcodes`/`ont_sample_names` identify TUMOR samples. Automatic Setup records
NORMAL rows separately in `ont_normal_folder`, `ont_normal_barcodes`, and
`ont_normal_sample_names`.

A cohort containing NORMAL rows uses `solid_biopsy` and `qdnaseq`, as shown above.
Native v2 analyzes every sample independently; it never pools, averages, or subtracts the NORMAL group.
The frozen Nextflow comparator does not support this route.


## Manual YAML

Use a manual file when selecting a subset of barcodes, reusing prepared hg38
indexes, or applying advanced settings. This example explicitly downloads
prebuilt indexes at run time; set `hg38_auto_download: false` for local index
building or a prepared reference.

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/config" "$PROJECT_DIR/results/manual_ont"

cat > "$PROJECT_DIR/config/ont.manual.yml" <<YAML
mode: ont
lpwgs_root: "$PROJECT_DIR/reference"
hg38_auto_download: true
outdir: "$PROJECT_DIR/results/manual_ont"
ont_folder: "$PROJECT_DIR/input/fastq_pass"
ont_barcodes: barcode01
ont_sample_names: Patient_A
ont_normal_folder: "$PROJECT_DIR/input/fastq_pass"
ont_normal_barcodes: barcode02
ont_normal_sample_names: Control_A
ont_analysis_type: solid_biopsy
ont_caller: qdnaseq
ont_binsize_kb: 100
ont_min_age_minutes: 0
run_cna_classifier: false
force: false
YAML

oncotracer check --config "$PROJECT_DIR/config/ont.manual.yml"
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
lpwgs_root: /absolute/path/project/reference
hg38_auto_download: true
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

Save that YAML as `project/config/ont.solid.yml`, then run from your analysis directory:

```bash
oncotracer check --config "$PWD/project/config/ont.solid.yml"
oncotracer run --backend conda --config "$PWD/project/config/ont.solid.yml"
```

This route reuses the native qDNAseq implementation and its existing scientific settings, passes the long-read BAMs as unpaired data, and writes initial caller output under `01_samurai_ont/qdnaseq/`. It does not combine or overwrite `01_samurai_ont/results/ichorcna/`; retaining a distinct `outdir` also keeps the downstream stage-02 through stage-06 products separate. `ont_caller: qdnaseq` is rejected unless `ont_analysis_type: solid_biopsy` is present.

## Optional POD5 methylation classification

For an ONT run, `--methylation` adds Sturgeon (`--sturgeon`, CNS-tumor research)
or MARLIN (`--marlin`, leukemia research) before CNA. The example below starts
from an explicit POD5 directory and requires local Dorado models. To reuse
modified-base BAMs without basecalling, follow the [BAM route](methylation.md#terminal-example-leukemia-using-existing-bams).

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

## Reuse a prepared hg38 reference

Set the prepared OncoTracer reference parent in the same YAML:

```yaml
lpwgs_root: /absolute/path/shared-reference
hg38_auto_download: false
```

Use the directory accepted by `setup --hg38_build`, as described in the
[reference guide](../reference_indexes.md). The legacy `ont_ref` key is ignored
by the native engine; it does not select an arbitrary FASTA. The native CNA
workflow requires the supported hg38 genome and compatible indexes.

## Completed-file age

For directories receiving live sequencing output, exclude newly written FASTQs:

```yaml
ont_min_age_minutes: 10
```

The default public examples use `0` because the downloaded files are complete.

## Force realignment

The legacy `ont_force_realign` key is ignored by the native engine. To deliberately
refresh the saved analysis, use the supported global flag:

```bash
oncotracer run --backend conda --config "$PWD/project/config/ont.manual.yml" --force
```

`--force` refreshes analysis stages, not only alignment. Ordinary reruns reuse
matching stages; use a new `outdir` for different scientific settings.

## Main ONT settings

| Setting | Typical value | Purpose |
| --- | --- | --- |
| `ont_folder` | absolute directory | Parent containing selected barcode directories |
| `ont_barcodes` | comma-separated names | Barcode selection |
| `ont_sample_names` | comma-separated names | Biological names in matching order |
| `ont_analysis_type` | `liquid_biopsy` | Analysis preset |
| `ont_caller` | `ichorcna` | `ichorcna`, or `qdnaseq` for an explicit `solid_biopsy` analysis |
| `ont_binsize_kb` | `500` | Initial caller bin width; set it explicitly for qDNAseq |
| `lpwgs_root` | prepared hg38 reference parent | Supported reference location; legacy `ont_ref` does not override it |
| `ont_min_age_minutes` | `0` | Minimum FASTQ age before use |
| `force` | `false` | Deliberate refresh of analysis stages, including alignment |
| `--methylation --sturgeon|--marlin --pod5-dir PATH` | optional CLI branch | Explicit POD5 methylation/classifier route; see the dedicated page |
| `run_cna_classifier` | `false` | Add native classifier/reports |

## Pre-run checks

```bash
PROJECT_DIR="$PWD/project"

find "$PROJECT_DIR/input/fastq_pass" -maxdepth 2 -type d -print | sort
find "$PROJECT_DIR/input/fastq_pass" -maxdepth 2 -type f -print | sort | head -30
gzip -t "$PROJECT_DIR/input/fastq_pass/barcode01/reads_001.fastq.gz"
sed -n '1,200p' "$PROJECT_DIR/config/ont/ont.auto.yml"
```

Confirm that every listed barcode exists, contains non-empty FASTQs, and maps to exactly one intended sample name.
