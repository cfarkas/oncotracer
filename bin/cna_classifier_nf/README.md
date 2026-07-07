# cna_classifier_nf

Cancer-agnostic Nextflow pipeline for CNA-pattern classification from SAMURAI/QDNAseq-style low-pass WGS CNA codification outputs.

The package was renamed from `cna_lymphoma_classifier_nf` to `cna_classifier_nf`. The original CNA/GISTIC/classification logic is preserved, but the reporting and interpretation layer is now pan-cancer and controlled by a single context flag:

```bash
--sample_set <context>
```

`--sample_set` controls the cancer/sample-set context used in the reports and the probable CNA-pattern classifier. Cancer-specific flags such as `--breast_samples`, `--brain_samples`, `--colon_samples`, etc. are no longer used. Sample inclusion is controlled with `--samples`, or by the built-in lymphoma preset.

## Supported sample-set contexts

Common values:

```text
pan_cancer
lymphoma
brain_cns
breast
pancreas
colorectal
leukemia
lung
prostate
ovarian
endometrial
gastric_esophageal
sarcoma
renal
urothelial
thyroid
melanoma
liver
head_neck
germ_cell
myeloma
neuroblastoma
neuroendocrine
pediatric_solid
```

Aliases are accepted. Examples: `colon`, `crc`, and `rectal` become `colorectal`; `brain`, `cns`, `glioma`, and `meningioma` become `brain_cns`; `gastric`, `stomach`, `esophageal`, and `GEJ` become `gastric_esophageal`; `AML`, `MDS`, and `myeloid` become `leukemia`.

## Sample filtering

Preferred explicit sample filtering:

```bash
--sample_set breast \
--samples "$BREAST_SAMPLES"
```

Compact syntax, using only `--sample_set`:

```bash
--sample_set 'breast:SAMPLE_A,SAMPLE_B,SAMPLE_C'
```

Built-in lymphoma sample subset:

```bash
--sample_set lymphoma
```

This automatically selects:

```text
V480,Y2119,U4333,O4789,E4904,X4999,A5465,B5924,K6537,A6566,S6922,Q7164,L7395,N7591,B8017,E9211,M9702,C10174,G11079,P11670,R13729
```

To run all samples in the input folder with broad cancer context, omit `--samples` and use:

```bash
--sample_set broad_cancer
```

`pan_cancer`, `all`, and `all_cancers` are accepted as aliases for `broad_cancer`.



## v8 context restriction and literature interpretation

When `--sample_set lymphoma` is used, the default catalog is now `assets/lymphoma_cna_regions.tsv`, and probable classifications are restricted to lymphoma-context labels. This prevents broad-cancer labels such as CNS/glioma, HER2/ERBB2 carcinoma, germ-cell, breast, colon, or pancreatic patterns from being reported during a lymphoma-only run. Broad pan-cancer exploration remains available with `--sample_set broad_cancer`.

The knowledge layer still supports public literature fallback. With `--knowledge_web true`, the pipeline queries public literature metadata/abstracts for the detected context-appropriate CNA regions. When `--knowledge_literature_llm true`, it attempts the configured local Hugging Face text-generation/summarization models and records which model succeeded in `06_knowledge/knowledge_llm_trials.tsv`. If no model is available or the server is offline, deterministic curated text is used so the interpretation fields are not empty.

Recommended lymphoma run:

```bash
nextflow run ./cna_classifier_nf/main.nf \
  -profile conda \
  -resume \
  --input /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina \
  --sample_set lymphoma \
  --pathology /media/server/STORAGE/LPWGS_2025/complete_biopsy_database_sanitized.csv \
  --pathology_sample_col illumina_sample_id \
  --outdir /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina/cna_classifier_nf_results \
  --gistic_exe auto \
  --gistic_refgene auto \
  --gistic_required false \
  --knowledge_web true \
  --run_pdf_reports true
```

## Basic run

```bash
cd /media/server/STORAGE/LPWGS_2025

nextflow run ./cna_classifier_nf/main.nf \
  -profile conda \
  -resume \
  --input /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina \
  --sample_set pan_cancer \
  --outdir /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina/cna_classifier_nf_results \
  --gistic_exe auto \
  --gistic_refgene auto \
  --gistic_required false \
  --knowledge_web true \
  --run_pdf_reports true
```

## Lymphoma-only run

