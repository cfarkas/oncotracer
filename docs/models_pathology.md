# Models and pathology interpretation

This page explains the optional native stage enabled by `run_cna_classifier: true`. For matched CSV creation and complete commands, begin with [Pathology and classifier configuration](configuration/pathology.md).

## Classifier input

The classifier does not read FASTQs directly. Core analysis first creates:

```text
03_cna_codification/cna_events.tsv
```

The classifier derives CNA burden, recurrent regions, cytobands, gene-region overlaps, and context-associated patterns from those final event/refined-bin products. A pathology CSV is joined by exact sample identifier.

<a id="study-contexts"></a>

## Choose the context before analysis

In the browser, enable **Add CNA interpretation reports**, then enter one exact
value below in **Study context for reports**. The same value is stored as
`cna_classifier_sample_set` in YAML. One context applies to all selected samples
in that project; use `broad_cancer` for a mixed cohort or separate projects for
different established contexts.

This is context **you supply**, used to guide report labels, catalog interpretation
and literature searches when enabled. It does not select a separately validated
tumor classifier or establish a diagnosis. Choose from your study inclusion
criteria and existing pathology, independently of the CNA result.

### All supported study contexts

| Exact value | Meaning | When to choose it |
| --- | --- | --- |
| `broad_cancer` | Broad cancer context; default | Mixed cancer cohorts, exploratory studies, or an unknown tumor context. Summarizes CNA patterns without assigning tissue of origin. |
| `lymphoma` | Lymphoma | A study already defined as lymphoma-focused. |
| `brain_cns` | Brain and central nervous system tumors | A CNS tumor cohort, including glioma or meningioma studies. |
| `breast` | Breast cancer | Breast-tumor or breast-cancer cell-line studies. |
| `pancreas` | Pancreatic and biliary cancers | Pancreatic, pancreatobiliary or cholangiocarcinoma studies. |
| `colorectal` | Colon and rectal cancer | A colorectal tumor cohort. |
| `leukemia` | Leukemia and myelodysplastic syndromes | Leukemia, MDS or related myeloid-neoplasm studies. Use this exact value for ALL; the shorthand `all` means broad cancer in the launcher. |
| `lung` | Lung cancer | Lung-tumor cohorts, including NSCLC or SCLC. |
| `prostate` | Prostate cancer | A prostate-tumor cohort. |
| `ovarian` | Ovarian, fallopian-tube and peritoneal carcinoma | Studies focused on these carcinoma groups. |
| `endometrial` | Endometrial and uterine carcinoma | An endometrial or uterine-carcinoma cohort; use `sarcoma` for uterine sarcoma. |
| `gastric_esophageal` | Gastric and esophageal cancer | Stomach, esophageal or gastroesophageal-junction carcinoma studies. |
| `sarcoma` | Sarcoma and gastrointestinal stromal tumors | Soft-tissue sarcoma, bone sarcoma or GIST studies. |
| `renal` | Renal-cell carcinoma | A kidney-cancer cohort focused on renal-cell carcinoma. |
| `urothelial` | Urothelial carcinoma | Bladder or other urinary-tract urothelial carcinoma studies. |
| `thyroid` | Thyroid carcinoma | A thyroid-carcinoma cohort. |
| `melanoma` | Melanoma | A melanoma cohort. |
| `liver` | Liver cancer, including hepatocellular carcinoma | A liver-cancer cohort; the pancreatic/biliary context above includes cholangiocarcinoma. |
| `head_neck` | Head-and-neck carcinoma | Oral, oropharyngeal, laryngeal or related carcinoma studies. |
| `germ_cell` | Germ-cell tumors | Germ-cell tumor studies, including seminoma and nonseminoma. |
| `myeloma` | Myeloma and plasma-cell neoplasms | A myeloma or plasma-cell-neoplasm cohort. |
| `neuroblastoma` | Neuroblastoma | A neuroblastoma cohort. |
| `neuroendocrine` | Neuroendocrine neoplasms | Neuroendocrine tumor or carcinoma studies. |
| `pediatric_solid` | Pediatric solid tumors | A broad childhood solid-tumor cohort; use a more specific context above when the study supports it. |

An empty value uses `broad_cancer`. Some aliases are accepted, but the exact values
above avoid ambiguous abbreviations. Unrecognized text is retained as a custom
context and uses generic report handling where no dedicated context rules exist;
it does **not** create new classifier support or automatically select broad mode.
Use `broad_cancer` when the biological context is unknown. Context-specific feature
catalogs differ in coverage; selecting a context does not guarantee a subtype call.

### Terminal / headless equivalent

For the same browser choice, set `cna_classifier_sample_set` in your saved YAML
(as in the complete example below), then check and run that configuration:

```bash
CONFIG="$PWD/project/config/illumina.auto.yml"
oncotracer check --config "$CONFIG"
oncotracer run --backend conda --config "$CONFIG"
```

