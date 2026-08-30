<a id="quick-start"></a>

# QuickStart 1: one Illumina and one ONT sample

QuickStart 1 is a complete native analysis of one public paired-end Illumina library and one public ONT library. It downloads approximately 225 MB of reads, validates exact byte counts and MD5 values, creates one YAML per sequencing route, runs both analyses, and verifies the required outputs.

The one-command route is recommended. The detailed walkthrough below exposes the same preparation, inspection, separate execution, resume, and output-verification steps presented in the original GitHub Pages tutorial.

[![Six-step OncoTracer QuickStart flow](assets/tutorial/quickstart_flow.svg)](assets/tutorial/quickstart_flow.svg)

## Estimated time and resources

The example FASTQs are small. The first uncached Illumina analysis still downloads hg38 and creates the BWA index, which can take tens of minutes and requires substantial addressable memory. Provide at least 80 GiB of RAM and enough free storage for the reference, BAMs, five Conda environments or container layers, and results.

!!! important "Choose the analyses directory first"
    Every copy/paste block below that uses `$PWD` begins with `cd /path/to/my/analyses_dir/`. Replace that placeholder with an existing directory where you want OncoTracer to create the QuickStart downloads, YAML files, reference cache, BAMs, and results. The installed `oncotracer` executable does not require a repository checkout.

## One-command QuickStart

```bash
cd /path/to/my/analyses_dir/

oncotracer install --conda
oncotracer doctor --backend conda

oncotracer quickstart 1 --test-root "$PWD/oncotracer-quickstart1" --download-only
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/configs/illumina.quickstart.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/configs/ont.quickstart.yml"
```

A successful command ends with:

```text
SUCCESS: both QuickStart workflows completed and required outputs were found.
QuickStart 1 completed: .../oncotracer-quickstart1
```

## Public samples

### Illumina

```csv
sample_name,status
ERR12341627,TUMOR
```

The command downloads:

```text
ERR12341627_1.fastq.gz
ERR12341627_2.fastq.gz
```

### ONT

```csv
barcode,sample_name,status
barcode01,DRR165691,TUMOR
```

The command downloads the public ONT FASTQ beneath `fastq_pass/barcode01/`.

## Step 1. Prepare and verify one backend

Conda:

```bash
oncotracer install --conda
oncotracer doctor --backend conda
```

Docker:

```bash
oncotracer install --docker
oncotracer doctor --backend docker
```

Singularity or Apptainer:

```bash
oncotracer install --singularity
oncotracer doctor --backend singularity
```

Poetry development route:

```bash
cd /path/to/my/oncotracer_source/
./oncotracer install --poetry \
  --prefix /path/to/my/oncotracer-v2-dev-envs
/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer doctor \
  --backend poetry
```

Use one route for a normal analysis.

## Step 2. Download, validate, and create both YAML files

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

oncotracer quickstart 1 \
  --test-root "$TEST_ROOT" \
  --download-only
```

This step checks file size, MD5, and gzip integrity. It does not align reads or call CNAs.

The command creates:

```text
oncotracer-quickstart1/
├── configs/
│   ├── illumina.quickstart.yml
│   └── ont.quickstart.yml
├── public/
│   ├── illumina_ERR12341627/
│   │   ├── ERR12341627_1.fastq.gz
│   │   ├── ERR12341627_2.fastq.gz
│   │   └── illumina.samplesheet.csv
│   └── ont_DRR165691/
│       └── fastq_pass/
│           └── barcode01/
│               └── DRR165691_1.fastq.gz
└── runs/
```

Inspect the generated run plans:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

ls -1 "$TEST_ROOT/configs"
sed -n '1,160p' "$TEST_ROOT/configs/illumina.quickstart.yml"
sed -n '1,160p' "$TEST_ROOT/configs/ont.quickstart.yml"
sed -n '1,20p' "$TEST_ROOT/public/illumina_ERR12341627/illumina.samplesheet.csv"
```

A YAML is a saved native run plan. It contains input paths, output paths, caller settings, and optional analysis flags; it does not contain sequencing reads or results.

## Step 3. Run the Illumina analysis separately

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

oncotracer run \
  --backend conda \
  --config "$TEST_ROOT/configs/illumina.quickstart.yml"
```

Wait for completion before starting ONT when workstation memory or storage bandwidth is limited.

Inspect the summary:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

head -n 12 "$TEST_ROOT/runs/illumina/06_workflow_summary/workflow_summary.txt"
grep -E '^(mode|dataset|engine|nextflow_used)=' \
  "$TEST_ROOT/runs/illumina/06_workflow_summary/workflow_summary.txt"
```

The summary should identify the Illumina qDNAseq analysis, `engine=native`, and `nextflow_used=false`.

## Step 4. Run the ONT analysis separately

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

oncotracer run \
  --backend conda \
  --config "$TEST_ROOT/configs/ont.quickstart.yml"