```bash
nextflow run ./cna_classifier_nf/main.nf \
  -profile conda \
  -resume \
  --input /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina \
  --sample_set lymphoma \
  --pathology /media/server/STORAGE/LPWGS_2025/complete_biopsy_database_sanitized.csv \
  --pathology_sample_col illumina_sample_id \
  --outdir /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina/cna_classifier_nf_results \
  --gistic_exe auto \
  --gistic_refgene auto \
  --gistic_required false \
  --knowledge_web true \
  --run_pdf_reports true
```

## Breast example

```bash
BREAST_SAMPLES="BR001,BR002,BR003"

nextflow run ./cna_classifier_nf/main.nf \
  -profile conda \
  -resume \
  --input /path/to/cna_folder \
  --sample_set breast \
  --samples "$BREAST_SAMPLES" \
  --pathology /path/to/pathology.csv \
  --pathology_sample_col illumina_sample_id \
  --outdir /path/to/breast_cna_classifier_results
```

Equivalent compact form:

```bash
nextflow run ./cna_classifier_nf/main.nf \
  -profile conda \
  -resume \
  --input /path/to/cna_folder \
  --sample_set "breast:BR001,BR002,BR003" \
  --outdir /path/to/breast_cna_classifier_results
```

## Input files

The main input can be a single `cna_events.tsv` file or a folder containing one or more CNA event files. The expected event table follows the SAMURAI codification format:

```text
sample, state, chrom, start, end, size_mb, cytoband, n_bins,
mean_log2, estimated_total_copy_number, copy_code, cna_shorthand
```

Optional notation table:

```bash
--cna_notation /path/to/cna_cytogenomic_notation.tsv
```

Optional pathology table:

```bash
--pathology /path/to/pathology.csv \
--pathology_sample_col illumina_sample_id
```

Supported pathology formats are CSV, TSV, XLS and XLSX. If pathology is provided, the report adds a pathology agreement assessment at the beginning of each HTML/PDF sample report. If pathology is absent, the report still gives a probable CNA-based classification.

## Main outputs

```text
01_prepared/
02_classification/
03_report/
04_gistic2/
05_gistic2_parsed/
06_knowledge/
07_pathology/
```

Open:

```bash
firefox cna_classifier_nf_results/03_report/cna_classifier_report.html
firefox cna_classifier_nf_results/03_report/pdf_reports/index.html
```

Important tables:

```text
02_classification/cna_patient_classification.tsv
06_knowledge/sample_knowledge_summary.tsv
07_pathology/pathology_concordance.tsv
03_report/pdf_reports/pdf_html_report_index.tsv
```

## What low-pass WGS CNA analysis can do

Low-pass WGS with read-depth segmentation can screen the whole genome for broad and focal copy-number gains, losses, deep losses, and high-level amplifications; estimate CNA burden, altered genome size, chromosomal/arm-level complexity, and aneuploidy; flag recurrent pan-cancer CNA regions; and support cohort recurrence analysis with GISTIC2 when enough samples are available.

It cannot reliably detect SNVs, indels, balanced translocations, gene fusions, methylation class, gene/protein expression, clonality, copy-neutral LOH, or biallelic inactivation without orthogonal evidence. Final interpretation must be integrated with pathology, IHC, tumor purity, sequencing depth, and clinical context.

## Evidence-tier labels in reports

The report uses research-reporting tiers, not AMP/ASCO/CAP clinical actionability tiers:

```text
driver-CNA
  A canonical/recurrent copy-number region in the built-in pan-cancer catalog that can support a biologically meaningful CNA pattern.

supportive-CNA
  Compatible but weaker copy-number evidence that supports biological context rather than a subtype call.

CNA-context
  Broad information such as CNA burden, aneuploidy, arm-level change, chromosomal instability, or complexity.

driver-CNA/high-risk-context
  CNA touches a high-risk axis such as TP53-region loss, but does not prove mutation or biallelic inactivation.

driver-CNA/actionability-context
  CNA touches a locus that can be clinically or biologically relevant in some diseases, such as ERBB2/HER2, EGFR, MDM2/CDK4, KIT/PDGFRA/KDR, MET, FGFR2, CCNE1, MYCN, AR, or JAK2/PD-L1/PD-L2. Clinical actionability requires orthogonal confirmation and tumor-type context.

context-dependent
  The same CNA can mean different things in breast, pancreatic, colorectal, leukemia, lymphoma, CNS, sarcoma, prostate, ovarian, renal, gastric, lung, and other tumors.
```

