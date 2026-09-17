"""A factual combined report assembled from saved CNA and methylation outputs."""
from __future__ import annotations

import csv
import html
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

SCHEMA = "oncotracer-final-report-v1"


def _file(root: Path, relative: str) -> Path | None:
    """Only expose regular files within the physical result tree."""
    candidate = Path(relative)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(root)
        except ValueError:
            return None
    if ".." in candidate.parts:
        return None
    path = root
    for part in candidate.parts:
        path /= part
        if path.is_symlink():
            return None
    return path if path.is_file() and path.stat().st_size else None


def _json(root, relative):
    path = _file(root, relative)
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text())
    except (ValueError, OSError):
        return {"status": "unreadable", "error": f"Cannot read {relative}"}
    return value if isinstance(value, dict) else {"status": "unreadable"}


def _rows(root, relative, limit=200):
    path = _file(root, relative)
    if path is None:
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="," if path.suffix == ".csv" else "\t")
        rows = []
        for index, row in enumerate(reader):
            if limit is not None and index >= limit:
                break
            rows.append({str(key): str(value or "") for key, value in row.items() if key is not None})
        return rows


def _e(value):
    return html.escape(str(value if value is not None else "Not available"))


def _table(headers, rows, prediction=False):
    css = ' class="prediction-table"' if prediction else ''
    return '<div class="scroll"><table'+css+'><thead><tr>' + ''.join('<th>'+_e(x)+'</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+_e(x)+'</td>' for x in row)+'</tr>' for row in rows) + '</tbody></table></div>'


def final_report(root: Path, summary: dict) -> tuple[str, dict]:
    """Return HTML body and structured source-backed report; never infer diagnoses."""
    root = root.absolute()
    page = root / "06_workflow_summary/final_report.html"

    def link(relative, label):
        path = _file(root, str(relative or ""))
        if path is None:
            return '<span>'+_e(label)+' · unavailable</span>'
        return '<a href="'+quote(os.path.relpath(path, page.parent), safe="/")+'">'+_e(label)+'</a>'

    data = {"schema": SCHEMA, "workflow_status": summary.get("workflow_status", "unknown"),
            "cna_status": summary.get("cna_status", summary.get("workflow_status", "unknown")),
            "completed_samples": summary.get("completed_samples", []),
            "failed_samples": summary.get("failed_samples", []),
            "cna_events": {}, "literature": {}, "methylation": {"status": summary.get("methylation_status") or "not_requested"}}
    body = '<p><a href="../index.html">← All results</a></p><h1>Final analysis report</h1>'
    body += '<p class="status">Workflow: '+_e(data['workflow_status'])+'</p><p>Saved CNA findings, literature evidence and available methylation results. Each analysis retains its own status; missing or failed predictions are not normal results.</p>'
    events = [] if data['cna_status'] == 'not_requested' else _rows(root, '03_cna_codification/cna_events.tsv', limit=None)
    for row in events:
        counts = data['cna_events'].setdefault(row.get('sample', 'unknown'), {})
        state = row.get('state', 'unknown')
        counts[state] = counts.get(state, 0) + 1
    body += '<section class="card"><h2>1. CNA results</h2><p>Status: '+_e(data['cna_status'])+'</p>'
    if data['cna_events']:
        body += _table(['Sample', 'Event counts'], [(name, ', '.join(f'{state}: {n}' for state,n in sorted(counts.items()))) for name, counts in sorted(data['cna_events'].items())])
    if data['cna_status'] != 'not_requested':
        body += '<p>'+link('03_cna_codification/cna_events.tsv','Exact CNA event table')+' · '+link('04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf','Copy-number profiles')+'</p>'
    body += '</section>'

    metrics = _json(root, '05_cna_classifier/06_knowledge/knowledge_metrics.json')
    generated = metrics.get('literature_llm_completed_features')
    data['literature'] = {"status": summary.get('cna_classifier_status', 'available' if metrics else 'not_requested'),
                          "llm_generated_features": generated,
                          "source_counts": metrics.get('literature_source_counts', {}),
                          "failed_llm_trials": metrics.get('literature_llm_failed_trials'),
                          "retrieval_errors": metrics.get('web_errors', []),
                          "metrics": '05_cna_classifier/06_knowledge/knowledge_metrics.json' if metrics else None}
    body += '<section class="card"><h2>2. Literature and LLM interpretation</h2><p>Status: '+_e(data['literature']['status'])+'</p>'
    if data['literature']['status'] in {'failed', 'partial_failure'}:
        body += '<p>Report generation failed or is incomplete. Any retained draft below may come from an earlier attempt.</p>'
    if metrics.get('status') == 'unreadable':
        body += '<p>Evidence metrics are unreadable; generation counts and evidence completeness are unknown.</p>'
    if not metrics:
        body += '<p>No literature/LLM evidence was produced for this run. Enable interpretation reports and choose literature with local language models, or add reports to this completed run with <code>oncotracer reports --config /path/to/run.yml --literature</code>.</p>'
    else:
        body += '<p>Accepted literature LLM drafts: '+_e(generated)+'. Failed generation trials: '+_e(data['literature']['failed_llm_trials'])+'.</p>'
        if generated == 0:
            body += '<p>No accepted literature LLM draft was generated. Any catalog or retrieved-text fallback is labeled below.</p>'
        if data['literature']['retrieval_errors']:
            body += '<p>Some literature requests failed. Check the evidence audit; incomplete retrieval is not evidence of no association.</p>'
        drafts = _rows(root,'05_cna_classifier/06_knowledge/sample_knowledge.tsv', limit=100)
        drafts = [row for row in drafts if row.get('literature_synthesis')]
        data['literature']['drafts'] = [{key: row.get(key, '') for key in ('sample','feature_id','literature_synthesis','literature_synthesis_source')} for row in drafts]
        if drafts:
            body += _table(['Sample / feature','Draft or fallback text','Source'], [(row.get('sample','')+' / '+row.get('display',row.get('feature_id','')),row['literature_synthesis'],row.get('literature_synthesis_source','unknown')) for row in drafts])
            body += '<p class="muted">Up to 100 feature entries shown. Drafts require review against the cited papers; model acceptance checks do not establish clinical validity.</p>'
        report_path = '05_cna_classifier/03_report/llm_reports/index.html'
        if _file(root, report_path) is None:
            report_path = '04_cna_custom_plots/llm_reports/index.html'  # Existing releases.
        body += '<p>'+link(report_path,'Full sample HTML/PDF reports')+' · '+link('05_cna_classifier/06_knowledge/knowledge_references.tsv','Citations and source metadata')+' · '+link('05_cna_classifier/06_knowledge/knowledge_llm_trials.tsv','Model generation audit')+'</p>'
        references = _rows(root,'05_cna_classifier/06_knowledge/knowledge_references.tsv', limit=100)
        links, seen = [], set()
        for row in references:
            url = row.get('url','')
            if urlsplit(url).scheme not in {'https','http'} or url in seen:
                continue
            seen.add(url)
            links.append('<li><a href="'+html.escape(url,quote=True)+'">'+_e(row.get('title') or row.get('pmid') or row.get('doi') or url)+'</a></li>')
        if links:
            body += '<details><summary>Retrieved references (up to 100)</summary><ul>'+''.join(links)+'</ul></details>'
    body += '</section>'

    methylation = _json(root, '07_methylation/methylation_status.json')
    if not methylation and data['methylation']['status'] != 'not_requested':
        body += '<section class="card"><h2>3. Methylation results</h2><p>Status: '+_e(data['methylation']['status'])+'</p><p>The methylation status artifact is unavailable. Per-sample predictions and provenance could not be verified.</p></section>'
    if methylation:
        data['methylation'] = {"status": methylation.get('overall_status', methylation.get('status', 'unknown')),
                               "classifier": methylation.get('classifier', 'unknown'), "samples": []}
        body += '<section class="card"><h2>3. Methylation results</h2><p>Classifier: '+_e(data['methylation']['classifier'])+' · Status: '+_e(data['methylation']['status'])+'</p>'
        records = methylation.get('samples', [])
        for record in records if isinstance(records,list) else []:
            if not isinstance(record, dict):
                continue
            entry = {key: record.get(key) for key in ('sample','barcode','status','error','read_id_count','modbam_records','covered_cpg_rows','modified_cpg_calls','covered_classifier_probes')}
            data['methylation']['samples'].append(entry)
            body += '<h3>'+_e(entry['sample'])+' · '+_e(entry['status'])+'</h3>'
            body += _table(['QC measure','Observed value'], [('Selected FASTQ read IDs',entry['read_id_count']),('Matched BAM records',entry['modbam_records']),('Covered CpG rows',entry['covered_cpg_rows']),('Modified CpG calls',entry['modified_cpg_calls']),('Covered classifier probes',entry['covered_classifier_probes'])])
            if entry['error']:
                body += '<p>'+_e(entry['error'])+'</p>'
            if record.get('bedmethyl'):
                body += '<p>'+link(record['bedmethyl'],'CpG methylation profile (bedMethyl)')+'</p>'
            # Ignore stale prediction files for samples without successful classification.
            if entry['status'] == 'complete':
                prediction = _file(root, str(record.get('classification') or ''))
                if prediction:
                    entry['classification'] = str(prediction.relative_to(root))
                    rows = _rows(root,entry['classification'],limit=3)
                    body += '<p>'+link(entry['classification'],'Full classifier predictions and scores')+'</p>'
                    if rows:
                        columns = list(rows[0])[:16]
                        body += _table(columns, [[row.get(key,'') for key in columns] for row in rows], prediction=True)
                        body += '<p class="muted">Raw classifier output preview: first 3 rows and 16 columns. The linked file contains all classes and scores.</p>'
                else:
                    entry['prediction_error'] = 'Recorded prediction file is missing or unsafe.'
                    body += '<p>Recorded prediction file is unavailable; do not treat this sample as a verified classification.</p>'
        body += '<p>Methylation predictions and CNA interpretation are separate findings. No combined diagnosis is inferred.</p><p>'+link('07_methylation/methylation_status.json','Methylation status')+' · '+link('07_methylation/methylation_provenance.json','Tools, models and input provenance')+'</p></section>'
    body += '<section class="card"><h2>Run provenance</h2><p>'+link('06_workflow_summary/workflow_summary.txt','Workflow summary')+' · '+link('06_workflow_summary/native_run_manifest.json','Original run manifest')+' · '+link('05_cna_classifier/report_provenance.json','Added-report provenance')+'</p><p class="muted">This report summarizes saved outputs. Browser Print can export it to PDF.</p></section>'
    return body, data
