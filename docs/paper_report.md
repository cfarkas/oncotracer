# Manuscript figures from saved evidence

Use `oncotracer --paper_report` to create English manuscript panels and a browsable report from explicit tables of saved results. Each figure has its own caption, source data and rendering provenance.

This is a standalone reporting command. It does not run alignment, CNA refinement, variant calling, literature retrieval or language models. It requires no installed analysis backend. Run it after the evidence tables needed for a figure are available.

## Install the plotting dependencies

From your OncoTracer source checkout, with the intended Python environment activated:

```bash
python -m pip install -e '.[paper]'
```

The optional `paper` extra supplies Matplotlib and NumPy. If an existing environment already contains those libraries and this OncoTracer checkout, use that environment. The plotting dependencies are loaded only when generating a paper report.

## Try the small synthetic example

**Terminal / headless version**, from the source checkout:

```bash
oncotracer --paper_report \
  --manifest examples/paper_report/manifest.json \
  --outdir "$PWD/paper-report-example"
```

The example contains two deliberately synthetic library counts. It demonstrates figure rendering and provides no biological validation.

These commands are equivalent:

```bash
oncotracer --paper-report --manifest paper-manifest.json --outdir paper-figures
oncotracer paper-report --manifest paper-manifest.json --outdir paper-figures
```

`--paper-manifest` and `--paper_manifest` are aliases for `--manifest`. Use the flag at the top level, as shown; `run --paper_report` is not supported. The terminal prints the generated `index.html` path.

## Define the figures

A JSON manifest identifies the figure order, captions, layouts and input tables. Table paths are relative to the manifest. A minimal example is:

```json
{
  "schema": "oncotracer-paper-report-v1",
  "language": "en",
  "title": "Synthetic cohort illustration",
  "figures": [
    {
      "id": "Fig1",
      "stem": "Figure_1_synthetic_cohort",
      "title": "Synthetic cohort illustration",
      "caption": "Illustrative library counts only; these are synthetic data.",
      "evidence_status": "descriptive",
      "layout": {"rows": 1, "cols": 1, "width": 7.2, "height": 4.8},
      "panels": [
        {
          "letter": "A",
          "type": "bar",
          "title": "Synthetic libraries by platform",
          "data": "cohort_counts.tsv",
          "xlabel": "Sequencing platform",
          "ylabel": "Libraries"
        }
      ]
    }
  ]
}
```

The TSV has a header and one row per category:

```text
category	value
Illumina	3
ONT	2
```

Supported panel types are `bar`, `distribution`, `scatter`, `line` and `matrix`. Select the columns and labels appropriate to the evidence table. Keep captions explicit about the number of patients, specimens, libraries and technical runs; these are different denominators.

## Plan the manuscript panels

| Figure | Required evidence | Interpretation |
| --- | --- | --- |
| 1. Cohort description | Curated, de-identified cohort and sequencing metadata | Descriptive counts and quality distributions. |
| 2. BAM/CNA refinement benchmark | Baseline/refined calls and independent reference events | Accuracy claims require a truth standard; movement of boundaries alone is descriptive. |
| 3. Variant evaluation | Measured depth, verified paired specimens, callable regions and compared callsets | Illumina–ONT agreement is concordance; independent truth is needed for accuracy. |
| 4. LLM report evaluation | Saved outputs, evidence citations and a defined reviewer evaluation | Report generation alone does not establish factual or diagnostic accuracy. |

Use `evidence_status` to distinguish `descriptive`, `concordance` and `independent_validation`. This label records the study design; the renderer cannot certify the underlying evidence. Include only figures with real input data. Unfinished analyses should not be replaced by invented results.

## Output

The report directory contains:

- Figure exports in PDF, SVG and 600-dpi PNG.
- Individual panel crops under `panels/`.
- Captions under `legends/`.
- Copies of the input plotting tables under `source_data/`.
- Rendering provenance and artifact checksums under `provenance/`.
- `index.html` to browse the figures and supporting files.

The source-table copies travel with the report. Supply de-identified, publication-appropriate plotting tables; do not use the private identity database or re-identification key as a figure input. Keep the full study linkage in its separate restricted location.
