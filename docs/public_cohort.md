# QuickStart 2: three public HCC1143 libraries

QuickStart 2 runs the complete native Illumina workflow for three public HCC1143 libraries. It downloads all six paired-end FASTQs, validates each exact size and MD5 checksum, creates the sample table and YAML automatically, runs the analysis, and verifies the required outputs.

The three sample/run mappings are:

| Sample | Public run |
| --- | --- |
| `HCC1143_DMSO` | `SRR7085656` |
| `HCC1143_BEZ235` | `SRR7085655` |
| `HCC1143_TRAMETINIB` | `SRR7085657` |

## Estimated resources

The complete input is larger than QuickStart 1. Provide enough storage for six FASTQs, hg38, BAMs, qDNAseq outputs, refinement tables, PDFs, and the selected backend. The first uncached Illumina run also creates the hg38 BWA index and should have at least 80 GiB of addressable memory.

## One-command QuickStart

```bash
oncotracer install --conda
oncotracer doctor --backend conda

oncotracer quickstart 2 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart2"
```

A successful command ends with:

```text
QuickStart 2 completed: .../oncotracer-quickstart2
```

## Step 1. Download and validate the public data only

```bash
TEST_ROOT="$PWD/oncotracer-quickstart2"

oncotracer quickstart 2 \
  --test-root "$TEST_ROOT" \
  --download-only
```

The versioned `examples/hcc1143_lpwgs/manifest.tsv` supplies each ENA URL, sample ID, run accession, read end, expected byte count, and MD5. Completed valid FASTQs are reused when the command is repeated.

The preparation creates:

```text
oncotracer-quickstart2/
├── public/
│   └── hcc1143_lpwgs/
│       ├── HCC1143_DMSO_R1.fastq.gz
│       ├── HCC1143_DMSO_R2.fastq.gz
│       ├── HCC1143_BEZ235_R1.fastq.gz
│       ├── HCC1143_BEZ235_R2.fastq.gz
│       ├── HCC1143_TRAMETINIB_R1.fastq.gz
│       ├── HCC1143_TRAMETINIB_R2.fastq.gz
│       └── samples.csv
├── configs/
│   └── hcc1143_lpwgs/
│       ├── auto_params_manifest.tsv
│       ├── illumina.auto.yml
│       └── illumina.samplesheet.csv
└── runs/
```

Inspect the generated files:

```bash
TEST_ROOT="$PWD/oncotracer-quickstart2"

cat "$TEST_ROOT/public/hcc1143_lpwgs/samples.csv"
sed -n '1,160p' \
  "$TEST_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml"
sed -n '1,20p' \
  "$TEST_ROOT/configs/hcc1143_lpwgs/illumina.samplesheet.csv"
cat "$TEST_ROOT/configs/hcc1143_lpwgs/auto_params_manifest.tsv"
```

The sample table contains:

```csv
sample_name,status
HCC1143_DMSO,TUMOR
HCC1143_BEZ235,TUMOR
HCC1143_TRAMETINIB,TUMOR
```

## Step 2. Run the generated YAML

```bash
TEST_ROOT="$PWD/oncotracer-quickstart2"

oncotracer run \
  --backend conda \
  --config "$TEST_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml"
```

The analysis runs alignment, Picard processing, qDNAseq, BAM-supported boundary refinement, CNA codification, cytogenomic notation, cohort/per-sample plots, and workflow summaries.

## Step 3. Verify the required result groups

The complete QuickStart command reuses valid completed stages and performs the bundled output checks:

```bash
TEST_ROOT="$PWD/oncotracer-quickstart2"

oncotracer quickstart 2 \
  --backend conda \
  --test-root "$TEST_ROOT"
```

Review:

```bash
TEST_ROOT="$PWD/oncotracer-quickstart2"
OUTDIR="$TEST_ROOT/runs/hcc1143_lpwgs"

head -n 20 "$OUTDIR/06_workflow_summary/workflow_summary.txt"
sed -n '1,30p' "$OUTDIR/03_cna_codification/cna_events.tsv"
ls -lh "$OUTDIR/04_cna_custom_plots/cna_per_sample_pages.pdf"
cat "$OUTDIR/.oncotracer-native/trace.tsv"
```

Required outputs include:

```text
runs/hcc1143_lpwgs/
├── 01_samurai_illumina/
├── 02_bam_refinement/
├── 03_cna_codification/
│   ├── cna_events.tsv
│   └── cna_cytogenomic_notation.tsv
├── 04_cna_custom_plots/
│   ├── cna_per_sample_pages.pdf
│   └── cohort plots
├── 06_workflow_summary/
│   ├── workflow_summary.txt
│   ├── workflow_summary.json
│   └── native_run_manifest.json
└── .oncotracer-native/
    ├── trace.tsv
    └── state.json
```

## Choose one of four execution methods

### Conda

```bash
oncotracer install --conda
oncotracer quickstart 2 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart2-conda"
```

### Docker

```bash
oncotracer install --docker
oncotracer quickstart 2 \
  --backend docker \
  --test-root "$PWD/oncotracer-quickstart2-docker"
```

### Singularity or Apptainer

```bash
oncotracer install --singularity
oncotracer quickstart 2 \
  --backend singularity \
  --test-root "$PWD/oncotracer-quickstart2-singularity"
```

### Poetry launcher

```bash
./oncotracer install --poetry
poetry run oncotracer quickstart 2 \
  --backend poetry \
  --test-root "$PWD/oncotracer-quickstart2-poetry"
```

## Run the prepared YAML through another backend

The downloaded FASTQs and generated YAML are backend-independent:

```bash
TEST_ROOT="$PWD/oncotracer-quickstart2"

oncotracer run \
  --backend docker \
  --config "$TEST_ROOT/configs/hcc1143_lpwgs/illumina.auto.yml"
```

## Resume

Repeat the same QuickStart or `oncotracer run` command with the same test root and result directory:

```bash
oncotracer quickstart 2 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart2"
```

The native ledger checks the stage command, relevant input metadata, and expected outputs before reuse. Use `--force` only for a deliberate full refresh.

## Continue

- [QuickStart 1](quick_start.md) demonstrates both Illumina and ONT.
- [Automatic Setup](auto_params.md) prepares your own FASTQs.
- [Full Tutorial](full_tutorial.md) processes the versioned 12-library PRJNA754199 manifest.
- [Output Files](outputs.md) explains the result tree.
