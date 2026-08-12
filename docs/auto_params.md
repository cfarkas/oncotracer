# Automatic Setup for your FASTQs

Automatic Setup is the recommended way to create a native OncoTracer YAML. It validates the input names, creates an exact samplesheet or barcode mapping, writes an audit manifest, and stops. It does not start the scientific analysis.

Use:

```text
oncotracer auto -> inspect generated files -> oncotracer run
```

## Illumina: paired-end or single-end FASTQs

Place all FASTQs for one analysis in one directory. Supported paired-end names include patterns such as:

```text
TUMOR_01_R1.fastq.gz
TUMOR_01_R2.fastq.gz
TUMOR_02_1.fastq.gz
TUMOR_02_2.fastq.gz
```

Single-end files may use:

```text
SAMPLE_01.fastq.gz
SAMPLE_02.fastq.gz
```

Do not mix single-end and paired-end samples in one generated configuration.

Create a small sample table:

```csv
sample_name,status
TUMOR_01,TUMOR
TUMOR_02,TUMOR
CONTROL_01,NORMAL
CONTROL_02,NORMAL
```

The following block is copy/paste ready and preserves the caller's working directory:

```bash
mkdir -p "$PWD/project/input/fastq" \
  "$PWD/project/config" \
  "$PWD/project/results"

cat > "$PWD/project/input/samples.csv" <<'CSV'
sample_name,status
TUMOR_01,TUMOR
TUMOR_02,TUMOR
CONTROL_01,NORMAL
CONTROL_02,NORMAL
CSV

oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/project/input/fastq" \
  --sample-table "$PWD/project/input/samples.csv" \
  --config-dir "$PWD/project/config" \
  --outdir "$PWD/project/results"

sed -n '1,160p' "$PWD/project/config/illumina.auto.yml"
sed -n '1,40p' "$PWD/project/config/illumina.samplesheet.csv"
cat "$PWD/project/config/auto_params_manifest.tsv"

oncotracer run --backend conda \
  --config "$PWD/project/config/illumina.auto.yml"
```

Automatic Setup writes absolute paths so that Docker and Singularity/Apptainer can mount the project consistently.

## How NORMAL samples are handled

Normal rows are ordinary, independently analyzed qDNAseq samples. Automatic
Setup preserves their `normal` status in `illumina.samplesheet.csv`.
It does not pool them, construct a sample-derived reference, or apply their
signal to the tumor rows. NORMAL-only and mixed cohorts are valid; every row is
processed as its own sample.

Inspect the generated samplesheet and manifest to confirm the intended role and
identity of every sample. The generated YAML contains no local-panel settings.

## What Automatic Setup creates for Illumina

```text
project/config/
├── auto_params_manifest.tsv
├── illumina.auto.yml
└── illumina.samplesheet.csv
```

The four-column samplesheet contains:

| Column | Meaning |
| --- | --- |
| `sample` | Unique analysis sample ID |
| `fastq_1` | Absolute single-end or R1 FASTQ path |
| `fastq_2` | Absolute R2 path, or empty for all rows in a single-end run |
| `status` | `tumor` or `normal` |

The manifest records discovered files, sample statuses, generated paths, and small-file hashes.

## ONT: barcode directories

Use the standard MinKNOW-style hierarchy:

```text
project/input/fastq_pass/
├── barcode01/
│   ├── reads_001.fastq.gz
│   └── reads_002.fastq.gz
├── barcode02/
│   └── reads_001.fastq.gz
└── samples.csv
```

FASTQs may end in `.fastq`, `.fq`, `.fastq.gz`, or `.fq.gz` and should be directly inside each selected barcode directory.

Create the mapping table:

```csv
barcode,sample_name,status
barcode01,PATIENT_A,TUMOR
barcode02,PATIENT_B,TUMOR
```

Generate and run:

```bash
mkdir -p "$PWD/project/input/fastq_pass" \
  "$PWD/project/config/ont" \
  "$PWD/project/results/ont"

cat > "$PWD/project/input/fastq_pass/samples.csv" <<'CSV'
barcode,sample_name,status
barcode01,PATIENT_A,TUMOR
barcode02,PATIENT_B,TUMOR
CSV

oncotracer auto \
  --mode ont \
  --reads-folder "$PWD/project/input/fastq_pass" \
  --sample-table "$PWD/project/input/fastq_pass/samples.csv" \
  --config-dir "$PWD/project/config/ont" \
  --outdir "$PWD/project/results/ont"

sed -n '1,160p' "$PWD/project/config/ont/ont.auto.yml"

oncotracer run --backend conda \
  --config "$PWD/project/config/ont/ont.auto.yml"
```

The generated ONT YAML records positional barcode and sample lists:

```yaml
ont_barcodes: barcode01,barcode02
ont_sample_names: PATIENT_A,PATIENT_B
```

The list lengths and order must remain identical.

## Enable the optional native classifier

Add `--run-cna-classifier` during generation:

```bash
oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/project/input/fastq" \
  --sample-table "$PWD/project/input/samples.csv" \
  --config-dir "$PWD/project/config" \
  --outdir "$PWD/project/results" \
  --run-cna-classifier
```

Then inspect or extend the flat YAML:

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer
run_gistic: true
gistic_required: false
knowledge_web: false
```

See [Pathology and classifier](configuration/pathology.md).

## Run the generated YAML with any backend

### Conda

```bash
oncotracer install --conda
oncotracer run --backend conda \
  --config "$PWD/project/config/illumina.auto.yml"
```

### Docker

```bash
oncotracer install --docker
oncotracer run --backend docker \
  --config "$PWD/project/config/illumina.auto.yml"
```

### Singularity or Apptainer

```bash
oncotracer install --singularity
oncotracer run --backend singularity \
  --config "$PWD/project/config/illumina.auto.yml"
```

### Poetry

```bash
./oncotracer install --poetry
poetry run oncotracer run --backend poetry \
  --config "$PWD/project/config/illumina.auto.yml"
```

The YAML is backend-independent. Do not generate separate YAMLs for Conda and containers.

## Inspect before starting the analysis

Confirm:

```bash
CONFIG="$PWD/project/config/illumina.auto.yml"

test -s "$CONFIG"
grep -E '^(mode|lpwgs_root|outdir|illumina_samplesheet):' "$CONFIG"
gzip -t "$PWD/project/input/fastq/TUMOR_01_R1.fastq.gz"
gzip -t "$PWD/project/input/fastq/TUMOR_01_R2.fastq.gz"
```

Also check that:

- sample names are unique;
- R1 and R2 are paired correctly;
- every configured path is absolute;
- the result directory is appropriate for this study;
- every submitted tumor/normal status matches the intended sample identity;
- pathology identifiers, when supplied, match sequencing IDs exactly.

## Dry-run and resume

Use `--dry-run` to inspect the native commands without executing scientific tools:

```bash
oncotracer run --backend conda \
  --config "$PWD/project/config/illumina.auto.yml" \
  --dry-run
```

Repeat the normal run command to resume. The native stage ledger reuses content-matched completed work automatically.
