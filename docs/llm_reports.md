# LLM-assisted reports

The report LLM writes short, source-linked **literature drafts** about detected
CNA features. It does not assign the tumor type, change CNA calls, or interpret
the methylation classifier. Those results remain separate.

## Add reports to a completed run

Keep the **original YAML unchanged** and use the managed Conda installation:

```bash
oncotracer reports --config /absolute/path/project/config/run.yml --backend conda
```

This adds catalog-based interpretation reports from authenticated, completed
native CNA outputs. It does not rerun alignment, CNA calling/refinement,
stage-04 CNA plots, or methylation. The original config, source CNA tables, and native run manifest
remain unchanged. Report provenance is saved in
`05_cna_classifier/report_provenance.json`.

To retrieve public literature and attempt local model drafts:

```bash
oncotracer reports --config /absolute/path/project/config/run.yml --backend conda \
  --literature --model /absolute/path/to/report-model --threads 4
```

`--literature` is optional. Models load from local files/cache by default;
`--allow-model-download` permits downloads. Generation uses CPU with **4 threads**
by default. Missing models or usable abstracts produce labeled fallback text,
not a successful LLM draft. The command refreshes the dashboard and final report.

## Enable reports before a new analysis

Edit the project YAML created by `oncotracer setup` or `auto`. Replace existing
values for the keys below; add only keys that are absent. Do not append a second
copy of a key. Keep one `key: value` per line.

```yaml
run_cna_classifier: true
run_pdf_reports: true               # Matched HTML/PDF knowledge reports
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
small model may fail the required response format and use a labeled retrieved-text
or catalog fallback.

Use the same public command and paths as the rest of your analysis:

```bash
oncotracer check --config /absolute/path/project/config/run.yml
oncotracer run --config /absolute/path/project/config/run.yml --backend conda --dry-run
oncotracer run --config /absolute/path/project/config/run.yml --backend conda
```

This requires the classifier environment installed by `oncotracer install --conda`.
Use your actual YAML path; batch setup names it `illumina.auto.yml` or `ont.auto.yml`.
The dry run shows the plan; it does not load or evaluate the model.

## Open the finished reports

In native runs, final knowledge/LLM reports are published alongside classifier reports in
`05_cna_classifier/03_report/llm_reports/` below your YAML's `outdir`:

| File | Contents |
| --- | --- |
| `index.html` | Sample list with links to matching HTML and PDF reports |
| `<sample>_CNA_knowledge_report.html` | Per-sample report for browser review |
| `<sample>_CNA_knowledge_report.pdf` | PDF for the same sample and source sections |
| `all_sample_CNA_knowledge_reports.pdf` | Combined PDF for the reported sample set |
| `pdf_html_report_index.tsv` | Sample-to-HTML/PDF mapping and report metadata |
| `pdf_report_index.tsv` | Compatibility index with the same report rows |

```bash
OUT="/absolute/path/project/results" # replace with the outdir from your YAML
xdg-open "$OUT/05_cna_classifier/03_report/llm_reports/index.html"
```

The HTML/PDF indexes describe the same sample set. The folder can also contain
catalog-based reports when model generation is off or falls back; its presence
alone does not establish that an LLM generated text. `run_pdf_reports: false`
suppresses these matched knowledge reports. The cohort classifier report and
clinician summaries remain in `05_cna_classifier/03_report/`.
The combined `06_workflow_summary/final_report.html` and `final_report.json`
summarize CNA findings, literature sources and available methylation predictions,
retaining each branch's status.

## Check what actually happened

Evidence and model audit files remain in `05_cna_classifier/06_knowledge/` under
the analysis output; the report index links back to this evidence:

| File | What to check |
| --- | --- |
| `knowledge_metrics.json` | Generated/fallback counts, CPU settings, request attempts and retrieval errors |
| `knowledge_llm_trials.tsv` | Model/revision, submitted evidence, raw reply, rejection reason |
| `knowledge_base.tsv` | Feature text, `literature_synthesis_source`, retrieval status and usable abstract count |
| `knowledge_references.tsv` | Deduplicated PMID/DOI/PMCID metadata, source URLs and retrieval queries |

`literature_retrieval_status` distinguishes `not_enabled`, `retrieved`,
`partial_failure`, `retrieval_failed`, and `no_results`. A successful search can
return metadata without usable abstracts; check `n_usable_literature_abstracts`.
`no_results` means the completed queries returned no records, not that no relevant
research exists. Failed requests are not cached as empty searches. Transient
requests receive bounded retries; valid cached evidence remains usable during
network outages.

Accepted drafts cite IDs actually present in the submitted evidence. The code
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

Its attempts are recorded in
`05_cna_classifier/06_knowledge/knowledge_literature_ranker_trials.tsv`.

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

After installing a previously missing model or repairing retrieval, repeat `reports`
with `--force` to regenerate interpretation outputs while reusing cached literature.
This does not repeat alignment, CNA calling or methylation.
