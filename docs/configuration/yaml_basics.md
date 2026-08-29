# Manual YAML editing

Automatic Setup is the recommended route. Manual flat YAML is useful for unusual filenames, prebuilt samplesheets, custom references, or advanced classifier and refinement settings.

## Flat YAML only

OncoTracer v2 deliberately accepts a flat top-level mapping:

```yaml
mode: illumina
lpwgs_root: /data/study
outdir: /data/study/results
illumina_samplesheet: /data/study/config/illumina.samplesheet.csv
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
```

Nested mappings and lists are rejected. Use comma-separated values where a parameter accepts multiple names.

Incorrect:

```yaml
illumina:
  binsize_kb: 100
```

Correct:

```yaml
illumina_binsize_kb: 100
```

## Value types

| Type | Examples |
| --- | --- |
| Boolean | `true`, `false` |
| Integer | `100`, `500`, `20` |
| Decimal | `0.10`, `0.98` |
| Text | `illumina`, `qdnaseq`, `broad_cancer` |
| Absolute path | `/data/study/input/fastq_pass` |
| Comma-separated names | `barcode01,barcode02` |

Do not place shell variables such as `$PWD` inside the YAML; expand them when creating the file.

## Minimal Illumina YAML

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/config" "$PROJECT_DIR/results"

cat > "$PROJECT_DIR/config/illumina.manual.yml" <<YAML
mode: illumina
lpwgs_root: $PROJECT_DIR
outdir: $PROJECT_DIR/results/illumina
illumina_samplesheet: $PROJECT_DIR/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
YAML

oncotracer run --backend conda \
  --config "$PROJECT_DIR/config/illumina.manual.yml"
```

## Minimal ONT YAML

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/config" "$PROJECT_DIR/results"

cat > "$PROJECT_DIR/config/ont.manual.yml" <<YAML
mode: ont
lpwgs_root: $PROJECT_DIR
outdir: $PROJECT_DIR/results/ont
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

oncotracer run --backend conda \
  --config "$PROJECT_DIR/config/ont.manual.yml"
```

## Illumina normal status

Record `normal` in the fourth column of `illumina_samplesheet` for each normal
sample. No extra YAML fields are needed: every row is analyzed independently,
and normal rows are never pooled into a sample-derived reference.

## Native classifier and pathology

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer
run_gistic: true
gistic_required: false
gistic_min_samples: 2
knowledge_web: false
knowledge_literature_llm: false
knowledge_deep_literature: false

pathology_csv: /data/study/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
```

## Validate before execution

```bash
CONFIG="$PWD/project/config/illumina.manual.yml"

test -s "$CONFIG"
grep -E '^(mode|lpwgs_root|outdir):' "$CONFIG"
oncotracer run --backend conda \
  --config "$CONFIG" \
  --dry-run
```

`--dry-run` prints the native argument arrays without launching the scientific tools.

## Precedence

The YAML supplies analysis settings. CLI options control the execution wrapper:

```bash
oncotracer run \
  --backend docker \
  --threads 8 \
  --config "$PWD/project/config/illumina.manual.yml"
```

`--force` and `--dry-run` may also be supplied on the CLI. The installed backend is used when `--backend` is omitted.
