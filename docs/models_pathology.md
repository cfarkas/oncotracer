# Models, Literature, and Pathology Inference

OncoTracer uses deterministic CNA rules first. Optional machine-learning and language-model layers are used only as add-ons. If an optional model fails, the pipeline records the failure and continues whenever possible.

## CNA Classifier

The CNA classifier uses:

- a pan-cancer CNA region catalog;
- CNA burden metrics;
- focal/broad event summaries;
- driver-region flags;
- optional GISTIC2 processing;
- a built-in CNA knowledge dictionary;
- optional literature retrieval from public PubMed/Europe-PMC-style metadata;
- optional Hugging Face literature synthesis and reference-ranking models.

The classifier produces a probable CNA classification, not a definitive diagnosis.

## Literature Models

Default literature models attempted:

```text
google/flan-t5-small
google/flan-t5-base
Falconsai/medical_summarization
```

They are used for:

- short literature synthesis for detected CNA features;
- selecting/ranking influential references from candidate papers.

Trial logs:

```text
05_cna_classifier/06_knowledge/knowledge_llm_trials.tsv
05_cna_classifier/06_knowledge/knowledge_literature_ranker_trials.tsv
```

If these models are unavailable or fail in the local Transformers environment, OncoTracer falls back to deterministic citation/relevance ranking and built-in CNA interpretation text.

## Optional Biomedical NER

Default optional NER model:

```text
d4data/biomedical-ner-all
```

This is disabled by default to keep runs lighter.

## Pathology Concordance/Inferences

When `pathology_csv` is supplied, OncoTracer performs pathology-vs-CNA concordance.

It does the following:

1. Matches CNA samples to pathology rows using the configured sample column.
2. Reads diagnosis, site, microscopic text, macroscopic text, and IHC/marker fields when available.
3. Infers broad pathology context using deterministic text and marker rules.
4. Compares inferred pathology features with CNA classifier features.
5. Computes an agreement call and score.
6. Optionally attempts biomedical transformer semantic scoring.
7. Writes complete audit tables.

Agreement calls can include:

```text
AGREEMENT
PARTIAL_AGREEMENT
PARTIAL_AGREEMENT_NON_LYMPHOMA
NOT_ASSESSABLE
NO_MATCH
PATHOLOGY_NOT_PROVIDED
```

The deterministic pathology agreement model is recorded as:

```text
OncoTracer local token agreement model v3.0-context-aware
```

## Pathology Biomedical Transformer Models

Default models attempted when `pathology_use_biomed_models: true`:

```text
microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract
dmis-lab/biobert-base-cased-v1.1
emilyalsentzer/Bio_ClinicalBERT
```

The model layer is recorded as:

```text
OncoTracer optional biomedical transformer agreement layer v1.0
```

Trial log:

```text
05_cna_classifier/07_pathology/pathology_model_trials.tsv
```

Metrics:

```text
05_cna_classifier/07_pathology/pathology_concordance_metrics.json
```

## Interpretation Limits

Pathology concordance does not replace pathology review. It reports whether CNA evidence is compatible with the text/IHC record available in the table. It cannot determine histologic diagnosis, mutation status, fusion status, methylation class, or clinically validated HER2/EGFR/MYC/etc. status by itself.
