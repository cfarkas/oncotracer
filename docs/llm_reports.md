# LLM-assisted reports

The report LLM writes short, source-linked **literature drafts** about detected
CNA features. It does not assign the tumor type, change CNA calls, or interpret
the methylation classifier. Those results remain separate.

## Enable it in your existing YAML

Add these settings to the project YAML created by `oncotracer setup`:

```yaml
run_cna_classifier: true
knowledge_web: true                 # Retrieve public paper titles and abstracts
knowledge_literature_llm: true
knowledge_literature_llm_models: /absolute/path/to/report-model
knowledge_literature_llm_local_files_only: true
knowledge_llm_threads: 4             # CPU only; no GPU allocation
knowledge_literature_llm_max_features: 24
knowledge_literature_llm_max_new_tokens: 192

# Start with deterministic paper ranking; fewer model calls.
knowledge_literature_reference_llm_selection: false
knowledge_deep_enable_llm_ranker: false
knowledge_deep_literature: false
```

`/absolute/path/to/report-model` is a **model directory**, not your reads or output
folder. It must contain the tokenizer, configuration, and Safetensors weights.
Standard Transformers encoder–decoder and causal language models are supported;
model repositories requiring custom Python code are not executed.

Alternatively, use a Hugging Face model ID, optionally pinned as
`organization/model@commit`. With `local_files_only: true`, it must already be
cached. Set that field to `false` only to allow model downloads. A comma-separated
model list supplies fallbacks, in order. Model size and draft quality vary; a
small model may fail the required response format and use the catalog fallback.

Use the same public command and paths as the rest of your analysis:

```bash
oncotracer run --config /absolute/path/project/oncotracer.yaml --backend conda --dry-run
oncotracer run --config /absolute/path/project/oncotracer.yaml --backend conda
```

This requires the classifier environment installed by `oncotracer install --conda`.
The dry run shows the plan; it does not load or evaluate the model.

## Check what actually happened

Look in `05_cna_classifier/06_knowledge/` under the analysis output:

| File | What to check |
| --- | --- |
| `knowledge_metrics.json` | Generated versus fallback feature counts, CPU settings |
| `knowledge_llm_trials.tsv` | Model/revision, submitted evidence, raw reply, rejection reason |
| `knowledge_base.tsv` | Final feature text and `literature_synthesis_source` |
| `knowledge_references.tsv` | Paper metadata for checking the citations |

Accepted drafts cite IDs actually present in the submitted abstracts. The code
inserts the corresponding PMIDs/DOIs and a review warning. Missing models,
malformed replies, unknown citations, or absent abstracts leave a labeled
deterministic fallback; an empty placeholder PMID is never treated as an abstract.

These checks verify format and citation identity, **not whether a claim is true**.
Review the cited papers before using any report. Tiny-model software tests are
not a clinical or model-quality validation.

## Optional LLM paper ranking

Enable `knowledge_literature_reference_llm_selection` to rank papers per feature
with the synthesis models. For the separate sample-wide ranking, set:

```yaml
knowledge_deep_enable_llm_ranker: true
knowledge_deep_llm_ranker_models: /absolute/path/to/report-model
knowledge_deep_llm_ranker_local_files_only: true
knowledge_deep_llm_ranker_max_candidates_per_sample: 18
```

Its attempts are recorded in `knowledge_literature_ranker_trials.tsv`.

## Network and privacy

Generation runs locally on CPU; there is no hosted LLM API call. Public literature
queries contain gene/region and cancer-context terms. Local-only model loading
does **not** disable those queries. Set `knowledge_web: false` to disable literature
retrieval; without retrieved abstracts this stage uses built-in catalog text.
Other optional models have separate switches, including
`pathology_use_biomed_models` and `knowledge_hf_ner`.

For repeatable runs, pin a model commit or retain an unchanged local model
directory. The audit records a prompt hash and resolved Hub revision when
available; a local model without a revision is marked `local_unversioned`.