```

Inspect the summary:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

head -n 12 "$TEST_ROOT/runs/ont/06_workflow_summary/workflow_summary.txt"
grep -E '^(mode|dataset|engine|nextflow_used)=' \
  "$TEST_ROOT/runs/ont/06_workflow_summary/workflow_summary.txt"
```

The summary should identify the ONT ichorCNA analysis, `engine=native`, and `nextflow_used=false`.

## Step 5. Verify both analyses

Run the complete QuickStart command against the same root. Content-matched completed stages are reused, and the bundled verifier checks both output trees:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

oncotracer quickstart 1 --test-root "$TEST_ROOT" --download-only
oncotracer run --backend conda \
  --config "$TEST_ROOT/configs/illumina.quickstart.yml"
oncotracer run --backend conda \
  --config "$TEST_ROOT/configs/ont.quickstart.yml"
```

The verifier checks, among other files:

- `06_workflow_summary/workflow_summary.txt`;
- `03_cna_codification/cna_events.tsv`;
- `03_cna_codification/cna_cytogenomic_notation.tsv`;
- `04_cna_custom_plots/cna_per_sample_pages.pdf`;
- cohort plot PDFs;
- `.oncotracer-native/trace.tsv`;
- `native_run_manifest.json`.

Important result folders are:

```text
oncotracer-quickstart1/runs/illumina/
├── 01_samurai_illumina/
├── 02_bam_refinement/
├── 03_cna_codification/
├── 04_cna_custom_plots/
└── 06_workflow_summary/

oncotracer-quickstart1/runs/ont/
├── 01_samurai_ont/
├── 02_bam_refinement/
├── 03_cna_codification/
├── 04_cna_custom_plots/
└── 06_workflow_summary/
```

## Choose one of four execution methods

The following commands run the complete example. Choose one backend.

### Conda

```bash
cd /path/to/my/analyses_dir/

oncotracer install --conda
oncotracer quickstart 1 --test-root "$PWD/oncotracer-quickstart1-conda" --download-only
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1-conda/configs/illumina.quickstart.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1-conda/configs/ont.quickstart.yml"
```

### Docker

```bash
cd /path/to/my/analyses_dir/

oncotracer install --docker
oncotracer quickstart 1 --test-root "$PWD/oncotracer-quickstart1-docker" --download-only
oncotracer run --backend docker \
  --config "$PWD/oncotracer-quickstart1-docker/configs/illumina.quickstart.yml"
oncotracer run --backend docker \
  --config "$PWD/oncotracer-quickstart1-docker/configs/ont.quickstart.yml"
```

### Singularity or Apptainer

```bash
cd /path/to/my/analyses_dir/

oncotracer install --singularity
oncotracer quickstart 1 --test-root "$PWD/oncotracer-quickstart1-singularity" --download-only
oncotracer run --backend singularity \
  --config "$PWD/oncotracer-quickstart1-singularity/configs/illumina.quickstart.yml"
oncotracer run --backend singularity \
  --config "$PWD/oncotracer-quickstart1-singularity/configs/ont.quickstart.yml"
```

### Poetry launcher

Poetry is a source-development route. Keep the source checkout separate from the analysis output:

```bash
cd /path/to/my/oncotracer_source/

./oncotracer install --poetry \
  --prefix /path/to/my/oncotracer-v2-dev-envs
/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer quickstart 1 --test-root /path/to/my/analyses_dir/oncotracer-quickstart1-poetry --download-only
/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer run --backend poetry \
  --config /path/to/my/analyses_dir/oncotracer-quickstart1-poetry/configs/illumina.quickstart.yml
/path/to/my/oncotracer-v2-dev-envs/poetry-runtime/bin/oncotracer run --backend poetry \
  --config /path/to/my/analyses_dir/oncotracer-quickstart1-poetry/configs/ont.quickstart.yml
```

## Run generated YAMLs through another backend

Preparation is backend-independent. For example, after `--download-only`, run the same Illumina and ONT YAML files through Docker:

```bash
cd /path/to/my/analyses_dir/
TEST_ROOT="$PWD/oncotracer-quickstart1"

oncotracer run \
  --backend docker \
  --config "$TEST_ROOT/configs/illumina.quickstart.yml"

oncotracer run \
  --backend docker \
  --config "$TEST_ROOT/configs/ont.quickstart.yml"
```

## Resume

Repeat the same command with the same `--test-root` or YAML. The native stage ledger reuses stages whose command, relevant input metadata, and outputs still match.

```bash
cd /path/to/my/analyses_dir/

oncotracer quickstart 1 --test-root "$PWD/oncotracer-quickstart1" --download-only
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/configs/illumina.quickstart.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/configs/ont.quickstart.yml"
```

Use `--force` only when deliberately invalidating reusable native stages.

## Continue from here

- [Automatic Setup](auto_params.md) generates a YAML for your own Illumina or ONT FASTQs.
- [QuickStart 2](public_cohort.md) runs all three public HCC1143 libraries.
- [Full Tutorial](full_tutorial.md) runs the complete versioned 12-library PRJNA754199 manifest.
- [Results Gallery](gallery.md) explains representative profiles, tables, and reports.
