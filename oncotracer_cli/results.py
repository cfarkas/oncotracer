"""Publish final CNA files and browse new or existing result trees locally."""
from __future__ import annotations

import fcntl
import html
import json
import os
import shutil
from pathlib import Path
from urllib.parse import quote

from .final_report import SCHEMA as FINAL_REPORT_SCHEMA, final_report
from .runtime import OncoTracerError, atomic_write_json, atomic_write_text, require_file

MARKER = "<!-- OncoTracer results browser v1 -->"
STAGES = (
    ("01_samurai_illumina", "01 · Alignment and initial CNA", "Caller results and sample quality checks."),
    ("01_samurai_ont", "01 · Alignment and initial CNA", "Caller results and sample quality checks."),
    ("02_bam_refinement", "02 · Refined copy-number results", "Final segments, refined bins, and boundary quality checks."),
    ("03_cna_codification", "03 · CNA event tables", "Authoritative event calls and cytogenomic notation."),
    ("04_cna_custom_plots", "04 · CNA plots", "Primary PDFs and per-sample copy-number plots."),
    ("05_cna_classifier", "05 · Interpretation and evidence", "CNA-pattern classifications, clinician summaries, and literature evidence."),
    ("06_workflow_summary", "06 · Final report and run status", "Combined CNA, literature and methylation report, status and provenance."),
    ("07_methylation", "07 · Methylation", "Per-sample status, CpG results, classifier predictions, and scores."),
)
PRIMARY_PLOTS = ("cna_per_sample_pages.pdf", "cna_log2_ratio_profiles_all_samples.pdf")


def publish_refinement(stage: Path, dataset: str) -> Path:
    """Keep one published final/QC set; retain full tool output as diagnostics."""
    raw = stage / "diagnostics" / dataset
    published = stage / dataset
    names = {
        "04_final_results": ("final_segments.tsv", "final_segments.bed",
                             "refined_bins_boundary_bp_difference.csv", "refined_bins_boundary_bp_difference.xlsx"),
        "01_tables": ("refined_bins.tsv.gz", "sample_refinement_summary.csv",
                      "boundary_refinement_statistics.csv", "bam_preparation_report.csv"),
    }
    records = []
    for folder, filenames in names.items():
        for filename in filenames:
            source = raw / folder / filename
            if not source.is_file():
                if filename in {"final_segments.tsv", "refined_bins.tsv.gz"}:
                    require_file(source, "refinement result")
                continue
            destination = published / folder / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            records.append({"path": str(destination.relative_to(stage)), "source": str(source.relative_to(stage)),
                            "bytes": destination.stat().st_size})
    atomic_write_json(published / "published_results.json", {"schema": "oncotracer-refinement-publication-v1", "files": records})
    return published


def organize_plot_exports(stage: Path) -> None:
    """Group newly generated supplementary formats without moving primary PDFs."""
    if not stage.is_dir():
        return
    for source in sorted(stage.iterdir()):
        if not source.is_file() or source.is_symlink() or source.name in PRIMARY_PLOTS:
            continue
        if source.suffix.lower() in {".pdf", ".png", ".svg"}:
            destination = stage / "exports" / source.suffix.lower()[1:] / source.name
        elif source.suffix.lower() == ".tsv" and (source.name.startswith("plot_table_") or source.name == "cna_plot_no_events_summary.tsv"):
            destination = stage / "tables" / source.name
        else:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)


def _safe_child(root: Path, path: Path) -> Path:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise OncoTracerError(f"Results presentation will not follow an output symlink: {current}")
    return path


def _write_page(root: Path, path: Path, content: str) -> None:
    _safe_child(root, path)
    if path.exists() and (not path.is_file() or not path.read_text(encoding="utf-8", errors="replace").startswith(MARKER)):
        raise OncoTracerError(f"Results presentation will not overwrite an unrelated file: {path}")
    atomic_write_text(path, MARKER + "\n" + content)


def _link(page: Path, target: Path, label: str) -> str:
    href = quote(os.path.relpath(target, page.parent), safe="/")
    return f'<a href="{href}">{html.escape(label)}</a>'


