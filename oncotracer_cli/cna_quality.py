"""Resolution-specific CNA evidence checks and explicit unavailable results.

Coverage QC is independent of tumor/control labels. Missing evidence must never
become a neutral profile, a deep deletion, or a diagnostic classification.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
from pathlib import Path
import uuid

from .runtime import OncoTracerError, atomic_write_json, atomic_write_text

POLICY = "coverage-v2"
REASONS = {
    "low_coverage_at_selected_bin_size": "Too few reads per usable bin at the selected resolution",
    "excess_empty_bins_at_selected_bin_size": "Too many usable bins have no reads",
    "insufficient_usable_bins": "Too few usable bins for CNA assessment",
    "non_finite_normalized_bins": "Too many bins have invalid normalized values",
    "normalized_copy_number_median_non_positive": "No positive normalized copy-number baseline",
    "no_usable_finite_normalized_copy_number_median": "No usable normalized copy-number baseline",
    "requires_coverage_recheck": "Resume the CNA analysis to apply the current coverage checks",
}


def _stable_text(path, text):
    if not path.is_file() or path.read_text() != text:
        atomic_write_text(path, text)


def publish_assessment(outdir: Path, status_path: Path, binsize: int):
    if not status_path.is_file():
        return None
    source = json.loads(status_path.read_text())
    records = source.get("samples", [])
    if not records:
        return None
    rows = []
    for record in records:
        current = record.get("qc_policy") == POLICY
        valid = current and record.get("assessment") == "assessable" and record.get("status") == "complete"
        reason = record.get("error") if current else "requires_coverage_recheck"
        rows.append({"sample": record["sample"], "assessment": "assessable" if valid else "not_assessable",
                     "reason": "" if valid else REASONS.get(reason, reason or "CNA processing did not complete"),
                     "bin_size_kb": binsize, "median_reads_per_bin": record.get("median_reads_per_bin"),
                     "zero_read_bin_fraction": record.get("zero_read_bin_fraction")})
    data = {"schema": "oncotracer-cna-assessment-v1", "qc_policy": POLICY,
            "thresholds": {"minimum_usable_bins": 100, "minimum_median_reads_per_bin": 20,
                           "maximum_zero_read_bin_fraction": 0.05, "minimum_finite_normalized_fraction": 0.99},
            "samples": rows}
    directory = outdir / "06_workflow_summary"
    directory.mkdir(parents=True, exist_ok=True)
    _stable_text(directory / "cna_assessment.json", json.dumps(data, sort_keys=True, indent=2) + "\n")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), delimiter="\t")
    writer.writeheader(); writer.writerows(rows)
    _stable_text(directory / "cna_assessment.tsv", buffer.getvalue())
    _stable_text(directory / "cna_assessment.html", '<!doctype html><meta charset="utf-8"><title>CNA assessability</title>'
                 '<style>body{font:16px sans-serif;margin:2em}td,th{padding:.6em;border-bottom:1px solid #ddd;text-align:left}</style>'
                 + assessment_html(data))
    return data


def assessment_html(data):
    if not data or not data.get("samples"):
        return ""
    def cell(value):
        return html.escape(str(value if value is not None else "unavailable"))
    rows = "".join('<tr>' + ''.join('<td>' + cell(value) + '</td>' for value in (
        row['sample'], 'Assessable' if row['assessment'] == 'assessable' else 'NOT ASSESSABLE',
        row.get('bin_size_kb'), row.get('median_reads_per_bin'),
        ('%.2f%%' % (100 * row['zero_read_bin_fraction'])) if row.get('zero_read_bin_fraction') is not None else None,
        row.get('reason', ''))) + '</tr>' for row in data['samples'])
    return ('<section class="card"><h2>CNA coverage and assessability</h2>'
            '<p>Not assessable means insufficient reliable evidence at the selected bin size. '
            'It is neither a normal/flat profile nor a deep loss. Such samples are excluded from CNA '
            'interpretation, oncoprints, clustering and GISTIC. Other analyses retain their own status.</p>'
            '<p>Consider coarser bins and recheck coverage, or obtain more usable reads. Passing these '
            'conservative research checks does not establish a clinical detection limit.</p>'
            '<p>A separate experimental broad read-depth screen is saved, when raw counts are available, '
            'under <code>06_workflow_summary/experimental_broad_cna/index.html</code>. '
            'It reports uncertain chromosome/arm candidates, never changes the primary assessment, '
            'and does not supply diagnostic classifications.</p>'
            '<div class="scroll"><table><thead><tr><th>Sample</th><th>CNA assessment</th><th>Bin size (kb)</th>'
            '<th>Median reads/bin</th><th>Empty bins</th><th>Reason</th></tr></thead><tbody>' + rows + '</tbody></table></div></section>')


def load_assessment(outdir):
    path = outdir / "06_workflow_summary/cna_assessment.json"
    if path.is_symlink() or path.parent.is_symlink():
        raise OncoTracerError("CNA assessment must be inside the physical results directory")
    return json.loads(path.read_text()) if path.is_file() else None


def summary_fields(data):
    if not data:
        return {}
    rows = data["samples"]
    return {"cna_assessment": "06_workflow_summary/cna_assessment.json",
            "cna_not_assessable_samples": [r["sample"] for r in rows if r["assessment"] != "assessable"],
            "cna_sample_statuses": {r["sample"]: "complete" if r["assessment"] == "assessable" else "not_assessable" for r in rows},
            "reports_sample_statuses": {r["sample"]: "not_assessable" for r in rows if r["assessment"] != "assessable"}}


def invalidate_derived_cna(outdir, status_path, segments):
    """Archive owned derived products when their evidence changes; keep alignments.

    Called only by the engine holding the output lease. No arbitrary paths from
    metadata are followed, and old products cannot remain in report discovery.
    """
    status = json.loads(status_path.read_text())
    # Status refresh times are bookkeeping, not new scientific evidence.
    fields = ("sample", "status", "error", "assessment", "qc_policy", "median_reads_per_bin",
              "zero_read_bin_fraction", "usable_bin_count", "normalized_median")
    scientific_status = [{key: record.get(key) for key in fields} for record in status.get("samples", [])]
    digest = hashlib.sha256(json.dumps(scientific_status, sort_keys=True).encode() + segments.read_bytes()).hexdigest()
    marker = outdir / ".oncotracer-native/cna-evidence.json"
    if marker.is_file() and json.loads(marker.read_text()).get("sha256") == digest:
        return
    archive = outdir / ".oncotracer-native/superseded-cna" / uuid.uuid4().hex
    for name in ("02_bam_refinement", "03_cna_codification", "04_cna_custom_plots", "05_cna_classifier"):
        path = outdir / name
        if path.is_symlink():
            raise OncoTracerError(f"Cannot archive symlinked CNA output: {path}")
        if path.exists():
            archive.mkdir(parents=True, exist_ok=True)
            path.rename(archive / name)
    atomic_write_json(marker, {"qc_policy": POLICY, "sha256": digest})


def eligible_classifier_samples(outdir, selected=""):
    data = load_assessment(outdir)
    if data is None:
        # Legacy QDNAseq reports must be regenerated from checked coverage first.
        for relative in ("01_samurai_ont/qdnaseq/qdnaseq_sample_status.json",
                         "01_samurai_illumina/qdnaseq/qdnaseq_sample_status.json",
                         "01_samurai_ont/results/ichorcna/ichorcna_sample_status.json"):
            status = outdir / relative
            if status.is_file() and json.loads(status.read_text()).get("samples"):
                raise OncoTracerError("CNA coverage has not been checked with the current policy. Resume the analysis before generating CNA interpretations.")
        return selected
    allowed = [r["sample"] for r in data["samples"] if r["assessment"] == "assessable"]
    if selected and selected.lower().strip() not in {"all", "none", "null", "false"}:
        import re
        requested = set(filter(None, re.split(r"[,;\s]+", selected.strip())))
        allowed = [s for s in allowed if s in requested]
    return ','.join(allowed)


def assess_ichor_counts(wig, gc_wig, map_wig):
    """Check autosomal read support in matching GC/mappability reference bins."""
    import gzip
    import math
    import statistics
    def read(path):
        values = {}
        opener = gzip.open if str(path).endswith(".gz") else open
        chrom, position, step = None, 0, 0
        with opener(path, "rt") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith(("track", "#", "browser")):
                    continue
                if line.startswith("fixedStep"):
                    fields = dict(part.split("=", 1) for part in line.split()[1:])
                    chrom = fields["chrom"].removeprefix("chr")
                    position, step = int(fields["start"]), int(fields["step"])
                    continue
                if chrom is None or step <= 0:
                    raise OncoTracerError("Invalid fixedStep read-count or reference WIG")
                value = float(line)
                if chrom in {str(i) for i in range(1, 23)}:
                    values[(chrom, position)] = value
                position += step
        return values
    counts, gc, mapping = read(wig), read(gc_wig), read(map_wig)
    usable = [v for key, v in counts.items() if 0 < gc.get(key, -1) < 1
              and mapping.get(key, -1) >= .75 and math.isfinite(v) and v >= 0]
    median = statistics.median(usable) if usable else None
    empty = sum(v == 0 for v in usable) / len(usable) if usable else None
    reason = ("insufficient_usable_bins" if len(usable) < 100 else
              "low_coverage_at_selected_bin_size" if median < 20 else
              "excess_empty_bins_at_selected_bin_size" if empty > .05 else None)
    return {"qc_policy": POLICY, "assessment": "not_assessable" if reason else "assessable",
            "coverage_error": reason, "median_reads_per_bin": median, "zero_read_bin_fraction": empty,
            "usable_bin_count": len(usable)}