When generating a project from the terminal, the equivalent option is
`--run-cna-classifier --cna-classifier-sample-set broad_cancer` on
`oncotracer setup --non-interactive`; replace `broad_cancer` with the chosen value.
See the [complete terminal setup example](configuration/pathology.md#3-generate-the-configuration)
for input and pathology settings.

## Deterministic initial mode

Edit these values in your existing YAML; do not add a second copy of a key.
Create the [pathology CSV](configuration/pathology.md#2-create-the-de-identified-pathology-table)
first, or leave `pathology_csv: null` for CNA-only reports.

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer

pathology_csv: /absolute/path/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true

run_gistic: true
gistic_required: false
knowledge_web: false
knowledge_literature_llm: false
knowledge_deep_literature: false
knowledge_deep_enable_llm_ranker: false
```

This mode is easiest to reproduce and review. Enable network/model assistance only after the deterministic route succeeds and governance permits it.

## Optional offline catalog drafts

With model weights already cached locally, these settings draft report text from
the built-in CNA catalog without retrieving papers:

```yaml
knowledge_catalog_llm: true
knowledge_web: false
knowledge_literature_llm: false
knowledge_literature_llm_local_files_only: true
pathology_use_biomed_models: false
```

Catalog drafts are labeled separately from literature-supported interpretations
and require review. They do not compare CNA results with pathology; that separate
model step requires a matched pathology table.

## Optional pathology matching models

```yaml
pathology_use_biomed_models: true
pathology_biomed_local_files_only: false
```

These models score compatibility with supplied pathology text; they do not write
the report's literature paragraphs. Attempts are recorded in
`diagnostics/pathology/pathology_model_trials.tsv`. The first run may download model weights.

For the machinery that writes report text, see [LLM-assisted reports](llm_reports.md).

## What CNA data can support

The classifier can summarize:

- broad and focal gains/losses;
- high-level amplifications and deep losses;
- altered-genome burden and aneuploidy;
- recurrent cytobands and cataloged driver regions;
- cohort recurrence when GISTIC2 is enabled and scientifically appropriate.

GISTIC uses modeled markers: a regular grid at `gistic_window_bp` spacing plus
all start/end coordinates from the full and altered-event SEG files. This keeps
refined boundaries and narrow events represented. `Num_Probes` in those GISTIC
files counts the modeled markers within each inclusive interval; it is not the
observed read-bin count. Original CNA tables and `samurai_events.seg` retain their
original counts. This follows GISTIC 2.0.23's support for [pseudo-markers when
observed markers are unavailable](https://broadinstitute.github.io/gistic2/).
The preparation metrics record this approximation.

LP-WGS read-depth CNA analysis does not reliably determine:

- SNVs or small insertions/deletions;
- balanced translocations or most gene fusions;
- methylation class or RNA/protein expression;
- copy-neutral loss of heterozygosity;
- clonality or biallelic inactivation without orthogonal evidence.

## Read the outputs

```bash
OUT="$PWD/project/results/illumina_pathology/05_cna_classifier"

sed -n '1,12p' "$OUT/diagnostics/prepared/sample_cna_summary.tsv"
sed -n '1,12p' "$OUT/tables/classification/cna_patient_classification.tsv"
sed -n '1,12p' "$OUT/evidence/sample_knowledge_summary.tsv"
sed -n '1,12p' "$OUT/diagnostics/pathology/pathology_concordance.tsv"
sed -n '1,120p' "$OUT/diagnostics/pathology/pathology_status.txt"
ls -lh "$OUT/cohort_report.html"
```

| Location | Contents | Status |
| --- | --- | --- |
| `diagnostics/prepared/` | Event and feature tables | Derived classifier input |
| `tables/classification/` | Context scores and classes | Research interpretation |
| `final_report.html`, `final_report.pdf` | Complete knowledge report | Matched browser/PDF presentation |
| `clinician_report.html`, `clinician_report.pdf` | Short summary | Research interpretation |
| `cohort_report.html`, `figures/` | Cohort overview and grouped plots | Presentation layer |
| `diagnostics/gistic/`, `diagnostics/gistic_parsed/` | Optional recurrence analysis | Cohort-level research output |
| `evidence/` | Literature evidence, metrics and model trials | Requires expert verification |
| `diagnostics/pathology/` | Matching, concordance, status, model trials | Compatibility assessment |

With multiple samples, individual reports are in `samples/<sample>/` and the root
PDFs combine them. For a single sample, only the root report is kept. See
[LLM-assisted reports](llm_reports.md) for the report audit and migration command.

## Interpret concordance carefully

See [CNA evidence and diagnostic uncertainty](cna_evidence_assessment.md) for
supporting-segment counts, overlapping catalog regions, research scores and the
limits of tissue-origin inference.

Concordance asks whether CNA features are compatible with the supplied diagnosis in the selected context. A disagreement or indeterminate result may reflect low tumor fraction, low sequencing depth, CNA-quiet biology, sample mismatch, an incomplete catalog, or alterations that LP-WGS cannot detect.

Review sample identity, coverage, segmentation, event tables, morphology, immunohistochemistry, cytogenetics, methylation/fusion testing, and validated molecular assays before drawing conclusions.

## Reproducibility and privacy

- Record OncoTracer version/commit, YAML, context, backend, and image/prefix identity.
- Preserve de-identified pathology-column mappings and matching results.
- Do not include names, national identifiers, birth dates, or unnecessary clinical text.
- Enable network retrieval only when approved.
- Manually verify literature references and generated summaries.

Classifier scores and pathology compatibility are research outputs, not diagnostic confirmation or a medical-device result.

The optional report language model defaults to the pinned
[Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)
revision `cdbee75f17c01a7cc42f958dc650907174af0554`. It runs on CPU;
allow at least **24 GiB available RAM** and about **8 GB** for downloaded weights.
The model produces reviewable drafts; source-format checks do not establish
biological correctness. The local catalog option makes no publication searches.