## Probability and pathology agreement

The local agreement/probable-classification score is a transparent token/CNA-pattern score. A true calibrated probability is only produced if a labelled calibration table is supplied:

```bash
--score_calibration_table /path/to/calibration_table.csv \
--score_calibration_score_col agreement_score \
--score_calibration_label_col agreement_true
```

Without such labels, the probability field is explicitly marked as an uncalibrated sigmoid-derived probability-like estimate.

## GISTIC2

GISTIC2 is included as a built-in optional process. By default:

```text
run_gistic = true
gistic_required = false
```

So the pipeline continues if GISTIC2 or its hg38 refgene cannot be resolved. For strict behavior:

```bash
--gistic_required true
```



## v9: deep CNA literature scraping and clinician driver summaries

This release keeps the CNA/GISTIC/classification logic intact and extends the downstream interpretation layer.

New outputs under `03_report/`:

```text
clinician_reports/
  index.html
  clinician_report_index.tsv
  all_sample_clinician_driver_summaries.pdf
  <sample>_clinical_driver_summary.html
  <sample>_clinical_driver_summary.pdf
```

New outputs under `06_knowledge/`:

```text
sample_literature.tsv
sample_literature_summary.tsv
knowledge_literature_ranker_trials.tsv
```

The literature layer now queries PubMed/Europe-PMC-style metadata for CNA features detected in each sample, ranks candidate papers by citation count, CNA/gene/context relevance, abstract availability, recency and review/classification signals, and can optionally score candidate papers with local Hugging Face text-generation/summarization models. If Hugging Face models cannot be downloaded or run, the pipeline falls back to deterministic citation/relevance ranking and records the model status.

Key controls:

```bash
--knowledge_web true --knowledge_deep_literature true --knowledge_deep_max_papers_per_feature 50 --knowledge_deep_top_papers_per_sample 12 --knowledge_deep_enable_llm_ranker true --knowledge_deep_llm_ranker_max_candidates_per_sample 18 --run_clinician_reports true
```

For fast offline runs:

```bash
--knowledge_web false --knowledge_literature_llm false --knowledge_deep_enable_llm_ranker false
```


## v9 updates

This package is now named `cna_classifier_nf` and remains cancer-context aware through `--sample_set`.

New in v9:

- Deep PubMed/Europe-PMC literature enrichment for CNA features actually detected in the analyzed samples.
- Influential-paper ranking based on citation count, CNA/gene/context text overlap, abstract availability, recency, and optional local Hugging Face model scoring.
- Additional outputs in `06_knowledge/`:
  - `sample_literature.tsv`
  - `sample_literature_summary.tsv`
  - `knowledge_literature_ranker_trials.tsv`
- Per-sample PDF/HTML knowledge reports now include a selected influential-papers section before the full web-knowledge trace.
- A separate concise clinician report set is generated in `03_report/clinician_reports/` when `--run_clinician_reports true`.

Run lymphoma-context analysis:

```bash
nextflow run ./cna_classifier_nf/main.nf   -profile conda   -resume   --input /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina   --sample_set lymphoma   --pathology /media/server/STORAGE/LPWGS_2025/complete_biopsy_database_sanitized.csv   --pathology_sample_col illumina_sample_id   --outdir /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina/cna_classifier_nf_results   --gistic_exe auto   --gistic_refgene auto   --gistic_required false   --knowledge_web true   --run_pdf_reports true   --run_clinician_reports true
```

To reduce runtime while testing, disable Hugging Face ranking/synthesis:

```bash
--knowledge_literature_llm false --knowledge_deep_enable_llm_ranker false --pathology_use_biomed_models false
```

The clinician-facing report is intentionally concise. It contains probable CNA classification, pathology compatibility when provided, top driver CNA features, selected influential papers, and limitations. It is not a standalone diagnosis.


## v10 clinician-report readability update

The clinician-facing driver summary in `03_report/clinician_reports/` now uses a structured plain-language interpretation table immediately after section 1. Instead of one long dense paragraph, it separates:

- clinician-readable interpretation
- main copy-number evidence
- why the CNA pattern was assigned
- CNA burden context
- how to use the result clinically
- score meaning and limitations

Driver-region tables in clinician reports no longer truncate explanatory text with `...`; long text is wrapped so the complete interpretation is retained in both HTML and PDF reports.