def _page(title: str, body: str) -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · OncoTracer</title><style>
body{{margin:0;background:#f3f6f4;color:#1d343c;font:16px/1.55 system-ui,sans-serif}}main{{max-width:1100px;margin:35px auto;padding:0 24px 50px}}h1{{font-size:32px;letter-spacing:-.8px}}h2{{font-size:21px}}a{{color:#116d62;overflow-wrap:anywhere}}.intro,.card{{background:#fff;border:1px solid #d8e3df;border-radius:12px;padding:22px;margin:18px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}.grid .card{{margin:0}}.muted{{color:#617078;font-size:14px}}.status{{font-weight:650;color:#166c5d}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:9px 8px;border-bottom:1px solid #e1e8e5;overflow-wrap:anywhere}}th{{color:#617078}}td:last-child{{white-space:nowrap}}.scroll{{overflow:auto}}.prediction-table th,.prediction-table td{{min-width:9rem;white-space:normal}}.badge{{font:12px ui-monospace,monospace;background:#edf3ef;border-radius:5px;padding:4px 8px}}ul{{padding-left:20px}}code{{overflow-wrap:anywhere}}details{{margin-top:18px}}summary{{cursor:pointer;color:#116d62}}</style></head><body><main>{body}</main></body></html>'''


def _diagnostic(stage: str, relative: Path) -> bool:
    parts = relative.parts
    if "diagnostics" in parts or "_work" in parts or "merged_fastq" in parts or ".reports" in parts:
        return True
    if stage.startswith("01_samurai_") and (relative.suffix in {".bam", ".bai", ".rds"} or "input" in parts):
        return True
    if stage == "02_bam_refinement":
        return not (len(parts) == 3 and
                    ((parts[1] == "04_final_results" and relative.name in {"final_segments.tsv", "final_segments.bed", "refined_bins_boundary_bp_difference.csv", "refined_bins_boundary_bp_difference.xlsx"})
                     or (parts[1] == "01_tables" and relative.name in {"refined_bins.tsv.gz", "sample_refinement_summary.csv", "boundary_refinement_statistics.csv", "bam_preparation_report.csv"})))
    if stage == "01_samurai_ont" and len(parts) >= 4 and parts[:2] == ("results", "ichorcna"):
        return True
    return False


def _quality(stage: str, relative: Path) -> bool:
    name = relative.name
    return (name.endswith(("sample_status.json", "summary_mqc.txt", ".metrics.txt"))
            or name in {"sample_refinement_summary.csv", "boundary_refinement_statistics.csv",
                        "bam_preparation_report.csv", "refined_bins_boundary_bp_difference.csv",
                        "refined_bins_boundary_bp_difference.xlsx", "knowledge_metrics.json",
                        "methylation_status.json"})


def _important(stage: str, relative: Path) -> bool:
    name = relative.name
    if stage.startswith("01_samurai_"):
        return (name in {"all_segments.seg", "all_calls.seg", "all_segments_ichorcna_gistic.seg"}
                or ("plots" in relative.parts and relative.suffix == ".pdf"))
    if stage == "02_bam_refinement":
        return name in {"final_segments.tsv", "refined_bins.tsv.gz", "sample_refinement_summary.csv"}
    if stage == "03_cna_codification":
        return name in {"cna_events.tsv", "cna_cytogenomic_notation.tsv"}
    if stage == "04_cna_custom_plots":
        return name in PRIMARY_PLOTS or relative.as_posix() == "llm_reports/index.html"
    if stage == "05_cna_classifier":
        return name in {"cna_patient_classification.tsv", "cna_classifier_report.html", "knowledge_metrics.json"} or relative.as_posix() == "03_report/clinician_reports/index.html" or relative.as_posix() == "03_report/llm_reports/index.html"
    if stage == "06_workflow_summary":
        return name in {"final_report.html", "final_report.json", "workflow_summary.txt", "workflow_summary.json", "native_run_manifest.json"}
    if stage == "07_methylation":
        return name in {"methylation_status.json", "methylation_provenance.json"} or (relative.suffix in {".tsv", ".csv", ".json", ".pdf", ".html"} and any(word in name.lower() for word in ("score", "prediction", "probabilit", "summary", "qc")))
    return name.endswith("sample_status.json") or name.endswith("summary_mqc.txt")


def _table(page: Path, root: Path, files: list[Path]) -> str:
    rows = []
    for path in files:
        size = path.stat().st_size
        label = str(path.relative_to(root))
        readable = f"{size / 1048576:.1f} MiB" if size >= 1048576 else f"{size:,} B"
        rows.append(f"<tr><td>{_link(page, path, label)}</td><td>{readable}</td></tr>")
    return '<div class="scroll"><table><thead><tr><th>File</th><th>Size</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"


def write_results_index(outdir: Path) -> Path:
    """Refresh presentation files only, reading either legacy or organized output."""
    outdir = Path(outdir).absolute()
    if outdir.is_symlink():
        raise OncoTracerError("Choose the physical results directory when refreshing its presentation.")
    summary_path = _safe_child(outdir, outdir / "06_workflow_summary/workflow_summary.json")
    summary = json.loads(require_file(summary_path, "workflow summary").read_text())
    if not isinstance(summary, dict):
        raise OncoTracerError("The workflow summary must be a JSON object.")
    root_page = outdir / "index.html"
    catalog_path = _safe_child(outdir, outdir / "06_workflow_summary/results_catalog.json")
    if catalog_path.exists():
        previous = json.loads(catalog_path.read_text())
        if not isinstance(previous, dict) or previous.get("schema") != "oncotracer-results-catalog-v1":
            raise OncoTracerError(f"Refusing to overwrite an unrelated catalog: {catalog_path}")
    report_page = _safe_child(outdir, outdir / "06_workflow_summary/final_report.html")
    report_json = _safe_child(outdir, outdir / "06_workflow_summary/final_report.json")
    if report_json.exists():
        try:
            previous_report = json.loads(report_json.read_text())
        except (ValueError, OSError) as error:
            raise OncoTracerError(f"Refusing to overwrite an unreadable final report: {report_json}") from error
        if not isinstance(previous_report, dict) or previous_report.get("schema") != FINAL_REPORT_SCHEMA:
            raise OncoTracerError(f"Refusing to overwrite an unrelated final report: {report_json}")
    report_body, report_data = final_report(outdir, summary)
    cards, catalog = [], []
    planned = [(report_page, _page("Final analysis report", report_body))]
    for stage, title, description in STAGES:
        directory = outdir / stage
        if not directory.is_dir() or directory.is_symlink():
            if stage == "05_cna_classifier":
                report_status = str(summary.get("cna_classifier_status") or "not_requested")
                cards.append(f'<section class="card"><h2>{html.escape(title)}</h2><p>Status: {html.escape(report_status)}</p><p>Interpretation files are absent. Add them from the completed CNA outputs with <code>oncotracer reports --config /path/to/run.yml --literature</code>.</p></section>')
                catalog.append({"stage": stage, "title": title, "status": report_status, "index": None,
                                "primary_files": [], "quality_control_files": [], "supporting_files": [],
                                "diagnostic_file_count": 0})
            continue
        page = directory / "index.html"
        diagnostic_page = directory / "diagnostics.html"
        files = [path for path in sorted(directory.rglob("*"))
                 if path.is_file() and not path.is_symlink() and path.stat().st_size > 0
                 and path not in {page, diagnostic_page, catalog_path, report_page, report_json} and ".oncotracer-native" not in path.parts]
        files = [path for path in files if not any(parent.is_symlink() for parent in path.parents if parent != outdir)]
        files = [path for path in files if not (path.name == "index.html" and
                 path.read_text(encoding="utf-8", errors="replace").startswith(MARKER))]
        diagnostics = [path for path in files if _diagnostic(stage, path.relative_to(directory))]
        visible = [path for path in files if path not in diagnostics]
        quality = [path for path in visible if _quality(stage, path.relative_to(directory))]
        primary = [path for path in visible if path not in quality and _important(stage, path.relative_to(directory))]
        navigation = _link(page, root_page, "← All results")
        body = f"<p>{navigation}</p><h1>{html.escape(title)}</h1><p>{html.escape(description)}</p>"
        if stage == "06_workflow_summary":
            body += '<section class="card"><h2>Final report</h2><p>' + _link(page, report_page, "Open combined analysis report") + ' · ' + _link(page, report_json, "Structured report (JSON)") + '</p></section>'
        if primary:
            body += '<section class="card"><h2>Primary results</h2>' + _table(page, directory, primary) + "</section>"
        if quality:
            body += '<section class="card"><h2>Quality control</h2>' + _table(page, directory, quality) + "</section>"
        remaining = [path for path in visible if path not in primary and path not in quality]
        if remaining:
            body += '<section class="card"><h2>Supporting files and exports</h2>' + _table(page, directory, remaining) + "</section>"
        if diagnostics:
            body += '<section class="card"><h2>Diagnostics and intermediate files</h2><p class="muted">Retained for troubleshooting and reproducibility. Final results are listed above.</p>' + _link(page, diagnostic_page, f"Browse {len(diagnostics)} diagnostic files") + "</section>"
            diagnostic_body = f'<p>{_link(diagnostic_page, page, "← Stage results")}</p><h1>{html.escape(title)} · Diagnostics</h1><p>Tool intermediates, model-fit plots, and compatibility representations. These may contain duplicate representations of final data.</p>' + _table(diagnostic_page, directory, diagnostics)
            planned.append((diagnostic_page, _page(title + " diagnostics", diagnostic_body)))
        planned.append((page, _page(title, body)))
        if stage == "05_cna_classifier":
            evidence = directory / "06_knowledge"
            if evidence.is_dir() and not evidence.is_symlink():
                evidence_page = evidence / "index.html"
                evidence_files = [path for path in files if path.parent == evidence and path != evidence_page]
                evidence_body = f'<p>{_link(evidence_page, page, "← Interpretation results")}</p><h1>Literature evidence and model audit</h1><p>Check generation status and cited sources before interpreting the knowledge reports.</p>' + _table(evidence_page, evidence, evidence_files)
                planned.append((evidence_page, _page("Literature evidence", evidence_body)))
        links = "".join(f"<li>{_link(root_page, path, path.name if path.name != 'index.html' else ('Clinician summaries' if path.parent.name == 'clinician_reports' else 'Knowledge HTML/PDF reports'))}</li>" for path in primary[:6])
        cards.append(f'<section class="card"><h2>{_link(root_page, page, title)}</h2><p class="muted">{html.escape(description)}</p><ul>{links}</ul></section>')
        catalog.append({"stage": stage, "title": title, "index": str(page.relative_to(outdir)),
                        "primary_files": [str(path.relative_to(outdir)) for path in primary] + ([str(report_page.relative_to(outdir)), str(report_json.relative_to(outdir))] if stage == "06_workflow_summary" else []),
                        "quality_control_files": [str(path.relative_to(outdir)) for path in quality],
                        "supporting_files": [str(path.relative_to(outdir)) for path in remaining],
                        "diagnostic_file_count": len(diagnostics)})
    completed = summary.get("completed_samples", [])
    failed = summary.get("failed_samples", [])
    status = str(summary.get("workflow_status") or "unknown")
    cna_status = str(summary.get("cna_status") or status)
    counts = ("CNA: not requested." if cna_status == "not_requested" else
              f"CNA status: {cna_status} · Completed samples: {len(completed)} · Failed samples: {len(failed)}.")
    if summary.get("methylation_status"):
        counts += (f" Methylation status: {summary['methylation_status']} · Completed samples: "
                   f"{len(summary.get('methylation_completed_samples', []))} · Failed samples: "
                   f"{len(summary.get('methylation_failed_samples', []))}.")
    body = f'<div class="intro"><span class="badge">OncoTracer results</span><h1>Your analysis results</h1><p class="status">Status: {html.escape(status)}</p><p>Open the combined report for CNA findings, literature evidence and available methylation results. Stages 01 and 02 use the same groups: primary results, quality control, supporting files and diagnostics.</p><p>{_link(root_page, report_page, "Open final analysis report")}</p><p class="muted">{html.escape(counts)} Check the workflow and per-sample status files before interpreting results.</p></div><div class="grid">' + "".join(cards) + '</div><p class="muted">This dashboard is a presentation of the saved files. Refreshing it does not rerun analysis or alter scientific outputs.</p>'
    planned.append((root_page, _page("Analysis results", body)))
    # Fail before changing any page if a user-created index occupies a target.
    for path, _content in planned:
        _safe_child(outdir, path)
        if path.exists() and (not path.is_file() or not path.read_text(encoding="utf-8", errors="replace").startswith(MARKER)):
            raise OncoTracerError(f"Results presentation will not overwrite an unrelated file: {path}")
    for path, content in planned:
        _write_page(outdir, path, content)
    atomic_write_json(report_json, report_data)
    atomic_write_json(catalog_path, {"schema": "oncotracer-results-catalog-v1", "workflow_status": status,
                                     "index": "index.html", "stages": catalog})
    return root_page


def command_results(args) -> int:
    from .output_safety import _reject_broad_target, _reject_symlink_components

    outdir = Path(args.outdir).expanduser().absolute()
    _reject_broad_target(outdir)
    _reject_symlink_components(outdir)
    native = _safe_child(outdir, outdir / ".oncotracer-native")
    if not native.is_dir():
        raise OncoTracerError("This is not a native OncoTracer result directory: no .oncotracer-native folder.")
    lock_path = _safe_child(outdir, native / "run.lock")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OncoTracerError("An analysis is running in this results directory; wait before refreshing its presentation.") from error
        try:
            index = write_results_index(outdir)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    print(f"Results dashboard: {index}\nScientific output files were preserved.")
    return 0


def add_results_command(subparsers) -> None:
    parser = subparsers.add_parser("results", help="Refresh the browsable index of an existing native result directory")
    parser.add_argument("--outdir", required=True, help="existing results directory; scientific files are left in place")
    parser.set_defaults(func=command_results)
