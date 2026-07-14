# Models and pathology

This page explains what happens after `run_cna_classifier: true`. For the copy-and-paste setup, matched CSV files, and run commands, begin with [Pathology and classifier configuration](configuration/pathology.md).

## What enters the optional stage

The classifier does not read FASTQs directly. The core workflow first creates:

```text
03_cna_codification/cna_events.tsv
```

Each row describes a copy-number event derived from the refined low-pass WGS bins. The optional stage uses those events to summarize CNA burden, recurrent regions, affected cytobands, and context-associated patterns. A pathology CSV, when supplied, is joined by the exact sample identifier.

## Choose the context before looking at results

`cna_classifier_sample_set` limits the labels and knowledge catalog used during interpretation. Choose the narrowest context justified by specimen provenance or study design.

| Example context | Appropriate use |
| --- | --- |
| `broad_cancer` | Exploratory pan-cancer cohort without a defensible narrower context. |
| `lymphoma` | Cohort already established as lymphoma-focused. |
| Organ/disease contexts such as `breast`, `brain`, `colon`, or `pancreas` | A study whose inclusion criteria already establish that context. |

Do not try several contexts and report only the one that gives the preferred classification.

## Minimal and model-assisted modes

Start with deterministic, lightweight pathology comparison:

```yaml
run_cna_classifier: true
cna_classifier_sample_set: broad_cancer
cna_classifier_profile: conda
pathology_csv: /absolute/path/oncotracer/project/input/pathology.csv
pathology_sample_col: illumina_sample_id
pathology_case_col: case_code
pathology_diagnosis_col: final_diagnosis
pathology_use_biomed_models: false
pathology_biomed_local_files_only: true
```

After that run succeeds, model assistance can be enabled:

```yaml
pathology_use_biomed_models: true                 # try the configured biomedical language models
pathology_biomed_local_files_only: false          # allow missing model files to be downloaded
```

The first model-assisted run may download several model packages and require substantial disk space. For an offline system, populate the model cache first, then set `pathology_biomed_local_files_only: true`. OncoTracer records model attempts in `07_pathology/pathology_model_trials.tsv`; it does not silently convert a failed model load into clinical evidence.

## What the classifier can use

From CNA data it can use broad and focal gains/losses, amplifications, deep losses, altered-genome burden, aneuploidy, recurrent cytobands, and cataloged driver regions. These features may support a CNA-pattern class or flag compatibility with supplied pathology.

Low-pass read-depth CNA analysis does **not** reliably determine:

- single-nucleotide variants or small insertions/deletions;
- balanced translocations or most gene fusions;
- methylation class or RNA/protein expression;
- copy-neutral loss of heterozygosity;
- clonality or biallelic inactivation without other evidence.

Absence from an OncoTracer report is therefore not evidence that one of these alterations is absent.

## Read the outputs in this order

```bash
OUT="$PWD/project/runs/illumina_pathology/05_cna_classifier"
sed -n '1,8p' "$OUT/01_prepared/sample_cna_summary.tsv"                 # CNA features supplied to classification
sed -n '1,8p' "$OUT/02_classification/cna_patient_classification.tsv"  # CNA-pattern research classification
sed -n '1,8p' "$OUT/07_pathology/pathology_concordance.tsv"            # comparison with supplied pathology
sed -n '1,80p' "$OUT/07_pathology/pathology_status.txt"                # matching/model status and warnings
```

Then open the cohort report:

```bash
xdg-open "$OUT/03_report/cna_classifier_report.html" # open locally when a desktop is available
```

On a remote server, copy the HTML/PDF report directory to your workstation instead of trying to open a browser on the server.

Important result groups are:

| Location | Contents | Interpretation status |
| --- | --- | --- |
| `01_prepared/` | Normalized event and per-sample feature tables | Classifier input; derived from core CNA calls |
| `02_classification/` | Probable CNA-pattern class and scores | Research interpretation, not diagnosis |
| `03_report/` | Cohort HTML, per-sample HTML/PDF, and optional clinician summaries | Presentation layer; verify against tables |
| `04_gistic2/`, `05_gistic2_parsed/` | Optional cohort recurrence analysis and parsed results | Cohort-level; small cohorts may be uninformative |
| `06_knowledge/` | Driver-region and literature-linked summaries | Hypothesis-supporting evidence requiring review |
| `07_pathology/` | Matched records, concordance table, metrics, status, and model trials | Compatibility assessment, not diagnostic agreement |

## How to interpret concordance

An agreement call asks whether observed CNA features are compatible with the supplied diagnosis under the selected context. A disagreement or indeterminate result can reflect low tumor fraction, limited sequencing depth, a CNA-quiet tumor, sample mismatch, an incomplete knowledge catalog, or a diagnosis driven by alterations invisible to LP-WGS.

Use it as a review flag:

1. confirm the samplesheet-to-pathology identifier match;
2. inspect coverage, segmentation, and tumor content;
3. review the actual CNA events and supporting regions;
4. compare with morphology, IHC, cytogenetics, and clinical-grade molecular tests;
5. document the final human interpretation separately.

## Reproducibility and privacy checklist

- Record the OncoTracer commit, container tag or digest, sample-set context, and YAML.
- Preserve the input pathology header mapping and a de-identified case key under appropriate governance.
- Do not put names, national identifiers, dates of birth, or unnecessary free clinical text into the analysis table.
- Do not enable network model retrieval unless the computing and data-governance policy permits it.
- Manually verify literature references and model-produced summaries.

!!! danger "Not a medical device"
    Classifier scores, driver summaries, literature links, and pathology compatibility are research outputs. They do not replace a validated diagnostic assay or expert pathology review.
