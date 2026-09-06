# Add pathology to copy-number reports

Pathology is optional. It compares the copy-number findings with a supplied
diagnosis; it does not diagnose the sample. Start with a successful [CNA run](../setup.md)
and inspect its QC. For a separate report analysis, use new configuration and
result folders as below. This example uses **two libraries and four FASTQs**.

## 1. Create the sequencing sample table

Work from your analysis directory. Replace these research sample names with
yours. Paste each `cat` block through `CSV`; it replaces the named file if it exists.

```bash
mkdir -p "$PWD/pathology-project/input/fastq"
cat > "$PWD/pathology-project/input/samples.csv" <<'CSV'
sample_name,status
I7738,TUMOR
V480,TUMOR
CSV
```

Place `I7738_R1.fastq.gz`, `I7738_R2.fastq.gz`, `V480_R1.fastq.gz` and
`V480_R2.fastq.gz` in `pathology-project/input/fastq/`. You can instead point
`--reads-folder` below at their existing folder.

## 2. Create the de-identified pathology table

The IDs in the first column must match the sequencing table exactly, including
capitalization. The diagnoses here are examples, not findings from your data.

```bash
cat > "$PWD/pathology-project/input/pathology.csv" <<'CSV'
illumina_sample_id,case_code,final_diagnosis
I7738,Case_07738,"Glioblastoma, IDH-wildtype."
V480,Case_00480,"Diffuse large B-cell lymphoma, NOS."
CSV
```

Quotes protect diagnosis text containing commas. Do not put names, contact
details or other identifying clinical information in shared examples.

## 3. Generate the configuration

```bash
oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/pathology-project/input/fastq" \
  --sample-table "$PWD/pathology-project/input/samples.csv" \
  --config-dir "$PWD/pathology-project/config" \
  --outdir "$PWD/pathology-project/results" \
  --threads 4 \
  --run-cna-classifier \
  --no-pathology-models
```

Open `pathology-project/config/illumina.auto.yml` in a text editor. **Replace**
its existing `pathology_csv: null` line with your absolute pathology path, then
add these three column settings (do not duplicate existing keys):

```yaml
pathology_csv: /absolute/path/pathology-project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
```

`pathology_csv` points to a file, not a directory. Find its absolute path with:

```bash
realpath "$PWD/pathology-project/input/pathology.csv"
```

The generated `cna_classifier_sample_set: broad_cancer` fits this mixed example.
For a study with a defined disease context, select it **before reviewing results**
using `--cna-classifier-sample-set NAME` in the initial `auto` command. Supported
names are listed in the [configuration reference](../configuration_v2.md#optional-native-cna-classifier).
Do not select a context to obtain a desired classification.

Web/LLM enrichment and biomedical models are disabled in this example.
[LLM-assisted reporting](../llm_reports.md) is a separate, optional step.
The [reference options](../reference_indexes.md) also apply to `auto`.

## 4. Check, then run

```bash
oncotracer check --config "$PWD/pathology-project/config/illumina.auto.yml"
oncotracer run --backend conda \
  --config "$PWD/pathology-project/config/illumina.auto.yml"
```

Check must list both sequencing samples. It is not a biological pathology
validation. Review matching status in the pathology outputs after the run.

## 5. Review the report and its evidence

Open these paths below `pathology-project/results/`:

| File | What to inspect |
| --- | --- |
| `06_workflow_summary/workflow_summary.txt` | Overall completion and warnings |
| `03_cna_codification/cna_events.tsv` | Underlying copy-number gains and losses |
| `05_cna_classifier/03_report/cna_classifier_report.html` | Report; open in a browser |
| `05_cna_classifier/07_pathology/pathology_status.txt` | Matching or processing problems |
| `05_cna_classifier/07_pathology/pathology_concordance.tsv` | Compatibility with the supplied diagnosis |

An indeterminate or discordant result can reflect low depth, low tumor DNA,
sample mismatch or biology that copy-number analysis cannot measure. A matching
report is not diagnostic confirmation. Review it alongside the original
pathology, other laboratory findings and expert assessment.

Keep the unedited generated files, your edited YAML, input tables and run records
with any shared result. The generator checksum record describes the original
YAML; your edited copy and the run record document the settings actually used.
