# Choose how to configure a run

Most analyses need one flat YAML file. Use this page to choose the shortest native v2 route.

## Which route should I choose?

| Goal | Start here | Result |
| --- | --- | --- |
| Verify installation with Illumina and ONT | [QuickStart 1](quick_start.md) | Downloads, runs, and verifies two public examples |
| Run the three-library HCC1143 example | [QuickStart 2](public_cohort.md) | Downloads and analyzes six public FASTQs |
| Run the complete public archive tutorial | [Full Tutorial](full_tutorial.md) | Processes the 12-run PRJNA754199 manifest |
| Configure a standard FASTQ folder | [Automatic Setup](auto_params.md) | Creates YAML, manifest, and Illumina samplesheet |
| Run six tumors and four normals independently | [Mock cohort](six_tumor_four_normal.md) | Keeps all ten samples in one native qDNAseq run without creating a local panel |
| Write an Illumina YAML manually | [Illumina setup](configuration/illumina.md) | Uses an existing samplesheet |
| Write an ONT YAML manually | [ONT setup](configuration/ont.md) | Uses explicit barcode/sample lists |
| Add pathology or classifier settings | [Pathology and classifier](configuration/pathology.md) | Adds native classifier, GISTIC2, and reports |
| Tune boundary refinement | [Advanced refinement](configuration/refinement.md) | Changes justified non-default thresholds |
| Look up a field | [Parameter reference](configuration/parameter_reference.md) | Documents CLI and YAML settings |

For standard Illumina or ONT layouts, use Automatic Setup. Manual YAML is the second option for unusual filenames or justified non-default settings.

## One flat YAML controls one analysis

```yaml
mode: illumina
lpwgs_root: /absolute/path/project
outdir: /absolute/path/project/results
illumina_samplesheet: /absolute/path/project/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
force: false
```

The YAML contains paths and analysis settings; it does not contain sequencing reads. Nested YAML is not supported.

## Recommended route: Automatic Setup

```bash
PROJECT_DIR="$PWD/project"

oncotracer auto \
  --mode illumina \
  --reads-folder "$PROJECT_DIR/input/fastq" \
  --sample-table "$PROJECT_DIR/input/samples.csv" \
  --config-dir "$PROJECT_DIR/config" \
  --outdir "$PROJECT_DIR/results"

oncotracer run \
  --backend conda \
  --config "$PROJECT_DIR/config/illumina.auto.yml"
```

For ONT, use `--mode ont` and analyze `ont.auto.yml`.

## Second option: create a manual YAML

```bash
PROJECT_DIR="$PWD/project"
mkdir -p "$PROJECT_DIR/config" "$PROJECT_DIR/results"

cat > "$PROJECT_DIR/config/illumina.manual.yml" <<YAML
mode: illumina
lpwgs_root: $PROJECT_DIR
outdir: $PROJECT_DIR/results
illumina_samplesheet: $PROJECT_DIR/config/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
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

`--dry-run` validates the native route and prints argument arrays without running the scientific tools.

## Choose one execution backend

The YAML is backend-independent:

```bash
CONFIG="$PWD/project/config/illumina.auto.yml"

oncotracer run --backend conda --config "$CONFIG"
# or:
oncotracer run --backend docker --config "$CONFIG"
# or:
oncotracer run --backend singularity --config "$CONFIG"
# or, in a Poetry development checkout:
poetry run oncotracer run --backend poetry --config "$CONFIG"
```

Prepare the selected backend first with `oncotracer install`.

## Common settings

| Key | Purpose |
| --- | --- |
| `mode` | `illumina` or `ont` |
| `lpwgs_root` | Common absolute project/reference root |
| `outdir` | Native result directory |
| `force` | Scientific refresh request; keep `false` initially |
| `run_cna_classifier` | Adds native classifier/report stage |
| `run_gistic` | Adds optional cohort recurrence analysis when classifier is enabled |
| `knowledge_web` | Enables or disables web enrichment; use `false` for deterministic offline runs |

## Illumina normal-sample rule

Every tumor and normal samplesheet row is analyzed independently. The status is
preserved in the generated contract; normal rows are not pooled or applied to
other samples as a local reference.

## Settings to leave unchanged initially

Keep the generated caller, analysis type, bin size, and refinement defaults for the first analysis. Use a new YAML and a new `outdir` for a scientifically different configuration so the original results and native stage records remain separate.
